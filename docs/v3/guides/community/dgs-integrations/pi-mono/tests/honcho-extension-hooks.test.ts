import { describe, it, expect, afterEach } from "bun:test";
import { bootstrap } from "./helpers/honcho-harness";
import { MockFetch } from "./helpers/mock-fetch";
import { drain } from "./helpers/env";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Flush setTimeout(0) flush timers so they cannot leak into later tests.
afterEach(async () => {
  await drain();
});

const WS = "http://honcho.test/v3/workspaces/ws-test";

const tempDirs: string[] = [];
function sandboxDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "honcho-hook-"));
  tempDirs.push(dir);
  return dir;
}

const msgPosts = (mf: MockFetch): any[] =>
  mf.calls.filter((c) => /\/sessions\/[^/]+\/messages$/.test(c.url) && c.method === "POST");
const allMsgs = (mf: MockFetch): any[] => msgPosts(mf).flatMap((c) => c.body.messages);

/* ------------------------------------------------------------------ */
/* session_start                                                       */
/* ------------------------------------------------------------------ */

describe("honcho extension session_start", () => {
  it("ensures the static workspace exists and notifies", async () => {
    const t = await bootstrap();
    await t.callHook("session_start", {});

    const create = t.mf.calls.find((c) => c.method === "POST" && /\/workspaces\/?$/.test(c.url))!;
    expect(create.url).toBe("http://honcho.test/v3/workspaces");
    expect(create.body).toEqual({ id: "ws-test" });
    expect(t.app.notifications[0].message).toBe("Honcho: ws-test (📌 static)");
  });

  it("in auto mode detects the workspace from the git origin", async () => {
    const root = sandboxDir();
    const proj = join(root, "MyProject");
    mkdirSync(join(proj, ".git"), { recursive: true });
    writeFileSync(join(proj, ".git", "config"), '[remote "origin"]\n\turl = https://github.com/some-org/My-Repo.git\n');

    const t = await bootstrap({ env: { HONCHO_WORKSPACE_MODE: "auto" } });
    await t.callHook("session_start", {}, { cwd: proj });

    const create = t.mf.calls.find((c) => /\/workspaces\/?$/.test(c.url) && c.method === "POST")!;
    expect(create.body).toEqual({ id: "my-repo" });
    expect(t.app.notifications[0].message).toBe("Honcho: my-repo (🔄 auto)");
  });

  it("in auto mode uses the directory basename when .git/config has no origin", async () => {
    const root = sandboxDir();
    const proj = join(root, "myproject");
    mkdirSync(join(proj, ".git"), { recursive: true });
    writeFileSync(join(proj, ".git", "config"), "[core]\n\trepositoryformatversion = 0\n");

    const t = await bootstrap({ env: { HONCHO_WORKSPACE_MODE: "auto" } });
    await t.callHook("session_start", {}, { cwd: proj });
    expect(t.app.notifications[0].message).toBe("Honcho: myproject (🔄 auto)");
  });

  it("in auto mode finds a .git/config in an ancestor directory", async () => {
    const root = sandboxDir();
    const proj = join(root, "MyProject");
    mkdirSync(join(proj, ".git"), { recursive: true });
    mkdirSync(join(proj, "deeply", "nested"), { recursive: true });
    writeFileSync(join(proj, ".git", "config"), '[remote "origin"]\n\turl = git@github.com:acme/widget-tool.git\n');

    const t = await bootstrap({ env: { HONCHO_WORKSPACE_MODE: "auto" } });
    await t.callHook("session_start", {}, { cwd: join(proj, "deeply", "nested") });
    expect(t.app.notifications[0].message).toBe("Honcho: widget-tool (🔄 auto)");
  });

  it("in auto mode prefixes well-known working-directory names with their parent", async () => {
    const root = sandboxDir();
    const dir = join(root, "apps", "server");
    mkdirSync(dir, { recursive: true });

    const t = await bootstrap({ env: { HONCHO_WORKSPACE_MODE: "auto" } });
    await t.callHook("session_start", {}, { cwd: dir });
    expect(t.app.notifications[0].message).toBe("Honcho: apps-server (🔄 auto)");
  });

  it("survives workspace-creation failures without crashing", async () => {
    const t = await bootstrap();
    t.mf.on((c) => /\/workspaces\/?$/.test(c.url) && c.method === "POST", () => new Response("down", { status: 500 }));
    await t.callHook("session_start", {});
    expect(t.app.notifications).toHaveLength(0);
  });
});

