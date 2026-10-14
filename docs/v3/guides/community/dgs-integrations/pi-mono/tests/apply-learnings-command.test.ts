import { describe, it, expect, beforeEach, afterEach, mock, beforeAll } from "bun:test";
import { MockFetch } from "./helpers/mock-fetch";
import { createMockPi } from "./helpers/mock-pi";
import { importFresh, APPLY_LEARNINGS_SRC } from "./helpers/env";

/**
 * Black-box tests for the /honcho-apply-learnings command.
 *
 * The command talks to hardcoded absolute paths (/home/dsidlo/.pi/agent/settings.json,
 * the agents dir, /tmp log, local synthesis docs) and to git via child_process.exec.
 * Both dependencies are mocked with a virtualized filesystem that only virtualizes those
 * hardcoded roots and falls through to the real fs for everything else (so later test
 * files using real temp files are unaffected), and a callback-style exec stub controlled
 * per-scenario.
 */

const realFs = await import("node:fs/promises");
const realCp = await import("node:child_process");

// bun's mock.module MUTATES the namespace it replaces, so calling realFs.readFile after
// mocking "node:fs/promises" would recurse into our own mock. Capture bound refs before.
const realReadFile = realFs.readFile.bind(realFs);
const realWriteFile = realFs.writeFile.bind(realFs);
const realAppendFile = realFs.appendFile.bind(realFs);
const realAccess = realFs.access.bind(realFs);

const AGENTS_DIR = "/home/dsidlo/.pi/agent/agents";
const SETTINGS_PATH = "/home/dsidlo/.pi/agent/settings.json";
const LOG_FILE = "/tmp/honcho-apply-learnings.log";
const LOCAL_FILES = [
  "/home/dsidlo/.local/lib/honcho/docs/v3/guides/community/dgs-integrations/synthesis-report-2026-03-28.md",
  "/home/dsidlo/.local/lib/honcho/docs/v3/guides/community/dgs-integrations/Honcho-Dream-Analysis.md",
];
const VIRTUALIZED_ROOTS = [AGENTS_DIR, SETTINGS_PATH, LOG_FILE, ...LOCAL_FILES];

function isVirtual(p: string): boolean {
  return VIRTUALIZED_ROOTS.some(
    (root) => p === root || p.startsWith(root.endsWith("/") ? root : `${root}/`),
  );
}

const files = new Map<string, string>();
const logLines: string[] = [];
let initLog = "";
const execBehavior = { error: null as Error | null, stdout: "test-branch" };

function errEnoent(p: string) {
  return Object.assign(new Error(`ENOENT (virtual fs): ${p}`), { code: "ENOENT" });
}

mock.module("node:fs/promises", () => ({
  ...realFs,
  readFile: async (p: any, enc?: any) => {
    const s = String(p);
    if (isVirtual(s)) {
      if (files.has(s)) return files.get(s);
      throw errEnoent(s);
    }
    return realReadFile(p as any, enc as any);
  },
  writeFile: async (p: any, data: any) => {
    const s = String(p);
    if (isVirtual(s)) {
      if (s === LOG_FILE) {
        initLog = String(data);
        return;
      }
      files.set(s, String(data));
      return;
    }
    return realWriteFile(p as any, data as any);
  },
  appendFile: async (p: any, data: any) => {
    const s = String(p);
    if (s === LOG_FILE) {
      logLines.push(String(data));
      return;
    }
    if (isVirtual(s)) {
      if (files.has(s)) {
        files.set(s, files.get(s) + String(data));
        return;
      }
      throw errEnoent(s);
    }
    return realAppendFile(p as any, data as any);
  },
  access: async (p: any) => {
    const s = String(p);
    if (isVirtual(s)) {
      if (files.has(s) || s === LOG_FILE) return;
      throw errEnoent(s);
    }
    return realAccess(p as any);
  },
}));

mock.module("node:child_process", () => ({
  ...realCp,
  exec: (cmd: any, opts: any, cb?: any) => {
    if (typeof opts === "function" && cb === undefined) cb = opts;
    queueMicrotask(() => {
      if (execBehavior.error) cb(execBehavior.error);
      else cb(null, { stdout: execBehavior.stdout, stderr: "" });
    });
  },
}));

/* ------------------------------------------------------------------ */

const AGENT_TEMPLATE = (name: string) =>
  `Name: Teams ${name} Agent\nLast-Updated: 2026-03-01 10:00\n---\n\n# Team ${name} Agent\n\n## Existing Knowledge\n- prior knowledge line\n`;

const DEFAULT_SETTINGS = JSON.stringify({
  honcho: {
    applyLearnings: {
      enabled: true,
      agents: ["teams-manager", "teams-architect", "teams-developer", "teams-reviewer"],
      autoApply: false,
    },
  },
});

let mf: MockFetch;
const apiBehavior: { ok: boolean; status: number; body: any } = { ok: true, status: 200, body: null };

