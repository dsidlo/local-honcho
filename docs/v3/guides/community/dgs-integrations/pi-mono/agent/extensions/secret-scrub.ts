/**
 * secret-scrub.ts — pi extension that keeps keys, passwords and other credentials out of
 * everything that is sent to the LLM (provider requests).
 *
 * Behavior:
 *  - Scrubbing is ALWAYS ON at session start. The user can turn it OFF mid-session with
 *    `/redact-off` or `/secret-scrub off` and back ON with `/redact-on`; `/redact` (or
 *    `/secret-scrub`) shows status and `/redact test <text>` previews how a line would be
 *    redacted.
 *  - `context` hook (fires before each LLM call): scrubs user prompts, assistant messages,
 *    tool-call arguments and tool-result messages per request. The local transcript keeps
 *    originals; only what pi sends to the provider is redacted.
 *  - `tool_result` hook: redacts tool output content durably (and structuredContent, whose
 *    presence is documented as required when content is replaced), so consumers of the
 *    transcript — compaction summaries, memory extensions — receive redacted values too.
 *  - Tool calls are NOT scrubbed: mutating tool input would break execution of legitimate
 *    tasks (e.g. storing credentials in a file). Turn scrubbing off for that workflow.
 *
 * Detection is regex-based and deliberately protective: it favors redacting on plausible
 * matches over missing secrets. Known false positives can always be bypassed with
 * `/secret-scrub off` for the rest of the session.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const SCRUB_LOG_FILE = "/tmp/pimono-secret-scrub.log";

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

function scrubLog(level: "DEBUG" | "INFO" | "WARN" | "ERROR", msg: string, data?: any): void {
  const ts = new Date().toISOString().slice(0, 19);
  let line = `[${ts}] [${level}] ${msg}`;
  if (data !== undefined) {
    const dataStr = typeof data === "object" ? JSON.stringify(data).slice(0, 500) : String(data);
    line += ` | data=${dataStr}`;
  }
  import("node:fs/promises")
    .then((fs) => fs.appendFile(SCRUB_LOG_FILE, line + "\n"))
    .catch(() => {
      /* logging must never break scrubbing */
    });
}

// ---------------------------------------------------------------------------
// Patterns
// ---------------------------------------------------------------------------
// Each pattern redacts its match with a stable placeholder `[REDACTED:<kind>]` unless it
// provides a custom build() that keeps useful structure (e.g. a URL scheme or key name).
// build() returning null keeps the original text (false-positive guard).

interface Pattern {
  name: string;
  rx: RegExp;
  build?: (whole: string, ...groups: any[]) => string | null;
}

/** Placeholder inserted in place of detected secrets. */
function redact(kind: string): string {
  return `[REDACTED:${kind}]`;
}

/**
 * Guard for the generic key=value patterns: values that look like variable references,
 * template expressions, already-masked output or placeholders are left alone.
 */
function looksLikeReferenceOrPlaceholder(value: string): boolean {
  if (value.length < 6) return true;
  if (/\$\{|\$[A-Za-z_]|%\w|\{|\}|\[|\]|\(|\)/.test(value)) return true; // ${VAR}, tokens[i], f(...)
  if (/^[*.·•_Xx]{4,}$/.test(value)) return true; // already masked: ****, xxxxx
  if (/^(true|false|null|none|undefined|nan|redacted|changeme|change-me|example|placeholder|your[-_].*)$/i.test(value)) {
    return true;
  }
  return false;
}

const PATTERNS: Pattern[] = [
  // --- structured secret formats -------------------------------------------------
  {
    name: "private_key",
    rx: /-----BEGIN [A-Z ]*PRIVATE KEY( BLOCK)?-----[\s\S]{20,}?-----END [A-Z ]*PRIVATE KEY( BLOCK)?-----/g,
  },
  {
    name: "github_pat",
    rx: /\bgithub_pat_[A-Za-z0-9_]{18,255}\b/g,
  },
  {
    name: "github_token",
    rx: /\bgh[pousr]_[A-Za-z0-9]{36,255}\b/g,
  },
  {
    name: "gitlab_token",
    rx: /\bglpat-[A-Za-z0-9_-]{16,255}\b/g,
  },
  {
    name: "anthropic_api_key",
    rx: /\bsk-ant-[A-Za-z0-9_-]{24,255}\b/g,
  },
  {
    name: "openai_api_key",
    rx: /\bsk-(?:proj-)?[A-Za-z0-9_-]{20,255}\b/g,
  },
  {
    name: "google_api_key",
    rx: /\bAIza[0-9A-Za-z_-]{35}\b/g,
  },
  {
    name: "aws_access_key_id",
    rx: /\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b/g,
  },
  {
    name: "aws_secret_access_key",
    rx: /\baws(?:[_-]?secret)?[_-]?access[_-]?[_-]?key["']?\s*[:=]?\s*["']?([A-Za-z0-9/+=]{40})["']?/gi,
  },
  {
    name: "slack_token",
    rx: /\bxox[bparsa]-[A-Za-z0-9-]{10,255}\b/g,
  },
  {
    name: "stripe_secret_key",
    rx: /\b(?:sk|rk)_live_[A-Za-z0-9]{20,255}\b/g,
  },
  {
    name: "npm_token",
    rx: /\bnpm_[A-Za-z0-9]{30,255}\b/g,
  },
  {
    // JWTs: header.payload with optional signature
    name: "jwt",
    rx: /\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?:\.[A-Za-z0-9_-]*)?/g,
  },

  // --- contexts that carry credentials -------------------------------------------
  {
    name: "auth_header",
    rx: /\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,255}/gi,
  },
  {
    // keep scheme + user, redact the password segment
    name: "db_url_password",
    rx:
      /\b((?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|amqps?):\/\/[^:\/\s@]{1,64}):([^@\s\/]{3,255})@/gi,
    build: (whole, schemeAndUser) => `${schemeAndUser}:${redact("db_url_password")}@`,
  },

  // --- named credentials ----------------------------------------------------------
  {
    // UPPER_SNAKE env/config names ending in a credential keyword: MY_APP_TOKEN=abc123
    name: "env_credential",
    rx:
      /\b([A-Z][A-Z0-9_]{2,}?(?:_)?(?:API_?KEY|ACCESS_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE_?KEY|PASSPHRASE|API_?SECRET))\b(\s*[:=]\s*)("[^"\s]{6,255}"|'[^'\s]{6,255}'|[^\s"'`:=,;]{6,255})/g,
    build: (whole, keyName, sep, value) => looksLikeReferenceOrPlaceholder(value) ? null : `${keyName}${sep}${redact("env_credential")}`,
  },
  {
    // generic mixed-case keywords: password: hunter2, api_key = "abcd1234"
    name: "credential_value",
    rx:
      /\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|refresh[_-]?token|client[_-]?secret|secret[_-]?key|private[_-]?key|pass(?:word|wd|phrase)|secret|token)\b(\s*[:=]\s*)("[^"\n]{6,255}"|'[^'\n]{6,255}'|[^\s"'`:=,;]{6,255})/gi,
    build: (whole, keyName, sep, value) => looksLikeReferenceOrPlaceholder(value) ? null : `${keyName}${sep}${redact("credential_value")}`,
  },
];