/* ------------------------------------------------------------------ */
/* prompt / turn / thought / tool events                               */
/* ------------------------------------------------------------------ */

describe("honcho extension conversation hooks", () => {
  it("before_agent_start creates a pi-<ts> session with user and agent peers", async () => {
    const t = await bootstrap();
    await t.callHook("before_agent_start", { prompt: "hello there" });
    await drain();

    const create = t.mf.calls.find((c) => /\/sessions$/.test(c.url) && c.method === "POST")!;
    expect(create.url).toBe(`${WS}/sessions`);
    expect(create.body.id).toMatch(/^pi-\d+$/);
    expect(create.body.peers).toEqual({ "user-t": {}, "agent-t": {} });

    expect(msgPosts(t.mf)).toHaveLength(1);
    expect(allMsgs(t.mf)[0]).toEqual({
      content: "hello there",
      peer_id: "user-t",
      metadata: { role: "user", type: "prompt", has_images: false, intended_model: "test-provider/test-model" },
    });
  });

  it("records has_images and the unknown model correctly", async () => {
    const t = await bootstrap();
    await t.callHook("before_agent_start", { prompt: "look", images: [{ type: "image" }] }, { model: null });
    await drain();
    expect(allMsgs(t.mf)[0].metadata.has_images).toBe(true);
    expect(allMsgs(t.mf)[0].metadata.intended_model).toBe("unknown");
  });

  it("turn_start queues the turn marker without flushing", async () => {
    const t = await bootstrap();
    await t.callHook("before_agent_start", { prompt: "seed" }); // sets the module's currentModel
    await drain();
    t.mf.calls.length = 0;

    await t.callHook("turn_start", { turnIndex: 3 });
    expect(msgPosts(t.mf)).toHaveLength(0);

    await t.callHook("agent_end", {}); // triggers the flush
    await drain();
    expect(allMsgs(t.mf)).toEqual([
      {
        content: "Starting turn 3",
        peer_id: "agent-t",
        metadata: { type: "turn_start", turn_index: 3, model: "test-provider/test-model" },
      },
    ]);
  });

  it("context captures the thinking from the last assistant message", async () => {
    const t = await bootstrap();
    await t.callHook("before_agent_start", { prompt: "seed" });
    await drain();
    t.mf.calls.length = 0;

    await t.callHook("context", {
      messages: [
        { role: "assistant", content: [{ type: "text", text: "plan part one " }, { type: "text", text: "part two" }] },
        { role: "user", content: "not an assistant" },
        { role: "assistant", content: [] }, // no text -> skipped
      ],
    });
    await t.callHook("agent_end", {});
    await drain();

    expect(allMsgs(t.mf)).toEqual([
      {
        content: "Thought: plan part one part two",
        peer_id: "agent-t",
        metadata: { type: "thought", step: "planning", model: "test-provider/test-model" },
      },
    ]);
  });

  it("context skips when no assistant message has text", async () => {
    const t = await bootstrap();
    await t.callHook("context", { messages: [{ role: "user", content: "just a user" }] });
    await t.callHook("agent_end", {});
    await drain();
    expect(msgPosts(t.mf)).toHaveLength(0);
  });

  it("tool_call queues a serialized tool invocation", async () => {
    const t = await bootstrap();
    await t.callHook("before_agent_start", { prompt: "seed" });
    await drain();
    t.mf.calls.length = 0;

    await t.callHook("tool_call", { toolName: "bash", toolCallId: "tc-1", input: { cmd: "ls" } });
    await t.callHook("agent_end", {});
    await drain();

    expect(allMsgs(t.mf)[0].content).toBe(JSON.stringify({ tool: "bash", tool_call_id: "tc-1", input: { cmd: "ls" } }));
    expect(allMsgs(t.mf)[0].metadata).toMatchObject({
      type: "tool_call",
      tool: "bash",
      tool_call_id: "tc-1",
      model: "test-provider/test-model",
    });
  });

  it("tool_result queues string output with success status", async () => {
    const t = await bootstrap();
    await t.callHook("tool_result", {
      toolName: "read",
      toolCallId: "tc-2",
      isError: false,
      result: { content: "file contents" },
    });
    await t.callHook("agent_end", {});
    await drain();

    expect(allMsgs(t.mf)[0].content).toBe("Observation (read):\nfile contents");
    expect(allMsgs(t.mf)[0].metadata).toMatchObject({
      type: "observation",
      tool: "read",
      tool_call_id: "tc-2",
      is_error: false,
      status: "success",
      output_length: "file contents".length,
      will_be_chunked: false,
    });
  });

  it("tool_result joins array content and flags errors", async () => {
    const t = await bootstrap();
    await t.callHook("tool_result", {
      toolName: "edit",
      toolCallId: "tc-3",
      isError: true,
      result: { content: [{ type: "text", text: "no such " }, { type: "text", text: "file" }] },
    });
    await t.callHook("agent_end", {});
    await drain();

    expect(allMsgs(t.mf)[0].content).toBe("Observation (edit):\nno such file");
    expect(allMsgs(t.mf)[0].metadata).toMatchObject({ is_error: true, status: "error" });
  });

  it("tool_result flags oversized output as will_be_chunked", async () => {
    const t = await bootstrap();
    await t.callHook("tool_result", {
      toolName: "big",
      toolCallId: "tc-4",
      isError: false,
      result: { content: "x".repeat(9000) },
    });
    await t.callHook("agent_end", {});
    await drain();
    expect(allMsgs(t.mf)[0].metadata.will_be_chunked).toBe(true);
  });

  it("turn_end queues the final assistant response and flushes asynchronously", async () => {
    const t = await bootstrap();
    await t.callHook("before_agent_start", { prompt: "seed" });
    await drain();
    t.mf.calls.length = 0;

    await t.callHook("turn_start", { turnIndex: 0 });
    await t.callHook("turn_end", {
      turnIndex: 0,
      message: { role: "assistant", content: [{ type: "text", text: "final answer" }] },
    });
    expect(msgPosts(t.mf)).toHaveLength(0);
    await drain();

    expect(msgPosts(t.mf)).toHaveLength(1);
    expect(allMsgs(t.mf)).toEqual([
      "Starting turn 0",
      "final answer",
    ].map((content) => ({
      content,
      peer_id: "agent-t",
      metadata:
        content === "Starting turn 0"
          ? { type: "turn_start", turn_index: 0, model: "test-provider/test-model" }
          : { role: "assistant", type: "final", turn_index: 0, model: "test-provider/test-model" },
    })));
  });

  it("turn_end ignores non-assistant or missing messages", async () => {
    const t = await bootstrap();
    await t.callHook("turn_end", { turnIndex: 1, message: { role: "user", content: "not assist" } });
    await t.callHook("turn_end", { turnIndex: 2 });
    await drain();
    expect(msgPosts(t.mf)).toHaveLength(0);
  });

  it("session_shutdown flushes and then queues the observation overlay summary", async () => {
    const t = await bootstrap();
    await t.callHook("session_shutdown", {});
    expect(t.mf.calls).toHaveLength(0); // nothing to do

    await t.callHook("turn_start", { turnIndex: 0 });
    await t.callHook("session_shutdown", {});
    expect(allMsgs(t.mf)[0].content).toBe("Starting turn 0");
    expect(t.mf.callsWhere("/messages")).toHaveLength(1);
  });
});