/** Import the command once (it holds no mutable module state) and register it on a mock pi. */
let mod: any;
beforeAll(async () => {
  mod = await importFresh(APPLY_LEARNINGS_SRC);
});

function boot() {
  const app = createMockPi();
  mod.default(app.pi);
  return app;
}

function seedDefaults() {
  files.set(SETTINGS_PATH, DEFAULT_SETTINGS);
  for (const a of ["teams-manager", "teams-architect", "teams-developer", "teams-reviewer"]) {
    files.set(`${AGENTS_DIR}/${a}.md`, AGENT_TEMPLATE(camel(a)));
  }
}

function camel(agent: string) {
  return agent.replace("teams-", "").replace(/^\w/, (c) => c.toUpperCase()).replace(/-(\w)/g, (_m, c) => c.toUpperCase());
}

const API_LEARNINGS = [
  { content: "learning-one", metadata: { created_at: "2026-03-05" } },
  { content: "learning-two", metadata: { created_at: "2026-03-06" } },
];

beforeEach(() => {
  files.clear();
  logLines.length = 0;
  initLog = "";
  execBehavior.error = null;
  execBehavior.stdout = "test-branch";
  apiBehavior.ok = true;
  apiBehavior.status = 200;
  apiBehavior.body = null;

  mf = new MockFetch().install();
  mf.on(
    (c) => c.method === "POST" && c.url.endsWith("/v3/workspaces"),
    () => MockFetch.json({ id: "default" }),
  );
  mf.on(
    (c) => c.url.endsWith("/conclusions/query"),
    () => (apiBehavior.ok ? MockFetch.json(apiBehavior.body ?? API_LEARNINGS) : new Response("boom", { status: apiBehavior.status })),
  );
});

afterEach(() => {
  mf.restore();
});

/* ------------------------------------------------------------------ */

describe("honcho-apply-learnings command: configuration", () => {
  it("refuses to run when disabled in settings.json", async () => {
    files.set(
      SETTINGS_PATH,
      JSON.stringify({ honcho: { applyLearnings: { enabled: false, agents: ["teams-manager"] } } }),
    );
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "");

    expect(result).toEqual({ success: false, reason: "disabled" });
    // the command always notifies "Starting..." first, then the reason
    expect(app.notifications[0]).toEqual({ message: "Starting apply-learnings...", level: "info" });
    expect(app.notifications.at(-1)).toEqual({
      message: "Apply learnings disabled in settings.json",
      level: "warning",
    });
  });

  it("treats settings without a honcho key as disabled", async () => {
    files.set(SETTINGS_PATH, JSON.stringify({ something: "else" }));
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "");
    expect(result.reason).toBe("disabled");
  });

  it("falls back to the built-in four-agent config when settings are unreadable", async () => {
    files.set(SETTINGS_PATH, "{ this is not json");
    seedDefaults(); // seed agent files
    files.delete(SETTINGS_PATH);
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "all");

    expect(result.updated).toBe(4);
    // git branch stub flowed through the fallback path
    expect(logLines.join("")).toContain("Using fallback config");
    expect(logLines.join("")).toContain("Using ALL agents");
  });

  it("rejects invalid agent arguments", async () => {
    seedDefaults();
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "teams-nonexistent");
    expect(result).toEqual({ success: false, reason: "invalid_agent" });
    expect(app.notifications[0].message).toBe("Starting apply-learnings...");
    expect(app.notifications.at(-1).message).toBe("Agent 'teams-nonexistent' not configured");
  });
});

