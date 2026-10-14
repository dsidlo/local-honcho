/**
 * Black-box + unit tests for the secret-scrub extension (agent/extensions/secret-scrub.ts).
 *
 * The extension redacts API keys / passwords from LLM context:
 *  - scrubbing is ALWAYS ON at session start (no persisted setting),
 *  - the user can toggle it mid-session with /secret-scrub on|off|status|test,
 *  - `context` scrubs the full outgoing request (copy-on-write),
 *  - `tool_result` scrubs durably (content + structuredContent together, details when hit).
 *
 * No module mocks are required: the extension only fire-and-forget appends to a log file.
 * The pure engines (`scrubText`, `scrubJson`) are exported and tested directly.
 */

import { describe, test, expect, beforeAll } from "bun:test";
import { importFresh, SCRUB_SRC } from "./helpers/env";
import { createMockPi, type MockPi } from "./helpers/mock-pi";

const REDACTED = (kind: string) => `[REDACTED:${kind}]`;

const PEM =
  "-----BEGIN RSA PRIVATE KEY-----\n" +
  "***REMOVED***\n" +
  "moreBase64BodyLinesWithoutSpaces1234567890+/=\n" +
  "-----END RSA PRIVATE KEY-----";

const GH_PAT = "github_pat_11ABCDEFG0aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890abcdefghijk";
const GH_CLASSIC = "***REMOVED***";
const GH_OAUTH = "gho_Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2";
const GLPAT = "***REMOVED***";
const AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"; // 40 base64-ish chars
const JWT3 =
  "eyJhbGciOiJIUzI1NiJ9.***REMOVED***";

let counter = 0;

/**
 * One fresh module instance per file (keeps bun coverage attribution on a single module).
 * State freshness comes from default()'s closure: loading the default export on a fresh
 * mock creates an isolated scrubbing state for each test.
 */
let mod: any;
beforeAll(async () => {
  counter += 1;
  mod = await importFresh(SCRUB_SRC);
});

/** Fresh extension runtime (fresh state) on a fresh mock pi. */
async function boot(): Promise<{ mod: any; mock: MockPi }> {
  const mock = createMockPi();
  mod.default(mock.pi);
  return { mod, mock };
}

/** Boot and fire session_start so the extension reaches its initial ON state. */
async function bootStarted(ctxOverrides: Record<string, any> = {}): Promise<{ mod: any; mock: MockPi }> {
  const { mod, mock } = await boot();
  await mock.callHook("session_start", {}, ctxOverrides);
  return { mod, mock };
}