/* ------------------------------------------------------------------ */
/* compaction (session_before_compact)                                 */
/* ------------------------------------------------------------------ */

describe("honcho extension compaction", () => {
  const prep = (extra: any = {}) => ({
    messagesToSummarize: [
      { role: "user", content: "what changed?" },
      { role: "assistant", content: [{ type: "text", text: "I edited " }, { type: "text", text: "main.ts" }] },
    ],
    turnPrefixMessages: [],
    previousSummary: "",
    firstKeptEntryId: "keep-1",
    tokensBefore: 42,
    fileOps: { read: [], edited: [], written: [] }, // pi always provides fileOps
    ...extra,
  });

  it("returns nothing when no model is available", async () => {
    const t = await bootstrap();
    const result = await t.callHook("session_before_compact", { preparation: prep() }, { model: null });
    expect(result).toBeUndefined();
    expect(t.mf.calls).toHaveLength(0);
  });

  it("falls back to a fixed summary when Honcho has no synthesis insights", async () => {
    const t = await bootstrap();
    const result = await t.callHook("session_before_compact", { preparation: prep() });

    // The conversation is stored before the query happens.
    expect(allMsgs(t.mf)).toEqual([
      {
        content: "[user]: what changed?\n[assistant]: I edited main.ts",
        peer_id: "agent-t",
        metadata: { type: "obs_extraction", session_part: "compact" },
      },
    ]);
    const query = t.mf.calls.find((c) => c.url.endsWith("/conclusions/query"))!;
    expect(query.body).toEqual({
      query: "recent observations pi session",
      top_k: 10,
      filters: { level: "synthesis" },
    });

    expect(result.compaction.summary).toBe(
      "## Observations\n- No Honcho insights; using raw context.\n\n## Open Threads\n- Recent messages.\n\n## Next Action Bias\n1. Proceed with current task.",
    );
    expect(result.compaction.firstKeptEntryId).toBe("keep-1");
    expect(result.compaction.tokensBefore).toBe(42);
    expect(result.compaction.details).toEqual({ strategy: "honcho-obs" });
  });

  it("reflects Honcho insights with dedupe and priority limits", async () => {
    const fillers = Array.from({ length: 45 }, (_, i) => `- 🟢 filler-${i}`).join("\n");
    const t = await bootstrap({
      defaults: {
        conclusionsQuery: [
          { content: "- 🔴 Check tests first\n- 🟢 Check tests first" }, // dup -> red wins
          { content: "- 🟡 Watch the flaky importer" },
          { content: fillers },
        ],
      },
    });
    const result = await t.callHook("session_before_compact", { preparation: prep() });
    const { summary, details } = result.compaction;

    expect(summary.startsWith("## Observations\n")).toBe(true);
    expect(summary).toContain("- 🔴 🔴 Check tests first");
    expect(summary).toContain("- 🟡 🟡 Watch the flaky importer");
    expect(summary).toContain("- 🟢 🟢 filler-0");
    expect(summary).toContain("- 🟢 🟢 filler-15");
    expect(summary).not.toContain("- 🟢 🟢 filler-16");
    expect(summary).toContain("## Open Threads\n- Continue from Honcho synthesis.");
    expect(summary).toContain("## Next Action Bias\n1. Use retrieved patterns from memory.");

    expect(details).toEqual({ strategy: "honcho-obs", honcho_hits: 3, dropped: 30 }); // 48 lines, 18 kept
  });

  it("appends read/modified file tags from the preparation fileOps", async () => {
    const t = await bootstrap();
    const result = await t.callHook("session_before_compact", {
      preparation: prep({ fileOps: { read: ["a.txt", "b.txt"], edited: ["c.ts"], written: ["d.md"] } }),
    });
    expect(result.compaction.summary).toContain("<read-files>\na.txt\nb.txt\n</read-files>");
    expect(result.compaction.summary).toContain("<modified-files>\nc.ts\nd.md\n</modified-files>");
  });

  it("treats query failures (404 and otherwise) as no insights", async () => {
    for (const status of [404, 500]) {
      const t = await bootstrap({
        defaults: { conclusionsQuery: new Response(`{"error":"x"}`, { status }) },
      });
      const result = await t.callHook("session_before_compact", { preparation: prep() });
      expect(result.compaction.summary).toContain("No Honcho insights; using raw context.");
      expect(result.compaction.details).toEqual({ strategy: "honcho-obs" });
    }
  });
});

