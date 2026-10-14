import { join } from "node:path";

/**
 * Shared environment / fresh-import helpers.
 *
 * honcho.ts reads HONCHO_* env vars and honcho.json config files at import time and keeps
 * mutable module state (message queue, session id, observation overlay). Tests therefore
 * (a) set env BEFORE importing and (b) import a fresh module instance per test by appending
 * a unique query string to the file path (Bun re-evaluates a module when the query differs).
 */

// import.meta.dir = <pi-mono>/tests/helpers → repo root is two levels up
const AGENT_DIR = join(import.meta.dir, "..", "..", "agent");

export const HONCHO_SRC = join(AGENT_DIR, "extensions", "honcho.ts");
export const SCRUB_SRC = join(AGENT_DIR, "extensions", "secret-scrub.ts");
export const APPLY_LEARNINGS_SRC = join(
  AGENT_DIR,
  "extensions",
  "honcho-apply-learnings-command.ts",
);
export const UPLOAD_SRC = join(AGENT_DIR, "honcho-upload-document", "upload.ts");

export const HONCHO_ENV_KEYS = [
  "HONCHO_BASE_URL",
  "HONCHO_USER",
  "HONCHO_AGENT_ID",
  "HONCHO_PEER_ID",
  "HONCHO_WORKSPACE",
  "HONCHO_WORKSPACE_MODE",
] as const;

export const BASE_ENV: Record<string, string> = {
  HONCHO_BASE_URL: "http://honcho.test",
  HONCHO_USER: "user-t",
  HONCHO_AGENT_ID: "agent-t",
  HONCHO_PEER_ID: "agent-t",
  HONCHO_WORKSPACE: "ws-test",
  HONCHO_WORKSPACE_MODE: "static",
};

export function clearHonchoEnv() {
  for (const k of HONCHO_ENV_KEYS) delete process.env[k];
}

/** Apply BASE_ENV, optional overrides, and optional key deletions. */
export function applyEnv(
  set?: Record<string, string | undefined>,
  del: string[] = [],
) {
  clearHonchoEnv();
  for (const k of del) delete process.env[k];
  if (set) {
    for (const [k, v] of Object.entries(set)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

// Per-file unique prefix so identical query strings never collide across test files
// (module cache is keyed by the full resolved specifier including the query).
const FILE_PREFIX = Math.random().toString(36).slice(2, 10);
let freshCounter = 0;

/** Import a path with a unique query string so Bun returns a fresh module instance. */
export async function importFresh<T = any>(path: string): Promise<T> {
  const sep = path.includes("?") ? "&" : "?";
  freshCounter += 1;
  return (await import(`${path}${sep}fresh=${FILE_PREFIX}-${freshCounter}`)) as T;
}

/** Let setTimeout(...,0) flushes scheduled by the extension run to completion. */
export async function drain(ms = 30): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}