describe("secret-scrub pattern engine (scrubText)", () => {
  test("redacts PEM private key blocks (incl. OPENSSH variant)", async () => {
    const { mod } = await boot();
    const r1 = mod.scrubText(`here is the key:\n${PEM}\nthat was it`);
    expect(r1.text).toBe(`here is the key:\n${REDACTED("private_key")}\nthat was it`);
    expect(r1.hits).toEqual(["private_key"]);

    const openssh = PEM.replace(/RSA (PRIVATE)/g, "OPENSSH $1");
    expect(mod.scrubText(openssh).text).toBe(REDACTED("private_key"));
  });

  test("redacts GitHub/GitLab tokens", async () => {
    const { mod } = await boot();
    const cases: Array<[string, string]> = [
      [GH_PAT, "github_pat"],
      [GH_CLASSIC, "github_token"],
      [GH_OAUTH, "github_token"],
      [GLPAT, "gitlab_token"],
    ];
    for (const [token, kind] of cases) {
      const r = mod.scrubText(`token=${token}`);
      expect(r.text).toBe(`token=${REDACTED(kind)}`);
      expect(r.hits).toEqual([kind]);
    }
  });

  test("redacts Anthropic and OpenAI keys", async () => {
    const { mod } = await boot();
    expect(mod.scrubText("key: sk-ant-api03-Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk").text).toBe(
      `key: ${REDACTED("anthropic_api_key")}`,
    );
    expect(mod.scrubText("OPENAI_API_KEY = sk-proj-4Lf5Gh6Ij7Kk8Lm9Mn0Op1Qr2St3").text).toBe(
      `OPENAI_API_KEY = ${REDACTED("openai_api_key")}`,
    );
    expect(mod.scrubText("sk-plain0123456789abcdefghijkl").text).toBe(REDACTED("openai_api_key"));
    // no word boundary before "sk-" inside "risk-management" → untouched
    expect(mod.scrubText("task risk-management summary").text).not.toContain("REDACTED");
  });

  test("redacts Google, AWS, Slack, Stripe and npm credentials", async () => {
    const { mod } = await boot();
    const gkey = "AIza" + "Sx" + "1234567890" + "x".repeat(23); // AIza + exactly 35 chars
    expect(mod.scrubText(gkey).text).toBe(REDACTED("google_api_key"));
    expect(mod.scrubText("AKIAIOSFODNN7EXAMPLE is real").text).toBe(`${REDACTED("aws_access_key_id")} is real`);
    expect(mod.scrubText("ASIAIOSFODNN7EXAMPLE").text).toBe(REDACTED("aws_access_key_id"));
    expect(mod.scrubText("AKIA123").text).toBe("AKIA123"); // too short
    expect(mod.scrubText(`aws_secret_access_key = ${AWS_SECRET}`).text).toBe(REDACTED("aws_secret_access_key"));
    expect(mod.scrubText(`aws-secret-access-key: "${AWS_SECRET}"`).text).toBe(REDACTED("aws_secret_access_key"));
    expect(mod.scrubText("xoxb-123456789-ABCdefGHIjklMNOpqrsTUVwxyz").text).toBe(REDACTED("slack_token"));
    expect(mod.scrubText("sk_live_abcdefghijklmnopqrstuvwx").text).toBe(REDACTED("stripe_secret_key"));
    expect(mod.scrubText("npm_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7").text).toBe(REDACTED("npm_token"));
  });

  test("redacts JWTs (two and three segments)", async () => {
    const { mod } = await boot();
    expect(mod.scrubText(`Authorization: ${JWT3}`).text).toBe(`Authorization: ${REDACTED("jwt")}`);
    const jwt2 = "eyJhbGciOiJub25lIn0.eyJzdWIiOiIxMjM0NTY3ODkwIn0.";
    expect(mod.scrubText(jwt2).text).toBe(REDACTED("jwt"));
  });

  test("redacts Bearer/Basic auth headers, skips short values", async () => {
    const { mod } = await boot();
    expect(mod.scrubText("Authorization: Bearer abc123def456ghi789jkl").text).toBe(
      `Authorization: ${REDACTED("auth_header")}`,
    );
    expect(mod.scrubText("basic dXNlcjpwYXNzd29yZDEyMzQ1Njc4OQ==").text).toBe(REDACTED("auth_header"));
    expect(mod.scrubText("Bearer abc").text).toBe("Bearer abc"); // too short
  });

  test("redacts passwords inside connection strings, leaves cred-less URLs alone", async () => {
    const { mod } = await boot();
    const baseUrl = (scheme: string) => `${scheme}://svc:dbpassw1@host.example.com/db`;
    for (const scheme of ["postgres", "postgresql", "mysql", "mariadb", "mongodb", "mongodb+srv", "redis", "rediss", "amqp", "amqps"]) {
      const r = mod.scrubText(baseUrl(scheme));
      expect(r.text).toBe(`${scheme}://svc:${REDACTED("db_url_password")}@host.example.com/db`);
      expect(r.hits).toEqual(["db_url_password"]);
    }
    expect(mod.scrubText("postgres://db.example.com/app").text).toBe("postgres://db.example.com/app");
    expect(mod.scrubText("https://user:pass@example.com").text).toBe("https://user:pass@example.com");
  });

  test("redacts UPPER_SNAKE env-style credentials, keeps the variable name", async () => {
    const { mod } = await boot();
    const r = mod.scrubText("HONCHO_API_KEY=supersecrettoken1");
    expect(r.text).toBe(`HONCHO_API_KEY=${REDACTED("env_credential")}`);
    expect(r.hits).toEqual(["env_credential"]);

    expect(mod.scrubText('MY_TOKEN: "abc123def456ghi"').text).toBe(`MY_TOKEN: ${REDACTED("env_credential")}`);
    expect(mod.scrubText("MY_APP_SECRET=zz9y8x7w6v5").text).toBe(`MY_APP_SECRET=${REDACTED("env_credential")}`);
    expect(mod.scrubText('process.env.MY_APP_PASSWORD = "realpass123"').text).toBe(
      `process.env.MY_APP_PASSWORD = ${REDACTED("env_credential")}`,
    );
    // variable references survive
    expect(mod.scrubText("MY_TOKEN=${VAULT_TOKEN}").text).toBe("MY_TOKEN=${VAULT_TOKEN}");
    expect(mod.scrubText("MY_APP_SECRET: %SECRET%").text).toBe("MY_APP_SECRET: %SECRET%");
  });

  test("redacts generic key=value credentials with false-positive guards", async () => {
    const { mod } = await boot();
    expect(mod.scrubText("password: hunter22").text).toBe(`password: ${REDACTED("credential_value")}`);
    expect(mod.scrubText("password: hunter22").hits).toEqual(["credential_value"]);
    expect(mod.scrubText('api_key = "abcd1234efgh"').text).toBe(`api_key = ${REDACTED("credential_value")}`);
    expect(mod.scrubText("secret = topsecret42").text).toBe(`secret = ${REDACTED("credential_value")}`);
    expect(mod.scrubText("client_secret: 'zzz999yyy000'").text).toBe(`client_secret: ${REDACTED("credential_value")}`);

    // guards: placeholders / references / already-masked / trivial values
    expect(mod.scrubText("password: changeme").text).toBe("password: changeme");
    expect(mod.scrubText("password: ${DATABASE_PASSWORD}").text).toBe("password: ${DATABASE_PASSWORD}");
    expect(mod.scrubText("password: %PASSWORD%").text).toBe("password: %PASSWORD%");
    expect(mod.scrubText("password: ********").text).toBe("password: ********");
    expect(mod.scrubText("secret: your-value-here").text).toBe("secret: your-value-here");
    expect(mod.scrubText("token: true").text).toBe("token: true"); // value <6 chars
    expect(mod.scrubText("password: 123456").text).toBe(`password: ${REDACTED("credential_value")}`); // PINs are credentials
  });

  test("placeholder output is stable (never re-matched)", async () => {
    const { mod } = await boot();
    const kinds = [
      "api_key", "credential_value", "env_credential", "db_url_password",
      "aws_secret_access_key", "private_key", "jwt", "openai_api_key", "auth_header",
    ];
    for (const kind of kinds) {
      expect(mod.scrubText(REDACTED(kind)).text).toBe(REDACTED(kind));
      expect(mod.scrubText(REDACTED(kind)).hits).toEqual([]);
    }
    const once = mod.scrubText("Authorization: Bearer abc123def456ghi789jkl password: hunter22");
    const twice = mod.scrubText(once.text);
    expect(twice.text).toBe(once.text);
    expect(twice.hits).toEqual([]);
  });

  test("multiple patterns in one text are all redacted and reported in pattern order", async () => {
    const { mod } = await boot();
    const text = `AKIAIOSFODNN7EXAMPLE\n${GH_PAT}\npassword: hunter22`;
    const r = mod.scrubText(text);
    expect(r.hits).toEqual(["github_pat", "aws_access_key_id", "credential_value"]);
    expect(r.text).toBe(
      `${REDACTED("aws_access_key_id")}\n${REDACTED("github_pat")}\npassword: ${REDACTED("credential_value")}`,
    );
  });

  test("plain text, empty input, bare keywords and short values pass through untouched", async () => {
    const { mod } = await boot();
    const plain = "The quick brown fox jumps over the lazy dog. URL: https://example.com/a/b";
    expect(mod.scrubText(plain).text).toBe(plain);
    expect(mod.scrubText(plain).hits).toEqual([]);
    expect(mod.scrubText("").hits).toEqual([]);
    expect(mod.scrubText("password").hits).toEqual([]); // keyword without separator
    expect(mod.scrubText("pwd: ok").hits).toEqual([]); // value too short
  });
});

