import { describe, it, expect, afterEach, afterAll, beforeAll, mock } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
// capture the real module BEFORE any mock so the shim can never re-enter the mocked registry
const realOS = require("node:os");
const realHomedir = realOS.homedir.bind(realOS);
import { join } from "node:path";


/**
 * Import-time configuration coverage for honcho.ts.
 *
 * honcho.ts resolves honcho.json (cwd, ~/.pi/, ~/.config/pi/, ~/.honcho.json) synchronously at
 * import and merges env vars on top. Each test chdirs into a sandbox and/or overrides HOME,
 * imports a fresh module, and reads the resolved values through the honcho-status command
 * (base URL + workspace) and a flushed session create call (user/agent peers).
 */

const ORIG_CWD = process.cwd();

const sandboxes: string[] = [];

function sandboxDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "honcho-cfg-"));
  sandboxes.push(dir);
  return dir;
}

// bun caches os.homedir() at first call, so redirecting HOME via env does not work.
// Instead, shim node:os to read a test-controlled variable (falls back to the real home).
// honcho.ts resolves config paths through node:os homedir() in both its async and sync loaders.
beforeAll(() => {
  mock.module("node:os", () => ({
    ...realOS,
    homedir: () => process.env.HONCHO_TEST_HOME ?? realHomedir(),
  }));
});
afterAll(() => {
  mock.restore();
  // avoid leaking the shim override into later test files
  delete process.env.HONCHO_TEST_HOME;
});

afterEach(() => {
  process.chdir(ORIG_CWD);
});

afterAll(() => {
  for (const dir of sandboxes) {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  }
});

const { bootstrap } = await import("./helpers/honcho-harness");

/** Boot with ONLY the given env (BASE_ENV is wiped); home defaults to its own sandbox. */
async function bootWith(home?: string) {
  return bootstrap({ envExact: true, env: { HONCHO_TEST_HOME: home ?? sandboxDir() } });
}

/* ------------------------------------------------------------------ */