/* ------------------------------------------------------------------ */
/* branch tree (session_before_tree)                                   */
/* ------------------------------------------------------------------ */

describe("honcho extension branch tree", () => {
  const prep = (extra: any = {}) => ({
    entriesToSummarize: [
      { content: "- 🔴 Keep branch state intact" },
      { content: "plain branch chatter" },
    ],
    fileOps: { read: ["one.txt"] },
    userWantsSummary: true,
    ...extra,
  });

  it("skips when no summary is wanted, entries are empty, or no model exists", async () => {
    const t = await bootstrap();
    expect(await t.callHook("session_before_tree", { preparation: prep({ userWantsSummary: false }) })).toBeUndefined();
    expect(await t.callHook("session_before_tree", { preparation: prep({ entriesToSummarize: [] }) })).toBeUndefined();
    expect(await t.callHook("session_before_tree", { preparation: prep() }, { model: null })).toBeUndefined();
    expect(t.mf.calls).toHaveLength(0);
  });

  it("queues raw branch data, stores a reflected summary, and merges similar patterns", async () => {
    const t = await bootstrap({
      defaults: {
        conclusionsQuery: [{ id: "d1", content: "earlier branch pattern text", metadata: { branch_id: "b9" } }],
      },
    });
    const result = await t.callHook("session_before_tree", { preparation: prep() });
    const summary = result.summary;

    const treeMsgs = allMsgs(t.mf);
    expect(treeMsgs).toHaveLength(2);
    expect(treeMsgs[0].metadata).toMatchObject({ type: "obs_tree", raw: true });
    expect(treeMsgs[0].content).toBe(prep().entriesToSummarize.map((e: any) => JSON.stringify(e)).join("\n"));
    expect(treeMsgs[1].metadata).toMatchObject({ type: "obs_tree", level: "inductive" });
    expect(treeMsgs[0].metadata.branch_id).toBe(treeMsgs[1].metadata.branch_id);
    expect(treeMsgs[1].metadata.branch_id).toMatch(/^branch-\d+-[a-z0-9]{5}$/);

    expect(msgPosts(t.mf)).toHaveLength(1);
    expect(summary.summary).toContain("## Observations\n- 🔴 🔴 Keep branch state intact");
    expect(summary.summary).toContain("## Branch File Ops\n<read-files>\none.txt\n</read-files>");
    expect(summary.summary).toContain("## Similar Branch Patterns\n- Pattern from b9: earlier branch pattern text");

    expect(summary.details).toMatchObject({
      strategy: "honcho-obs-tree",
      honcho_hits: 1,
      entries_summarized: 2,
    });
  });

  it("appends no similar patterns when the query 404s", async () => {
    const t = await bootstrap({ defaults: { conclusionsQuery: "404" } });
    const result = await t.callHook("session_before_tree", { preparation: prep() });
    expect(result.summary.summary).not.toContain("Similar Branch Patterns");
    expect(result.summary.details.honcho_hits).toBe(0);
  });

  it("accumulates branch summaries in the overlay and flushes them on shutdown", async () => {
    const t = await bootstrap();
    await t.callHook("session_before_tree", { preparation: prep() });
    await t.callHook("session_before_tree", { preparation: prep() });

    t.mf.calls.length = 0;
    await t.callHook("session_shutdown", {});

    const shutdownMsgs = allMsgs(t.mf);
    expect(shutdownMsgs).toHaveLength(1);
    expect(shutdownMsgs[0].metadata).toEqual({ type: "obs_summary", level: "synthesis" });
    // the overlay keeps both branch summaries, joined by newlines
    expect(shutdownMsgs[0].content.split("\n\n").length).toBeGreaterThanOrEqual(2);
  });
});