describe("secret-scrub JSON scrubbing (scrubJson)", () => {
  test("scrubs strings and nested objects, copying on write", async () => {
    const { mod } = await boot();
    const input = { file: "notes.txt", content: "password: hunter22", meta: { ok: true } };
    const r = mod.scrubJson(input);
    expect((r.value as any).content).toBe(`password: ${REDACTED("credential_value")}`);
    expect((r.value as any).file).toBe("notes.txt");
    expect(r.value).not.toBe(input); // changed → copied
    expect((r.value as any).meta).toBe(input.meta); // unchanged subtree keeps identity

    const clean = { a: 1, b: null, c: [true, 4.2] };
    const rc = mod.scrubJson(clean);
    expect(rc.value).toBe(clean);
    expect(rc.hits).toEqual([]);
  });

  test("scrubs arrays; unchanged arrays keep identity", async () => {
    const { mod } = await boot();
    const arr = ["hello", "token: abc123def"];
    const r = mod.scrubJson(arr);
    expect(r.hits).toEqual(["credential_value"]);
    expect((r.value as any[])[0]).toBe("hello");
    expect((r.value as any[])[1]).toBe(`token: ${REDACTED("credential_value")}`);
    expect(r.value).not.toBe(arr);

    const cleanArr = ["a", { b: 2 }];
    const rc = mod.scrubJson(cleanArr);
    expect(rc.value).toBe(cleanArr);
  });

  test("passes primitives through", async () => {
    const { mod } = await boot();
    for (const v of [null, undefined, 5, true, 4.2]) {
      expect(mod.scrubJson(v).value).toBe(v);
      expect(mod.scrubJson(v).hits).toEqual([]);
    }
  });

  test("stops recursing deeper than 8 levels", async () => {
    const { mod } = await boot();
    let deep: any = { leaf: "password: hunter22" };
    for (let i = 0; i < 12; i++) deep = { nested: deep };
    const r = mod.scrubJson(deep);
    expect(r.hits).toEqual([]);
    expect(JSON.stringify(r.value)).toContain("hunter22"); // untouched below the depth limit

    let shallow: any = { leaf: "password: hunter22" };
    for (let i = 0; i < 8; i++) shallow = { nested: shallow };
    const rs = mod.scrubJson(shallow);
    expect(rs.hits).toEqual(["credential_value"]);
    expect(JSON.stringify(rs.value)).toContain("REDACTED");
  });
});