describe("honcho-apply-learnings command: success paths", () => {
  it("applies learnings to all four agents with tailored focus suffixes", async () => {
    seedDefaults();
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "all");

    expect(result.success).toBe(true);
    expect(result).toEqual({ success: true, updated: 4, skipped: 0, failed: 0 });

    const manager = files.get(`${AGENTS_DIR}/teams-manager.md`)!;
    expect(manager).toContain("## Recent Synthesis Insights (Post 2026-03-28)");
    expect(manager).toContain("learning-one\n---\nlearning-two");
    expect(manager).toContain("- Manager Focus");
    expect(manager).not.toContain("- Reviewer Focus");
    expect(manager.match(/Last-Updated:/g)!.length).toBe(1);
    expect(manager).toMatch(/Last-Updated: \d{4}-\d{2}-\d{2} \d{2}:\d{2}\n/);
    // old resolution history is preserved, with the new timestamp appended
    expect(manager).toMatch(/### Resolution\n- Updated: 2026-03-01 10:00\n--- -> \d{4}-\d{2}-\d{2} \d{2}:\d{2}/);
    expect(manager).toContain("## Existing Knowledge"); // original body preserved

    expect(files.get(`${AGENTS_DIR}/teams-architect.md`)).toContain("- Architect Focus");
    expect(files.get(`${AGENTS_DIR}/teams-architect.md`)).not.toContain("- Manager Focus");
    expect(files.get(`${AGENTS_DIR}/teams-developer.md`)).toContain("- Developer Focus");
    expect(files.get(`${AGENTS_DIR}/teams-reviewer.md`)).toContain("- Reviewer Focus");

    const finalNote = app.notifications.at(-1)!;
    expect(finalNote.message).toBe("Updated 4/4 agents.");
    expect(finalNote.level).toBe("info");
    expect(logLines.join("")).toContain("=== COMMAND COMPLETE ===");
    expect(initLog).toContain("=== Starting /honcho-apply-learnings");
  });

  it("runs a single agent when passed as argument", async () => {
    seedDefaults();
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "teams-developer");

    expect(result.updated).toBe(1);
    expect(files.get(`${AGENTS_DIR}/teams-developer.md`)!).toContain("- Developer Focus");
    expect(files.get(`${AGENTS_DIR}/teams-manager.md`)).toBe(AGENT_TEMPLATE("Manager"));
    expect(app.notifications.at(-1)!.message).toContain("Updated 1/1 agents.");
  });

  it("reads learnings from local synthesis files when the API returns a non-array", async () => {
    seedDefaults();
    apiBehavior.body = { items: [{ content: "wrapped" }] }; // not an array -> learnings stay empty
    for (const local of LOCAL_FILES) files.set(local, "local synthesis insight text");
    files.delete(LOCAL_FILES[0]);
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "teams-manager");

    expect(result.updated).toBe(1);
    const manager = files.get(`${AGENTS_DIR}/teams-manager.md`)!;
    expect(manager).toContain("local synthesis insight text");
    expect(manager).not.toContain("wrapped"); // non-array payload never parsed as learnings
    expect(logLines.join("")).toContain("Trying local files");
    expect(logLines.join("")).toContain("Loaded local file");
  });
});

describe("honcho-apply-learnings command: skip and failure paths", () => {
  it("skips agents that already have the insights section", async () => {
    seedDefaults();
    files.set(
      `${AGENTS_DIR}/teams-manager.md`,
      AGENT_TEMPLATE("Manager") + "\n## Recent Synthesis Insights (Post 2026-03-28)",
    );
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "teams-manager");

    expect(result).toEqual({ success: true, updated: 0, skipped: 1, failed: 0 });
    expect(app.notifications.at(-1)!.message).toBe(
      "No files updated - all 1 agent(s) already have synthesis insights.",
    );
    expect(files.get(`${AGENTS_DIR}/teams-manager.md`)).toBe(
      AGENT_TEMPLATE("Manager") + "\n## Recent Synthesis Insights (Post 2026-03-28)",
    );
  });

  it("reports mixed updated/skipped states", async () => {
    seedDefaults();
    files.set(`${AGENTS_DIR}/teams-manager.md`, AGENT_TEMPLATE("Manager") + "\n## Recent Synthesis Insights (Post 2026-03-28)");
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "all");

    expect(result.updated).toBe(3);
    expect(result.skipped).toBe(1);
    expect(app.notifications.at(-1)!.message).toBe("Updated 3/4 agents. Skipped 1 (already present).");
  });

  it("records missing agent files as failures", async () => {
    seedDefaults();
    files.delete(`${AGENTS_DIR}/teams-manager.md`);
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "all");

    expect(result.success).toBe(true);
    expect(result.updated).toBe(3);
    expect(result.failed).toBe(1);
    expect(app.notifications.at(-1)!.message).toContain(
      "Updated 3/4 agents. Failed: teams-manager: not found.",
    );
    expect(app.notifications.at(-1)!.level).toBe("warning");
  });

  it("reports no_learnings when API fails and no local files exist", async () => {
    seedDefaults();
    apiBehavior.ok = false;
    apiBehavior.status = 500;
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "all");

    expect(result).toEqual({ success: false, reason: "no_learnings" });
    expect(app.notifications[0].message).toBe("Starting apply-learnings...");
    expect(app.notifications.at(-1).message).toBe("No learnings available");
    expect(logLines.join("")).toContain("Local file failed:");
  });
});

describe("honcho-apply-learnings command: git branch", () => {
  it("captures the mock branch output in the log", async () => {
    seedDefaults();
    execBehavior.stdout = "feature/crazy-branch\n";
    const app = boot();
    await app.runCommand("honcho-apply-learnings", "teams-reviewer");
    expect(logLines.join("")).toContain("Branch: feature/crazy-branch");
  });

  it("survives git failures and logs the fallback", async () => {
    seedDefaults();
    execBehavior.error = new Error("git unavailable");
    const app = boot();
    const result = await app.runCommand("honcho-apply-learnings", "teams-reviewer");
    expect(result.updated).toBe(1);
    expect(result.success).toBe(true);
    expect(logLines.join("")).toContain("Git branch failed:");
  });
});