/** Redact secrets in a text string. Returns the new text plus the pattern names that hit. */
export function scrubText(text: string): { text: string; hits: string[] } {
  const hits: string[] = [];
  let out = text;
  for (const p of PATTERNS) {
    out = out.replace(p.rx, (...args: any[]) => {
      const replacement = p.build ? p.build(...args) : redact(p.name);
      if (replacement === null) return args[0] as string;
      hits.push(p.name);
      return replacement;
    });
  }
  return { text: out, hits };
}

/**
 * Recursively redact secrets inside JSON-ish data (tool arguments, structuredContent,
 * details). Strings are scrubbed; objects and arrays are copied on write.
 */
export function scrubJson(value: unknown, depth: number = 0): { value: unknown; hits: string[] } {
  const hits: string[] = [];
  if (typeof value === "string") {
    const r = scrubText(value);
    hits.push(...r.hits);
    return { value: r.text, hits };
  }
  if (depth > 8 || value === null || typeof value !== "object") {
    return { value, hits };
  }
  if (Array.isArray(value)) {
    let changed = false;
    const out: unknown[] = [];
    for (const item of value) {
      const r = scrubJson(item, depth + 1);
      hits.push(...r.hits);
      if (r.value !== item) changed = true;
      out.push(r.value);
    }
    return { value: changed ? out : value, hits };
  }
  let changed = false;
  const out: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    const r = scrubJson(item, depth + 1);
    hits.push(...r.hits);
    if (r.value !== item) changed = true;
    out[key] = r.value;
  }
  return { value: changed ? out : value, hits };
}

// ---------------------------------------------------------------------------
// AgentMessage scrubbing (context hook)
// ---------------------------------------------------------------------------

interface ContentBlock {
  type: string;
  text?: string;
  arguments?: unknown;
}

/**
 * Redact user prompts, assistant text/thinking, tool-call arguments and tool-result
 * content blocks. Messages are copied on write; unchanged messages pass through.
 */
function scrubMessage(message: any): { message: any; hits: string[] } {
  const hits: string[] = [];
  const content = message?.content;
  if (typeof content === "string") {
    const r = scrubText(content);
    hits.push(...r.hits);
    return { message: r.hits.length ? { ...message, content: r.text } : message, hits };
  }
  if (Array.isArray(content)) {
    let changed = false;
    const blocks = content.map((block: ContentBlock) => {
      if (block?.type === "text" && typeof block.text === "string") {
        const r = scrubText(block.text);
        hits.push(...r.hits);
        if (r.hits.length) {
          changed = true;
          return { ...block, text: r.text };
        }
        return block;
      }
      if (block?.type === "toolCall" && block.arguments && typeof block.arguments === "object") {
        const r = scrubJson(block.arguments);
        hits.push(...r.hits);
        if (r.hits.length) {
          changed = true;
          return { ...block, arguments: r.value };
        }
        return block;
      }
      return block;
    });
    return { message: changed ? { ...message, content: blocks } : message, hits };
  }
  return { message, hits };
}