describe("secret-scrub hooks: lifecycle and context", () => {
  test("session_start always enables scrubbing and notifies", async () => {
    const { mock } = await bootStarted();
    expect(mock.notifications).toHaveLength(1);
    expect(mock.notifications[0].message).toContain("ON");
    expect(mock.notifications[0].message).toContain("/secret-scrub off");
  });

  test("session_start resets to ON even after an explicit OFF in the same process", async () => {
    const { mock } = await boot();
    await mock.runCommand("secret-scrub", "off");
    // simulate a new session on the same extension runtime
    await mock.callHook("session_start", {}, {});
    const res = await mock.callHook("context", { messages: [{ role: "user", content: "password: hunter22" }] });
    expect(res).toEqual({ messages: [{ role: "user", content: `password: ${REDACTED("credential_value")}` }] });
  });

  test("context scrubs user prompts, assistant messages, tool-call args and tool results", async () => {
    const { mock } = await bootStarted();
    const toolCallBlock = {
      type: "toolCall", id: "t1", name: "bash",
      arguments: { command: 'echo "HONCHO_API_KEY=supersecrettoken1"' },
    };
    const messages = [
      { role: "user", content: "please fix\napi_key = abcd1234efgh" },
      {
        role: "assistant",
        content: [
          { type: "text", text: "I will read password: hunter22" },
          { type: "thinking", thinking: "internal" },
        ],
      },
      { role: "assistant", content: [
        { type: "text", text: "running:" },
        toolCallBlock,
        { type: "toolCall", id: "t2", name: "read", arguments: { path: "notes.txt" } },
      ] },
      {
        role: "toolResult",
        content: [
          { type: "text", text: "postgres://alice:s3cretpw@db.example.com/app" },
          { type: "image", data: "base64==" },
        ],
        toolCallId: "t1",
        toolName: "bash",
        isError: false,
      },
    ];
    const res = await mock.callHook("context", { messages });
    expect(res.messages).toBeDefined();
    expect(res.messages[0].content).toBe(`please fix\napi_key = ${REDACTED("credential_value")}`);
    const blocks1 = res.messages[1].content;
    expect(blocks1[0].text).toBe(`I will read password: ${REDACTED("credential_value")}`);
    expect(blocks1[1]).toBe(messages[1].content[1]); // thinking untouched (same ref)
    const blocks2 = res.messages[2].content;
    expect(blocks2[0]).toBe(messages[2].content[0]); // unchanged block keeps identity
    expect(blocks2[1].arguments.command).toBe(`echo "HONCHO_API_KEY=${REDACTED("env_credential")}"`);
    expect(blocks2[2]).toBe(messages[2].content[2]); // clean args keep identity
    expect(res.messages[3].content[0].text).toBe(
      `postgres://alice:${REDACTED("db_url_password")}@db.example.com/app`,
    );
    expect(res.messages[3].content[1]).toBe(messages[3].content[1]); // image untouched
    // copy-on-write: changed messages are new objects, originals untouched
    expect(res.messages[0]).not.toBe(messages[0]);
    expect(messages[0].content).toBe("please fix\napi_key = abcd1234efgh");
  });

  test("context returns undefined when nothing matches (no provider payload replacement)", async () => {
    const { mock } = await bootStarted();
    const res = await mock.callHook("context", { messages: [{ role: "user", content: "hello world" }] });
    expect(res).toBeUndefined();
  });

  test("context handles malformed/custom message shapes", async () => {
    const { mock } = await bootStarted();
    const res = await mock.callHook("context", { messages: [{ foo: "bar" }, { role: "user", content: null }] });
    expect(res).toBeUndefined();
  });

  test("disabled scrubbing leaves content completely intact", async () => {
    const { mock } = await bootStarted();
    await mock.runCommand("secret-scrub", "off");
    const content = "password: hunter22";
    const res = await mock.callHook("context", { messages: [{ role: "user", content }] });
    const res2 = await mock.callHook("context", { messages: [{ role: "user", content }] });
    expect(res).toBeUndefined();
    expect(res2).toBeUndefined();
    expect(content).toBe("password: hunter22");
  });

  test("/secret-scrub on re-enables and re-redacts already-transcribed content", async () => {
    const { mock } = await bootStarted();
    await mock.runCommand("secret-scrub", "off");
    await mock.runCommand("secret-scrub", "on");
    const res = await mock.callHook("context", { messages: [{ role: "user", content: "password: hunter22" }] });
    expect(res.messages[0].content).toBe(`password: ${REDACTED("credential_value")}`);
  });

  test("headless mode (hasUI: false) skips notifications but still scrubs", async () => {
    const { mock } = await bootStarted({ hasUI: false });
    expect(mock.notifications).toHaveLength(0);
    const res = await mock.callHook("context", { messages: [{ role: "user", content: "password: hunter22" }] });
    expect(res.messages[0].content).toBe(`password: ${REDACTED("credential_value")}`);
  });
});

