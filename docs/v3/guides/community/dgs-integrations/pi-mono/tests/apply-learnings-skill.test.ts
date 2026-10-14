import { describe, it, expect } from "bun:test";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * Tests for honcho-apply-learnings/run.sh, executed as real bash subprocesses with a
 * sandboxed $HOME. The script writes to $HOME/.pi/agent/agents/teams-*.md.
 */

const SCRIPT = join(import.meta.dir, "..", "agent", "honcho-apply-learnings", "run.sh");

const tempDirs: string[] = [];
function sandboxDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "honcho-sh-"));
  tempDirs.push(dir);
  return dir;
}

const AGENTS_DIR = (home: string) => join(home, ".pi", "agent", "agents");
const TEAM_FILES = ["teams-manager.md", "teams-architect.md", "teams-developer.md", "teams-reviewer.md"];

function run(home: string, args: string[] = []) {
  return spawnSync("bash", [SCRIPT, ...args], {
    env: { ...process.env, HOME: home },
    encoding: "utf-8",
    timeout: 20000,
  });
}

function seedFile(home: string, team: string, ts: string, marker?: string) {
  const body = marker
    ? `# Team ${team} Agent\n\nCUSTOM-MARKER-${marker}\nLast-Updated: ${ts}\n- prior knowledge\n`
    : `# Team ${team} Agent\n\nLast-Updated: ${ts}\n- prior knowledge\n`;
  writeFileSync(join(AGENTS_DIR(home), `teams-${team}.md`), body);
}

describe("honcho-apply-learnings run.sh", () => {
  it("passes a bash syntax check", () => {
    const check = spawnSync("bash", ["-n", SCRIPT], { encoding: "utf-8" });
    expect(check.status).toBe(0);
  });

  it("creates all four agent files with defaults and simulated learnings on a fresh HOME", () => {
    const home = sandboxDir();
    mkdirSync(AGENTS_DIR(home), { recursive: true });

    const res = run(home);
    expect(res.status).toBe(0);
    expect(res.stdout).toContain("Oldest last-update: 2026-01-01 00:00");
    expect(res.stdout).toContain("All updates complete");

    for (const team of ["manager", "architect", "developer", "reviewer"]) {
      const file = join(AGENTS_DIR(home), `teams-${team}.md`);
      expect(existsSync(file)).toBe(true);
      const content = readFileSync(file, "utf-8");
      expect(content).toContain("[Initial content]");
      expect(content).toContain("## Recent Synthesis Insights (Post 2026-01-01 00:00)");
      expect(content).toContain("Unified state consolidation");
      expect(content).toContain("### Resolution Notes");
      expect(content).toMatch(/Last-Updated\*\*: 20\d{2}-\d{2}-\d{2} \d{2}:\d{2}/);
    }
  });

  it("picks the oldest timestamp across all files and preserves existing content", () => {
    const home = sandboxDir();
    mkdirSync(AGENTS_DIR(home), { recursive: true });
    seedFile(home, "manager", "2026-03-01 10:00", "M1");
    seedFile(home, "architect", "2026-02-01 10:00", "A1");
    seedFile(home, "developer", "2026-04-01 10:00", "D1");
    seedFile(home, "reviewer", "2026-01-15 10:00", "R1");

    const res = run(home);
    expect(res.status).toBe(0);
    expect(res.stdout).toContain("Oldest last-update: 2026-01-15 10:00");
    expect(res.stdout).toContain("Applied syntheses >2026-01-15 10:00 to all roles");

    const manager = readFileSync(join(AGENTS_DIR(home), "teams-manager.md"), "utf-8");
    expect(manager).toContain("CUSTOM-MARKER-M1");
    expect(manager).toContain("## Recent Synthesis Insights (Post 2026-01-15 10:00)");
  });

  it("honors a role filter and only touches that team file", () => {
    const home = sandboxDir();
    mkdirSync(AGENTS_DIR(home), { recursive: true });
    seedFile(home, "manager", "2026-02-01 10:00", "M1");
    seedFile(home, "architect", "2026-02-01 10:00", "A1");

    const beforeArchitect = readFileSync(join(AGENTS_DIR(home), "teams-architect.md"), "utf-8");
    const beforeManager = readFileSync(join(AGENTS_DIR(home), "teams-manager.md"), "utf-8");

    const res = run(home, ["manager"]);
    expect(res.status).toBe(0);
    expect(res.stdout).toContain("to manager roles");

    const afterArchitect = readFileSync(join(AGENTS_DIR(home), "teams-architect.md"), "utf-8");
    const afterManager = readFileSync(join(AGENTS_DIR(home), "teams-manager.md"), "utf-8");
    expect(afterArchitect).toBe(beforeArchitect);
    expect(afterManager).not.toBe(beforeManager);
    expect(afterManager).toContain("## Recent Synthesis Insights");
    expect(afterArchitect).not.toContain("Recent Synthesis Insights");
  });

  it("fails loudly (set -e) when the agents directory does not exist", () => {
    const home = sandboxDir();
    // note: .pi/agent/agents intentionally NOT created
    const res = run(home);
    expect(res.status).not.toBe(0);
  });
});