/**
 * Mock of the pi ExtensionAPI surface used by honcho.ts and honcho-apply-learnings-command.ts.
 * Records registered hooks/tools/commands and ui.notify calls, and can fire hooks with a ctx.
 */

export interface Notification {
  message: string;
  level?: string;
  timeout?: number;
}

export interface MockPi {
  handlers: Map<string, (event: any, ctx: any) => Promise<any>>;
  tools: Map<string, any>;
  commands: Map<string, { description: string; handler: (args: string, ctx: any) => Promise<any> }>;
  notifications: Notification[];
  ui: { notify: (message: string, level?: string, timeout?: number) => void };
  makeCtx: (overrides?: Record<string, any>) => any;
  callHook: (name: string, event: any, ctxOverrides?: Record<string, any>) => Promise<any>;
  executeTool: (name: string, params: any) => Promise<any>;
  runCommand: (name: string, args: string, ctxOverrides?: Record<string, any>) => Promise<any>;
}

export const DEFAULT_CTX_MODEL = { provider: "test-provider", id: "test-model" };

export function createMockPi(): MockPi & { pi: any } {
  const handlers = new Map<string, any>();
  const tools = new Map<string, any>();
  const commands = new Map<string, any>();
  const notifications: Notification[] = [];

  const ui = {
    notify(message: string, level?: string, timeout?: number) {
      notifications.push({ message, level, timeout });
    },
  };

  const pi = {
    on(name: string, fn: unknown) {
      if (handlers.has(name)) throw new Error(`duplicate handler registered: ${name}`);
      handlers.set(name, fn as any);
    },
    registerTool(def: { name: string }) {
      tools.set(def.name, def);
    },
    registerCommand(name: string, def: unknown) {
      commands.set(name, def as any);
    },
  };

  const makeCtx = (overrides: Record<string, any> = {}) => ({
    cwd: process.cwd(),
    model: DEFAULT_CTX_MODEL,
    ui,
    ...overrides,
  });

  /**
   * Apply TypeBox-style schema defaults (pi resolves these before calling execute).
   * Works with the minimal @sinclair/typebox stub shape: {type:"object", properties:{...}}.
   */
  const resolveDefaults = (schema: any, params: any): any => {
    if (!schema || schema.type !== "object" || !schema.properties) return params;
    const resolved = { ...params };
    for (const [key, prop] of Object.entries<any>(schema.properties)) {
      if (resolved[key] === undefined && prop?.default !== undefined) resolved[key] = prop.default;
      if (resolved[key] !== undefined && prop?.type === "object") {
        resolved[key] = resolveDefaults(prop, resolved[key]);
      }
    }
    return resolved;
  };

  const callHook = async (name: string, event: any, ctxOverrides: Record<string, any> = {}) => {
    const fn = handlers.get(name);
    if (!fn) throw new Error(`no handler for ${name}`);
    return fn(event, makeCtx(ctxOverrides));
  };

  const executeTool = async (name: string, params: any) => {
    const tool = tools.get(name);
    if (!tool) throw new Error(`tool not registered: ${name}`);
    const resolved = resolveDefaults(tool.parameters, params);
    const result = await tool.execute(`toolcall-${name}`, resolved);
    // Flatten the standard tool result shape for convenient assertions.
    const text = result?.content
      ?.filter((c: any) => c.type === "text")
      .map((c: any) => c.text)
      .join("\n");
    return { ...result, text };
  };

  const runCommand = async (name: string, args: string, ctxOverrides: Record<string, any> = {}) => {
    const cmd = commands.get(name);
    if (!cmd) throw new Error(`command not registered: ${name}`);
    return cmd.handler(args, makeCtx(ctxOverrides));
  };

  return { pi, handlers, tools, commands, notifications, ui, makeCtx, callHook, executeTool, runCommand };
}