describe("secret-scrub hooks: tool_result", () => {
  const baseToolResult = (overrides: Record<string, unknown> = {}) => ({
    type: "tool_result",
    toolCallId: "t1",
    toolName: "bash",
    input: {},
    content: [{ type: "text", text: "ls -la" }] as any,
    isError: false,
    ...overrides,
  });

  test("scrubs content durably and redacts structuredContent alongside it", async () => {
    const { mock } = await bootStarted();
    const res = await mock.callHook(
      "tool_result",
      baseToolResult({
        content: [{ type: "text", text: "token: abc123def" }],
        structuredContent: { output: "password: hunter22", exitCode: 0 },
      }),
    );
    expect(res.content[0].text).toBe(`token: ${REDACTED("credential_value")}`);
    expect(res.structuredContent.output).toBe(`password: ${REDACTED("credential_value")}`);
    expect(res.structuredContent.exitCode).toBe(0);
  });

  test("always returns structuredContent when replacing content, even when unchanged", async () => {
    const { mock } = await bootStarted();
    const structured = { value: "clean" };
    const res = await mock.callHook(
      "tool_result",
      baseToolResult({ content: [{ type: "text", text: "secret = topsecret42" }], structuredContent: structured }),
    );
    expect(res.structuredContent).toEqual(structured);
    expect(res.structuredContent).toBe(structured); // unchanged JSON keeps identity
  });

  test("returns undefined when there is nothing to redact (pass-through)", async () => {
    const { mock } = await bootStarted();
    expect(await mock.callHook("tool_result", baseToolResult({ structuredContent: { a: 1 } }))).toBeUndefined();
  });

  test("scrubs details only when they contain hits, keeps originals by reference otherwise", async () => {
    const { mock } = await bootStarted();
    const res1 = await mock.callHook(
      "tool_result",
      baseToolResult({ details: { command: "ls -la", cwd: "/tmp" } }),
    );
    expect(res1).toBeUndefined(); // no hits anywhere → full pass-through

    const dirtyDetails = { command: 'echo "password: hunter22"' };
    const res2 = await mock.callHook("tool_result", baseToolResult({ details: dirtyDetails }));
    expect(res2).toBeDefined();
    expect(res2.details.command).toBe(`echo "password: ${REDACTED("credential_value")}"`);
    expect(dirtyDetails.command).toBe('echo "password: hunter22"'); // copy-on-write
  });

  test("image blocks pass through untouched", async () => {
    const { mock } = await bootStarted();
    const res = await mock.callHook(
      "tool_result",
      baseToolResult({ content: [{ type: "image", data: "aGVsbG8=" }, { type: "text", text: "pwd: ok" }] }),
    );
    expect(res).toBeUndefined();
  });

  test("disabled hook is a no-op", async () => {
    const { mock } = await bootStarted();
    await mock.runCommand("secret-scrub", "off");
    const res = await mock.callHook(
      "tool_result",
      baseToolResult({ content: [{ type: "text", text: "password: hunter22" }] }),
    );
    expect(res).toBeUndefined();
  });
});

