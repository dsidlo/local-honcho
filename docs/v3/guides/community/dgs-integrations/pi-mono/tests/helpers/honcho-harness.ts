import { applyEnv, importFresh, drain } from "./env";
import { HONCHO_SRC } from "./env";
import { createMockPi, MockPi } from "./mock-pi";
import { MockFetch } from "./mock-fetch";

export interface BootstrapOptions {
  /** Environment overrides applied on top of BASE_ENV (undefined value deletes the key). */
  env?: Record<string, string | undefined>;
  /** When true, apply exactly opts.env (all BASE keys deleted first) — for config tests. */
  envExact?: boolean;
  /** Extra env keys to delete after HONCHO_* clearing (e.g. HOME during config tests). */
  envDelete?: string[];
  /** Do not install the standard honcho fetch routes. */
  noDefaults?: boolean;
  /** Options forwarded to MockFetch.addHonchoDefaults. */
  defaults?: Parameters<MockFetch["addHonchoDefaults"]>[0];
}

export interface Bootstrapped {
  mod: any;
  mf: MockFetch;
  app: MockPi;
  baseEnv: typeof BASE_ENV_LOCAL;
  callHook: (name: string, event: any, ctxOverrides?: Record<string, any>) => Promise<any>;
  executeTool: (name: string, params: any) => Promise<any>;
  runCommand: (name: string, args: string, ctxOverrides?: Record<string, any>) => Promise<any>;
  drain: typeof drain;
}

const BASE_ENV_LOCAL = {
  HONCHO_BASE_URL: "http://honcho.test",
  HONCHO_USER: "user-t",
  HONCHO_AGENT_ID: "agent-t",
  HONCHO_PEER_ID: "agent-t",
  HONCHO_WORKSPACE: "ws-test",
  HONCHO_WORKSPACE_MODE: "static",
};

/**
 * Import a fresh honcho.ts instance with the given env, wire it to a mock pi API,
 * and install default mock fetch routes. Returns everything a scenario needs.
 */
export async function bootstrap(opts: BootstrapOptions = {}): Promise<Bootstrapped> {
  // Exact env wins over the BASE defaults; otherwise BASE_ENV is the floor.
  applyEnv(opts.envExact ? (opts.env ?? {}) : { ...BASE_ENV_LOCAL, ...(opts.env ?? {}) }, opts.envDelete);
  const mf = new MockFetch().install();
  if (!opts.noDefaults) mf.addHonchoDefaults(opts.defaults);
  const mod = await importFresh(HONCHO_SRC);
  const app = createMockPi();
  mod.default(app.pi);
  return {
    mod,
    mf,
    app,
    baseEnv: BASE_ENV_LOCAL,
    callHook: app.callHook,
    executeTool: app.executeTool,
    runCommand: app.runCommand,
    drain,
  };
}

export { BASE_ENV_LOCAL as BASE_ENV };