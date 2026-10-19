/**
 * File-scoped mock of node:child_process for voice-control tests.
 *
 * voice-control.ts imports { spawn, execSync } at module level, so the mock
 * must be installed with bun's mock.module() BEFORE the extension module is
 * dynamically imported. The emulator recognizes every command shape
 * voice-control.ts issues (kill -0/kill, sleep, python -c module probes,
 * VAD list-devices, pyaudio output listings, pactl get-default-{sink,source})
 * and throws for anything else, mirroring a missing binary.
 *
 * Scriptable outputs are queued per prefix; tests pre-seed device listings
 * and pactl responses. spawn() records the full args/options and can be
 * configured to fake daemon startup by creating the pid/socket files the
 * wait loop checks for.
 */

export interface SpawnRecord {
  cmd: string;
  args: string[];
  opts: Record<string, any>;
}

export interface ChildProcessMock {
  spawnCalls: SpawnRecord[];
  execCalls: { cmd: string; opts?: Record<string, any> }[];
  killCalls: string[];
  /** Make the next spawn of this command appear to succeed by running onScripted side effects. */
  daemonStartFiles?: { pidFile: string; socketFile: string; deviceEnvFile?: string };
  /** Queue responses for execSync by command prefix (matched in FIFO order). */
  script: (prefix: string, output: string) => void;
  reset: () => void;
}

let spawnCalls: SpawnRecord[] = [];
let execCalls: { cmd: string; opts?: Record<string, any> }[] = [];
let killCalls: string[] = [];
let scripted: { prefix: string; output: string }[] = [];
let daemonStartFiles: { pidFile: string; socketFile: string; deviceEnvFile?: string } | undefined;
import { writeFileSync } from "node:fs";
import { mkdirSync } from "node:fs";

function emulator(cmd: string, opts?: Record<string, any>): string {
  execCalls.push({ cmd, opts });
  const trimmed = cmd.trim();

  // Record kill operations. "kill -0 <pid>" actually probes the real pid so
  // stale-pid branches stay exercisable; plain "kill" is only recorded.
  if (trimmed.startsWith("kill -0 ")) {
    const pid = parseInt(trimmed.slice("kill -0 ".length).trim(), 10);
    if (Number.isFinite(pid)) process.kill(pid, 0);
    return "";
  }
  if (trimmed.startsWith("kill ")) {
    killCalls.push(trimmed);
    return "";
  }

  // sleep is a no-op so daemon wait loops spin instantly.
  if (trimmed.startsWith("sleep")) {
    return "";
  }

  // Scripted outputs win (matched by prefix, oldest queued first).
  for (let i = 0; i < scripted.length; i++) {
    if (trimmed.includes(scripted[i].prefix)) {
      const [entry] = scripted.splice(i, 1);
      return entry.output;
    }
  }

  // Unscripted python probes and external tools behave like missing binaries.
  const err = new Error(`Command failed: ${trimmed}`) as Error & { status: number };
  err.status = 127;
  throw err;
}

export function installChildProcessMock(): ChildProcessMock {
  const bunMock = require("bun:test") as { mock: { module: (id: string, fn: () => any) => void } };
  bunMock.mock.module("node:child_process", () => ({
    execSync: (cmd: string, opts?: Record<string, any>) => emulator(cmd, opts),
    spawn: (cmd: string, args: string[], opts: Record<string, any> = {}) => {
      spawnCalls.push({ cmd, args, opts });
      if (daemonStartFiles) {
        mkdirSync("/tmp", { recursive: true });
        try {
          writeFileSync(daemonStartFiles.pidFile, String(process.pid));
          writeFileSync(daemonStartFiles.socketFile, "");
        } catch {}
      }
      return { unref() {}, pid: 42424 };
    },
    default: undefined,
  }));

  return {
    get spawnCalls() {
      return spawnCalls;
    },
    get execCalls() {
      return execCalls;
    },
    get killCalls() {
      return killCalls;
    },
    get daemonStartFiles() {
      return daemonStartFiles;
    },
    set daemonStartFiles(v: typeof daemonStartFiles) {
      daemonStartFiles = v;
    },
    script: (prefix: string, output: string) => {
      scripted.push({ prefix, output });
    },
    reset: () => {
      spawnCalls = [];
      execCalls = [];
      killCalls = [];
      scripted = [];
      daemonStartFiles = undefined;
    },
  };
}