describe("secret-scrub command /secret-scrub", () => {
  test("no args / status report the current state", async () => {
    const { mock } = await bootStarted();
    await mock.runCommand("secret-scrub", "");
    expect(mock.notifications.at(-1)?.message).toContain("Secret scrubbing is ON");
    await mock.runCommand("secret-scrub", "status");
    expect(mock.notifications.at(-1)?.message).toContain("Secret scrubbing is ON");
    await mock.runCommand("secret-scrub", "off");
    await mock.runCommand("secret-scrub", "status");
    expect(mock.notifications.at(-1)?.message).toContain("Secret scrubbing is OFF");
  });

  test("off notifies at warning level; on restores the ON notice", async () => {
    const { mock } = await bootStarted();
    await mock.runCommand("secret-scrub", "off");
    const offNotice = mock.notifications.at(-1);
    expect(offNotice?.level).toBe("warning");
    expect(offNotice?.message).toContain("OFF");
    await mock.runCommand("secret-scrub", "on");
    const onNotice = mock.notifications.at(-1);
    expect(onNotice?.level).toBe("info");
    expect(onNotice?.message).toContain("ON");
  });

  test("test previews redaction with matched pattern names", async () => {
    const { mock } = await bootStarted();
    await mock.runCommand("secret-scrub", "test password: hunter22 and AKIAIOSFODNN7EXAMPLE");
    const notice = mock.notifications.at(-1)?.message ?? "";
    expect(notice).toContain("credential_value");
    expect(notice).toContain("aws_access_key_id");
    expect(notice).toContain(REDACTED("credential_value"));
    expect(notice).not.toContain("hunter22");

    await mock.runCommand("secret-scrub", "test nothing sensitive here");
    expect(mock.notifications.at(-1)?.message).toContain("No secrets detected");
  });

  test("test without a sample shows usage warnings", async () => {
    const { mock } = await bootStarted();
    await mock.runCommand("secret-scrub", "test   ");
    expect(mock.notifications.at(-1)?.message).toContain("Usage:");
    expect(mock.notifications.at(-1)?.level).toBe("warning");
  });

  test("unknown option shows usage warning", async () => {
    const { mock } = await bootStarted();
    await mock.runCommand("secret-scrub", "explode");
    expect(mock.notifications.at(-1)?.message).toContain("Usage:");
    expect(mock.notifications.at(-1)?.level).toBe("warning");
  });
});