describe("honcho extension: import-time configuration", () => {
  it("falls back to defaults when no env or config file is present", async () => {
    const t = await bootWith();
    await t.runCommand("honcho-status", "");
    expect(t.app.notifications[0].message).toBe(
      "No active session\nAPI: http://localhost:8300\nWorkspace: default",
    );
  });

  it("reads honcho.json from cwd (base_url, user, agent_id, workspace)", async () => {
    const root = sandboxDir();
    writeFileSync(
      join(root, "honcho.json"),
      JSON.stringify({
        honcho: {
          base_url: "http://file.test",
          user: "file-user",
          agent_id: "file-agent",
          workspace: "file-ws",
          workspace_mode: "static",
        },
      }),
    );
    process.chdir(root);
    const t = await bootWith();

    await t.runCommand("honcho-status", "");
    const note = t.app.notifications[0].message;
    expect(note).toContain("API: http://file.test");
    expect(note).toContain("Workspace: file-ws");

    await t.callHook("turn_start", { turnIndex: 0 });
    await t.runCommand("honcho-flush", "");
    const create = t.mf.calls.find((c: any) => /\/sessions$/.test(c.url))!;
    expect(create.url).toBe("http://file.test/v3/workspaces/file-ws/sessions");
    expect(Object.keys(create.body.peers).sort()).toEqual(["file-agent", "file-user"]);
  });

  it("uses port from config when no base_url is set", async () => {
    const root = sandboxDir();
    writeFileSync(join(root, "honcho.json"), JSON.stringify({ honcho: { port: 9099 } }));
    process.chdir(root);
    const t = await bootWith();
    await t.runCommand("honcho-status", "");
    expect(t.app.notifications[0].message).toContain("API: http://localhost:9099");
  });

  it("prefers base_url over port when both are configured", async () => {
    const root = sandboxDir();
    writeFileSync(
      join(root, "honcho.json"),
      JSON.stringify({ honcho: { port: 9099, base_url: "http://win.test" } }),
    );
    process.chdir(root);
    const t = await bootWith();
    await t.runCommand("honcho-status", "");
    expect(t.app.notifications[0].message).toContain("API: http://win.test");
  });

  it("env vars take precedence over the config file", async () => {
    const root = sandboxDir();
    writeFileSync(
      join(root, "honcho.json"),
      JSON.stringify({ honcho: { base_url: "http://file.test", user: "file-user", agent_id: "file-agent" } }),
    );
    process.chdir(root);
    const t = await bootstrap({
      envExact: true,
      env: { HONCHO_TEST_HOME: sandboxDir(), HONCHO_BASE_URL: "http://env.test", HONCHO_USER: "env-user" },
    });

    await t.runCommand("honcho-status", "");
    expect(t.app.notifications[0].message).toContain("API: http://env.test");

    await t.callHook("turn_start", { turnIndex: 0 });
    await t.runCommand("honcho-flush", "");
    const create = t.mf.calls.find((c: any) => /\/sessions$/.test(c.url))!;
    const peers = Object.keys(create.body.peers).sort();
    expect(peers).toContain("env-user");
    expect(peers).not.toContain("file-user");
    // env.agent_id unset -> file agent_id survives (file beats the hardcoded default)
    expect(peers).toEqual(["env-user", "file-agent"]);
  });

  it("HONCHO_PEER_ID env var overrides the config file agent_id", async () => {
    const root = sandboxDir();
    writeFileSync(
      join(root, "honcho.json"),
      JSON.stringify({ honcho: { user: "file-user", agent_id: "file-agent", workspace: "file-ws", workspace_mode: "static", base_url: "http://file.test" } }),
    );
    process.chdir(root);
    const t = await bootstrap({
      envExact: true,
      env: { HONCHO_TEST_HOME: sandboxDir(), HONCHO_PEER_ID: "env-peer" },
    });

    await t.callHook("turn_start", { turnIndex: 0 });
    await t.runCommand("honcho-flush", "");
    const create = t.mf.calls.find((c: any) => /\/sessions$/.test(c.url))!;
    expect(Object.keys(create.body.peers).sort()).toEqual(["env-peer", "file-user"]);
  });

  it("falls through to ~/.pi/honcho.json when cwd config is malformed", async () => {
    const root = sandboxDir();
    const home = sandboxDir();
    writeFileSync(join(root, "honcho.json"), "{ this is not json");
    mkdirSync(join(home, ".pi"), { recursive: true });
    writeFileSync(
      join(home, ".pi", "honcho.json"),
      JSON.stringify({ honcho: { base_url: "http://home-pi.test", user: "home-user", workspace: "home-ws", workspace_mode: "static" } }),
    );
    process.chdir(root);
    const t = await bootWith(home);

    await t.runCommand("honcho-status", "");
    const note = t.app.notifications[0].message;
    expect(note).toContain("API: http://home-pi.test");
    expect(note).toContain("Workspace: home-ws");
  });

  it("uses ~/.config/pi/honcho.json when ~/.pi/honcho.json is absent", async () => {
    const home = sandboxDir();
    mkdirSync(join(home, ".config", "pi"), { recursive: true });
    writeFileSync(
      join(home, ".config", "pi", "honcho.json"),
      JSON.stringify({ honcho: { base_url: "http://home-config.test", workspace: "fc-ws", workspace_mode: "static" } }),
    );
    const t = await bootWith(home);
    await t.runCommand("honcho-status", "");
    const note = t.app.notifications[0].message;
    expect(note).toContain("API: http://home-config.test");
    expect(note).toContain("Workspace: fc-ws");
  });

  it("prefers ~/.config/pi/honcho.json over ~/.honcho.json", async () => {
    const home = sandboxDir();
    mkdirSync(join(home, ".config", "pi"), { recursive: true });
    writeFileSync(
      join(home, ".config", "pi", "honcho.json"),
      JSON.stringify({ honcho: { base_url: "http://home-config.test" } }),
    );
    writeFileSync(
      join(home, ".honcho.json"),
      JSON.stringify({ honcho: { base_url: "http://home-dot.test" } }),
    );
    const t = await bootWith(home);
    await t.runCommand("honcho-status", "");
    expect(t.app.notifications[0].message).toContain("API: http://home-config.test");
  });

  it("uses ~/.honcho.json as the last config file fallback", async () => {
    const home = sandboxDir();
    writeFileSync(
      join(home, ".honcho.json"),
      JSON.stringify({ honcho: { base_url: "http://home-dot.test", port: 1234 } }),
    );
    const t = await bootWith(home);
    await t.runCommand("honcho-status", "");
    expect(t.app.notifications[0].message).toContain("API: http://home-dot.test");
  });

  it("the cwd config file wins over HOME config files", async () => {
    const root = sandboxDir();
    const home = sandboxDir();
    writeFileSync(join(root, "honcho.json"), JSON.stringify({ honcho: { base_url: "http://cwd.test" } }));
    mkdirSync(join(home, ".pi"), { recursive: true });
    writeFileSync(join(home, ".pi", "honcho.json"), JSON.stringify({ honcho: { base_url: "http://home.test" } }));
    process.chdir(root);
    const t = await bootWith();
    await t.runCommand("honcho-status", "");
    expect(t.app.notifications[0].message).toContain("API: http://cwd.test");
  });
});