// ---------------------------------------------------------------------------
// Extension
// ---------------------------------------------------------------------------

interface ScrubState {
  enabled: boolean;
}

export default function (pi: ExtensionAPI): void {
  const state: ScrubState = { enabled: false }; // resolved on session_start (always ON)

  function notify(ctx: any, message: string, level: "info" | "warning" = "info"): void {
    // JSON/print modes have no UI (hasUI === false); commands/events still log to file.
    if (ctx?.hasUI !== false && ctx?.ui?.notify) {
      try {
        ctx.ui.notify(message, level);
      } catch {
        /* notification failures must not break scrubbing */
      }
    }
  }

  function statusLine(): string {
    return state.enabled ? "ON" : "OFF";
  }

  async function handleScrubCommand(args: string, ctx: any): Promise<void> {
    const arg = (args || "").trim().toLowerCase();
    scrubLog("DEBUG", "scrub command", { arg });
    if (arg === "" || arg === "status") {
      notify(ctx, `Secret scrubbing is ${statusLine()}. Default: ON at session start. Use /redact-on|/redact-off (or /secret-scrub on|off) to toggle, /redact test <text> to preview redaction.`);
      return;
    }
    if (arg === "on") {
      state.enabled = true;
      notify(ctx, "Secret scrubbing is ON: credentials are redacted from LLM context.");
      return;
    }
    if (arg === "off") {
      state.enabled = false;
      notify(ctx, "Secret scrubbing is OFF: full content (including secrets) is sent to the LLM.", "warning");
      return;
    }
    if (arg === "test") {
      notify(ctx, "Usage: /redact test <text to redact>", "warning");
      return;
    }
    if (arg.startsWith("test")) {
      const sample = args.trim().slice(4).trim();
      const r = scrubText(sample);
      notify(ctx, r.hits.length ? `Redacted (${r.hits.join(", ")}):\n${r.text}` : `No secrets detected. Text unchanged:\n${r.text}`);
      return;
    }
    notify(ctx, "Unknown option. Usage: /redact [on|off|status|test <text>]", "warning");
  }

  pi.on("session_start", async (_event, ctx) => {
    state.enabled = true; // scrubbing is ALWAYS on at session start
    scrubLog("INFO", `session_start: secret scrubbing ${statusLine()}`);
    notify(
      ctx,
      "Secret scrubbing is ON: API keys and passwords are redacted from LLM context (/secret-scrub off to disable).",
    );
  });

  pi.on("context", async (event, _ctx) => {
    if (!state.enabled) return;
    const hits: string[] = [];
    const messages = event.messages.map((m: any) => {
      const r = scrubMessage(m);
      hits.push(...r.hits);
      return r.message;
    });
    if (!hits.length) return;
    const counts: Record<string, number> = {};
    for (const h of hits) counts[h] = (counts[h] || 0) + 1;
    scrubLog("INFO", "context: redacted secrets in outgoing LLM request", counts);
    return { messages };
  });

  pi.on("tool_result", async (event, _ctx) => {
    if (!state.enabled) return;
    const hits: string[] = [];
    const content = event.content.map((block: any) => {
      if (block?.type === "text" && typeof block.text === "string") {
        const r = scrubText(block.text);
        hits.push(...r.hits);
        return r.hits.length ? { ...block, text: r.text } : block;
      }
      return block;
    });
    // The docs require replacing structuredContent alongside content, otherwise it is dropped.
    const result: Record<string, unknown> = { content };
    if (event.structuredContent !== undefined) {
      const r = scrubJson(event.structuredContent);
      hits.push(...r.hits);
      result.structuredContent = r.value;
    }
    if (event.details !== undefined) {
      const r = scrubJson(event.details);
      hits.push(...r.hits);
      if (r.hits.length) result.details = r.value;
    }
    if (!hits.length) return;
    const counts: Record<string, number> = {};
    for (const h of hits) counts[h] = (counts[h] || 0) + 1;
    scrubLog("INFO", "tool_result: redacted secrets", { toolName: (event as any).toolName, counts });
    return result;
  });

  pi.registerCommand("secret-scrub", {
    description: "Show/toggle redaction of API keys & passwords in LLM context (on at session start)",
    handler: (args: string, ctx: any) => handleScrubCommand(args, ctx),
  });

  pi.registerCommand("redact", {
    description: "Show/toggle credential redaction in LLM context (/redact [on|off|status|test <text>])",
    handler: (args: string, ctx: any) => handleScrubCommand(args, ctx),
  });

  pi.registerCommand("redact-on", {
    description: "Turn credential redaction ON for LLM context (also enabled at session start)",
    handler: (_args: string, ctx: any) => handleScrubCommand("on", ctx),
  });

  pi.registerCommand("redact-off", {
    description: "Turn credential redaction OFF for LLM context (secrets flow to the LLM)",
    handler: (_args: string, ctx: any) => handleScrubCommand("off", ctx),
  });

  scrubLog("DEBUG", "extension loaded (scrubbing resolves ON at session_start)");
}