describe("secret-scrub command aliases /redact, /redact-on, /redact-off", () => {
  test("/redact shows state and mirrors /secret-scrub subcommands", async () => {
    const { mock } = await bootStarted();
    await mock.runCommand("redact", "");
    expect(mock.notifications.at(-1)?.message).toContain("Secret scrubbing is ON");
    expect(mock.notifications.at(-1)?.message).toContain("/redact-on");

    await mock.runCommand("redact", "status");
    expect(mock.notifications.at(-1)?.message).toContain("Secret scrubbing is ON");

    await mock.runCommand("redact", "off");
    expect(mock.notifications.at(-1)?.level).toBe("warning");
    let res = await mock.callHook("context", { messages: [{ role: "user", content: "password: hunter22" }] });
    expect(res).toBeUndefined();

    await mock.runCommand("redact", "on");
    res = await mock.callHook("context", { messages: [{ role: "user", content: "password: hunter22" }] });
    expect(res.messages[0].content).toBe(`password: ${REDACTED("credential_value")}`);

    await mock.runCommand("redact", "test password: hunter22");
    expect(mock.notifications.at(-1)?.message).toContain("credential_value");
    expect(mock.notifications.at(-1)?.message).not.toContain("hunter22");
  });

  test("/redact-on enables scrubbing and /redact-off disables it (with warning)", async () => {
    const { mock } = await bootStarted(); // starts ON
    await mock.runCommand("redact-off");
    expect(mock.notifications.at(-1)?.message).toContain("OFF");
    expect(mock.notifications.at(-1)?.level).toBe("warning");
    expect(await mock.callHook("context", { messages: [{ role: "user", content: "password: hunter22" }] })).toBeUndefined();

    await mock.runCommand("redact-on");
    expect(mock.notifications.at(-1)?.message).toContain("ON");
    const res = await mock.callHook("context", { messages: [{ role: "user", content: "password: hunter22" }] });
    expect(res.messages[0].content).toBe(`password: ${REDACTED("credential_value")}`);

    // idempotent when already ON
    const before = mock.notifications.length;
    await mock.runCommand("redact-on");
    expect(mock.notifications.length).toBe(before + 1);
    expect(mock.notifications.at(-1)?.message).toContain("ON");
  });

  test("/redact supports bare test + unknown options, /redact-on|off ignore extra args", async () => {
    const { mock } = await bootStarted();
    await mock.runCommand("redact", "test");
    expect(mock.notifications.at(-1)?.message).toContain("Usage:");
    expect(mock.notifications.at(-1)?.level).toBe("warning");

    await mock.runCommand("redact", "explode");
    expect(mock.notifications.at(-1)?.message).toContain("Usage: /redact [on|off|status|test <text>]");

    // extra args to the dedicated toggles are ignored ("on" wins)
    await mock.runCommand("redact-off", "please");
    expect(mock.notifications.at(-1)?.message).toContain("OFF");
    await mock.runCommand("redact-on", "now");
    expect(mock.notifications.at(-1)?.message).toContain("ON");
  });

  test("session_start resets to ON even when disabled via /redact-off", async () => {
    const { mock } = await boot();
    await mock.runCommand("redact-off");
    await mock.callHook("session_start", {}, {});
    const res = await mock.callHook("context", { messages: [{ role: "user", content: "password: hunter22" }] });
    expect(res.messages[0].content).toBe(`password: ${REDACTED("credential_value")}`);
  });

  test("/redact works before session_start (factory-only state)", async () => {
    const { mock } = await boot();
    await mock.runCommand("redact", "");
    expect(mock.notifications.at(-1)?.message).toContain("Secret scrubbing is OFF");
    await mock.runCommand("redact-on");
    const res = await mock.callHook("context", { messages: [{ role: "user", content: "password: hunter22" }] });
    expect(res.messages[0].content).toBe(`password: ${REDACTED("credential_value")}`);
  });

  test("all four command names are registered", async () => {
    const { mock } = await boot();
    expect([...mock.commands.keys()].sort()).toEqual(["redact", "redact-off", "redact-on", "secret-scrub"]);
  });
});