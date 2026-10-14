import { describe, it, expect, afterEach } from "bun:test";
import { bootstrap } from "./helpers/honcho-harness";
import { MockFetch } from "./helpers/mock-fetch";
import { drain } from "./helpers/env";

// Flush any setTimeout(0) timers created by a previous test so they cannot leak
// HTTP calls into the next test's MockFetch.
afterEach(async () => {
  await drain();
});

const WS = "http://honcho.test/v3/workspaces/ws-test";
const MSG_URL = /\/sessions\/sess-1\/messages$/;

function msgPosts(mf: MockFetch): any[] {
  return mf.calls.filter((c) => MSG_URL.test(c.url) && c.method === "POST");
}
function msgPostsAnySession(mf: MockFetch): any[] {
  return mf.calls.filter((c) => /\/sessions\/[^/]+\/messages$/.test(c.url) && c.method === "POST");
}
function sessionCreates(mf: MockFetch): any[] {
  return mf.calls.filter((c) => /\/v3\/workspaces\/[^/]+\/sessions$/.test(c.url) && c.method === "POST");
}
function allMsgs(mf: MockFetch): any[] {
  return msgPosts(mf).flatMap((c) => c.body.messages);
}

/* ------------------------------------------------------------------ */
/* commands                                                            */
/* ------------------------------------------------------------------ */

describe("honcho extension commands", () => {
  it("honcho-status reports no session when none exists", async () => {
    const t = await bootstrap();
    await t.runCommand("honcho-status", "");
    const note = t.app.notifications[0];
    expect(note.message).toBe("No active session\nAPI: http://honcho.test\nWorkspace: ws-test");
    expect(note.level).toBe("info");
  });

  it("honcho-status reports the current session and pending count", async () => {
    const t = await bootstrap();
    await t.callHook("before_agent_start", { prompt: "first" }); // creates sess-1, flushes
    await drain();
    await t.callHook("turn_start", { turnIndex: 1 }); // queue without flushing
    await t.callHook("turn_start", { turnIndex: 2 });
    await t.runCommand("honcho-status", "");
    const note = t.app.notifications[0];
    expect(note.message).toBe("Session: sess-1 (2 pending)\nAPI: http://honcho.test\nWorkspace: ws-test");
  });

  it("honcho-flush sends queued messages and notifies with the flushed count", async () => {
    const t = await bootstrap();
    await t.callHook("turn_start", { turnIndex: 0 });
    await t.callHook("turn_start", { turnIndex: 1 });
    expect(t.mf.callsWhere("/messages")).toHaveLength(0); // turn_start never flushes

    await t.runCommand("honcho-flush", "");
    expect(t.app.notifications[0].message).toBe("Flushed 2 messages");
    expect(t.app.notifications[0].level).toBe("success");
    expect(msgPosts(t.mf)).toHaveLength(1);
    expect(allMsgs(t.mf).map((m) => m.content)).toEqual(["Starting turn 0", "Starting turn 1"]);

    await t.runCommand("honcho-status", "");
    expect(t.app.notifications[1].message).toBe("Session: sess-1\nAPI: http://honcho.test\nWorkspace: ws-test");
  });

  it("honcho-flush on an empty queue does not touch the API", async () => {
    const t = await bootstrap();
    await t.runCommand("honcho-flush", "");
    expect(t.app.notifications[0].message).toBe("Flushed 0 messages");
    expect(t.mf.calls).toHaveLength(0);
  });
});

/* ------------------------------------------------------------------ */
/* honcho_store                                                        */
/* ------------------------------------------------------------------ */

describe("honcho_store tool", () => {
  it("stores a message with explicit peer and metadata", async () => {
    const t = await bootstrap();
    const result = await t.executeTool("honcho_store", {
      peer_id: "colleague",
      content: "remember this",
      metadata: { source: "test" },
    });
    expect(result.text).toBe("Message stored");

    expect(msgPosts(t.mf)).toHaveLength(1);
    expect(allMsgs(t.mf)).toEqual([
      { content: "remember this", peer_id: "colleague", metadata: { source: "test" } },
    ]);
  });

  it("defaults peer_id to the configured user", async () => {
    const t = await bootstrap();
    await t.executeTool("honcho_store", { content: "note" });
    expect(allMsgs(t.mf)[0].peer_id).toBe("user-t");
  });

  it("works without metadata", async () => {
    const t = await bootstrap();
    const result = await t.executeTool("honcho_store", { content: "note", peer_id: "p" });
    expect(result.text).toBe("Message stored");
    expect(allMsgs(t.mf)[0].metadata).toEqual({});
  });
});

/* ------------------------------------------------------------------ */
/* honcho_chat                                                         */
/* ------------------------------------------------------------------ */

describe("honcho_chat tool (dialectic)", () => {
  it("queries the configured user peer with stream=false", async () => {
    const t = await bootstrap();
    const result = await t.executeTool("honcho_chat", { query: "who am I?", reasoning_level: "high" });
    expect(result.text).toBe("dialectic-answer");
    expect(result.details).toEqual({ content: "dialectic-answer" });

    const call = t.mf.calls.find((c) => c.url.endsWith("/chat"))!;
    expect(call.url).toBe(`${WS}/peers/user-t/chat`);
    expect(call.body).toEqual({
      query: "who am I?",
      reasoning_level: "high",
      stream: false,
      session_id: null,
    });
  });

  it("includes the current session id once a session exists", async () => {
    const t = await bootstrap();
    await t.callHook("before_agent_start", { prompt: "hi" });
    await drain();
    t.mf.calls.length = 0;

    await t.executeTool("honcho_chat", { query: "still there?" });
    const call = t.mf.calls.find((c) => c.url.endsWith("/chat"))!;
    expect(call.body.session_id).toBe("sess-1");
  });

  it("surfaces API failures as Error text", async () => {
    const t = await bootstrap({ defaults: { chat: new Response("boom", { status: 500 }) } });
    const result = await t.executeTool("honcho_chat", { query: "q" });
    expect(result.text).toBe("Error: Honcho API error: 500 - boom");
  });

  it("falls back to 'No results' when the response has no content", async () => {
    const t = await bootstrap({ defaults: { chat: MockFetch.json({ unexpected: true }) } });
    const result = await t.executeTool("honcho_chat", { query: "q" });
    expect(result.text).toBe("No results");
  });
});

/* ------------------------------------------------------------------ */
/* honcho_list_documents                                               */
/* ------------------------------------------------------------------ */

describe("honcho_list_documents tool", () => {
  const docs = [
    { id: "d1", created_at: "2026-01-01", content: "alpha content" },
    { id: "d2", created_at: "2026-01-02", content: "beta   spaced\n\ncontent" },
    { id: "d3", created_at: "2026-01-03", content: "gamma" },
  ];

  it("lists documents with default filters", async () => {
    const t = await bootstrap({ defaults: { conclusionsList: MockFetch.json({ items: docs }) } });
    const result = await t.executeTool("honcho_list_documents", {});
    expect(result.text).toBe(
      "3 documents total, showing 3:\n- d1 (2026-01-01): alpha content...\n- d2 (2026-01-02): beta spaced content...\n- d3 (2026-01-03): gamma...",
    );

    const call = t.mf.calls.find((c) => c.url.endsWith("/conclusions/list"))!;
    expect(call.url).toBe(`${WS}/conclusions/list`);
    expect(call.body).toEqual({
      filters: { observer_id: "agent-t", observed_id: "user-t" },
    });
  });

  it("respects explicit observer/observed/limit and caps displayed items", async () => {
    const t = await bootstrap({ defaults: { conclusionsList: MockFetch.json({ items: docs }) } });
    const result = await t.executeTool("honcho_list_documents", {
      observer: "obs-1",
      observed: "target-1",
      limit: 1,
    });
    expect(result.text).toBe("3 documents total, showing 1:\n- d1 (2026-01-01): alpha content...");
    const call = t.mf.calls.find((c) => c.url.endsWith("/conclusions/list"))!;
    expect(call.body).toEqual({ filters: { observer_id: "obs-1", observed_id: "target-1" } });
  });

  it("truncates long content to 100 chars with whitespace collapsed", async () => {
    const long = "x".repeat(150) + " " + "y".repeat(50);
    const t = await bootstrap({ defaults: { conclusionsList: MockFetch.json({ items: [{ id: "L", created_at: "t", content: long }] }) } });
    const result = await t.executeTool("honcho_list_documents", {});
    expect(result.text).toBe(
      `1 documents total, showing 1:\n- L (t): ${"x".repeat(100)}...`,
    );
  });

  it("reports zero documents when the response has no items", async () => {
    const t = await bootstrap();
    const result = await t.executeTool("honcho_list_documents", {});
    expect(result.text).toBe("0 documents total, showing 0:\n");
  });

  it("surfaces API errors as Error text", async () => {
    const t = await bootstrap({ defaults: { conclusionsList: MockFetch.json({ error: "boom" }, 500) } });
    const result = await t.executeTool("honcho_list_documents", {});
    expect(result.text).toBe("Error: Honcho API error: 500 - {\"error\":\"boom\"}");
  });
});

/* ------------------------------------------------------------------ */
/* honcho_search_documents                                             */
/* ------------------------------------------------------------------ */

describe("honcho_search_documents tool (hybrid search)", () => {
  const docs = [
    { id: "s1", content: "first search hit", distance: 0.3 },
    { id: "s2", content: "second hit" },
  ];

  it("searches with defaults and formats scores from distance", async () => {
    const t = await bootstrap({ defaults: { documentsSearch: MockFetch.json(docs) } });
    const result = await t.executeTool("honcho_search_documents", { query: "hello" });
    expect(result.text).toBe(
      "Found 2 documents:\n\n1. [Score: 0.70] (s1): first search hit...\n\n2. [Score: N/A] (s2): second hit...",
    );

    const call = t.mf.calls.find((c) => c.url.endsWith("/documents/search"))!;
    expect(call.url).toBe(`${WS}/documents/search`);
    expect(call.body).toEqual({
      query: "hello",
      top_k: 10,
      observer: "agent-t",
      observed: "user-t",
      distance: 0.7,
    });
  });

  it("respects the limit parameter", async () => {
    const t = await bootstrap({ defaults: { documentsSearch: MockFetch.json(docs) } });
    await t.executeTool("honcho_search_documents", { query: "hello", limit: 3 });
    const call = t.mf.calls.find((c) => c.url.endsWith("/documents/search"))!;
    expect(call.body.top_k).toBe(3);
  });

  it("shows N/A when distance is missing", async () => {
    const t = await bootstrap({ defaults: { documentsSearch: MockFetch.json([{ id: "x", content: "c" }]) } });
    const result = await t.executeTool("honcho_search_documents", { query: "q" });
    expect(result.text).toContain("[Score: N/A]");
  });

  it("reports an empty result set", async () => {
    const t = await bootstrap({ defaults: { documentsSearch: MockFetch.json([]) } });
    const result = await t.executeTool("honcho_search_documents", { query: "q" });
    expect(result.text).toBe("No documents found matching your query.");
  });

  it("falls back to conclusions/query with the same params on 404", async () => {
    const t = await bootstrap({
      defaults: {
        documentsSearch: MockFetch.json({ error: "not found" }, 404),
        conclusionsQuery: MockFetch.json([{ id: "f1", content: "fallback hit", distance: 0.5 }]),
      },
    });
    const result = await t.executeTool("honcho_search_documents", { query: "q", limit: 4, distance: 0.9 });
    expect(result.text).toBe(
      "Search complete (semantic only). Found 1 documents:\n\n1. [Score: 0.50] (f1): fallback hit...",
    );

    const fallback = t.mf.calls.filter((c) => c.url.endsWith("/conclusions/query"))[0];
    expect(fallback.body).toEqual({ query: "q", top_k: 4, observer: "agent-t", observed: "user-t", distance: 0.9 });
  });

  it("reports fallback errors", async () => {
    const t = await bootstrap({
      defaults: {
        documentsSearch: MockFetch.json({ error: "not found" }, 404),
        conclusionsQuery: MockFetch.json({ error: "boom" }, 500),
      },
    });
    const result = await t.executeTool("honcho_search_documents", { query: "q" });
    expect(result.text).toBe("Search API error: Honcho API error: 500 - {\"error\":\"boom\"}");
  });

  it("surfaces non-404 search failures as Error text", async () => {
    const t = await bootstrap({ defaults: { documentsSearch: MockFetch.json({ error: "boom" }, 500) } });
    const result = await t.executeTool("honcho_search_documents", { query: "q" });
    expect(result.text).toBe("Error: Honcho API error: 500 - {\"error\":\"boom\"}");
  });
});

/* ------------------------------------------------------------------ */
/* flush pipeline                                                      */
/* ------------------------------------------------------------------ */

describe("honcho extension flush pipeline", () => {
  it("batches messages in groups of five", async () => {
    const t = await bootstrap();
    for (let i = 0; i < 11; i++) await t.callHook("turn_start", { turnIndex: i });

    await t.runCommand("honcho-flush", "");
    const posts = msgPosts(t.mf);
    expect(posts).toHaveLength(3); // 11 messages -> 5 + 5 + 1
    expect(posts[0].body.messages.map((m: any) => m.content)).toEqual([
      "Starting turn 0", "Starting turn 1", "Starting turn 2", "Starting turn 3", "Starting turn 4",
    ]);
    expect(posts[1].body.messages).toHaveLength(5);
    expect(posts[2].body.messages).toHaveLength(1);
    expect(posts[2].body.messages[0].content).toBe("Starting turn 10");
  });

  it("requeues failed non-chunk messages and delivers them on the next flush", async () => {
    const t = await bootstrap();
    // All message batches fail initially.
    t.mf.on(
      (c) => /\/messages$/.test(c.url) && c.method === "POST",
      () => MockFetch.json({ error: "overloaded" }, 500),
    );

    for (let i = 0; i < 6; i++) await t.callHook("turn_start", { turnIndex: i });
    await t.runCommand("honcho-flush", ""); // both batches fail
    expect(t.app.notifications[0].message).toBe("Flushed 6 messages"); // count is pre-flush queue length

    // Deliver the requeued messages on a later flush.
    t.mf.on((c) => /\/messages$/.test(c.url) && c.method === "POST", () => MockFetch.json({ results: [] }));
    await t.callHook("turn_start", { turnIndex: 99 }); // one more queued message
    await t.runCommand("honcho-flush", "");
    const posts = msgPosts(t.mf).filter((c) => c.body.messages?.length !== undefined);

    const successful = posts.filter((c) => Array.isArray(c.body.messages) && c.body.messages.length > 0);
    // successful posts: the last flush's batches (7 msgs -> 5 + 2)
    expect(successful.at(-2)!.body.messages).toHaveLength(5);
    expect(successful.at(-1)!.body.messages).toHaveLength(2);
  });

  it("does not requeue chunked messages after a batch failure", async () => {
    const t = await bootstrap();
    t.mf.on((c) => /\/messages$/.test(c.url) && c.method === "POST", () => new Response("fail", { status: 500 }));

    // one oversized message (split into 3 chunks) plus two normal ones
    await t.callHook("turn_start", { turnIndex: 0 });
    await t.callHook("turn_start", { turnIndex: 1 });
    const big = `${"x".repeat(7900)}\n\n${"y".repeat(7900)}\n\n${"z".repeat(4100)}`;
    await t.callHook("tool_result", { toolName: "reader", isError: false, result: { content: big } });
    await t.runCommand("honcho-flush", "");

    const posts = msgPosts(t.mf);
    expect(posts).toHaveLength(1);
    expect(posts[0].body.messages).toHaveLength(5); // 2 normal + 3 chunks, all in one batch
    const chunks = posts[0].body.messages.filter((m: any) => m.metadata.is_chunk);
    const prefix = "Observation (reader):\n";
    expect(chunks).toHaveLength(3);
    expect(chunks.map((m: any) => m.metadata.chunk_index)).toEqual([1, 2, 3]);
    expect(chunks.every((m: any) => m.metadata.total_chunks === 3)).toBe(true);
    expect(chunks.every((m: any) => m.metadata.original_length === prefix.length + big.length)).toBe(true);

    // failure -> only the two normal messages are requeued
    t.mf.on((c) => /\/messages$/.test(c.url) && c.method === "POST", () => MockFetch.json({ results: [] }));
    await t.callHook("turn_start", { turnIndex: 2 }); // one extra to force a non-empty flush
    await t.runCommand("honcho-flush", "");
    const retry = msgPosts(t.mf).at(-1)!.body.messages;
    expect(retry).toHaveLength(3); // 2 requeued + 1 new, no chunk remnants
    expect(retry.every((m: any) => !m.metadata.is_chunk)).toBe(true);
  });

  it("keeps the queue intact when session creation fails", async () => {
    const t = await bootstrap();
    t.mf.on(
      (c) => /\/sessions$/.test(c.url) && c.method === "POST",
      () => MockFetch.json({ error: "down" }, 500),
    );
    await t.callHook("turn_start", { turnIndex: 0 });
    await t.callHook("turn_start", { turnIndex: 1 });
    await t.runCommand("honcho-flush", "");
    expect(t.mf.callsWhere("/messages")).toHaveLength(0);

    await t.runCommand("honcho-status", "");
    expect(t.app.notifications.at(-1)!.message).toContain("(2 pending)");

    t.mf.on((c) => /\/sessions$/.test(c.url) && c.method === "POST", () => MockFetch.json({ id: "sess-9" }));
    await t.runCommand("honcho-flush", "");
    expect(msgPostsAnySession(t.mf)).toHaveLength(1);
    expect(msgPostsAnySession(t.mf)[0].url).toContain("/sessions/sess-9/messages");
    expect(msgPostsAnySession(t.mf)[0].body.messages).toHaveLength(2);
  });

  it("reuses one session across many flushes", async () => {
    const t = await bootstrap();
    await t.executeTool("honcho_store", { content: "one" });
    await t.executeTool("honcho_store", { content: "two" });
    await t.executeTool("honcho_store", { content: "three" });

    expect(sessionCreates(t.mf)).toHaveLength(1); // single session create
    expect(msgPosts(t.mf)).toHaveLength(3);
    expect(msgPosts(t.mf).every((c) => c.url.includes("/sessions/sess-1/messages"))).toBe(true);
    // separate single-message posts, not a merged batch
    expect(msgPosts(t.mf).map((c) => c.body.messages[0].content)).toEqual(["one", "two", "three"]);
  });

  it("flushes automatically on turn_end and agent_end via setTimeout(0)", async () => {
    const t = await bootstrap();
    await t.callHook("before_agent_start", { prompt: "seed" });
    await drain();
    t.mf.calls.length = 0;

    await t.callHook("turn_start", { turnIndex: 0 });
    await t.callHook("turn_end", { turnIndex: 0, message: { role: "assistant", content: "reply text" } });
    await drain();
    expect(msgPosts(t.mf)).toHaveLength(1);
    expect(allMsgs(t.mf).map((m) => m.content)).toEqual(["Starting turn 0", "reply text"]);
    expect(allMsgs(t.mf)[1].metadata).toMatchObject({ role: "assistant", type: "final", turn_index: 0 });

    await t.callHook("turn_start", { turnIndex: 1 });
    await t.callHook("agent_end", {});
    await drain();
    expect(msgPosts(t.mf)).toHaveLength(2);
    expect(allMsgs(t.mf).at(-1)!.content).toBe("Starting turn 1");
  });

  it("before_agent_start flushes the user prompt asynchronously", async () => {
    const t = await bootstrap();
    await t.callHook("before_agent_start", { prompt: "my question", images: [{ type: "image" }] });
    expect(msgPosts(t.mf)).toHaveLength(0); // queued, not yet flushed

    await drain();
    const post = msgPosts(t.mf)[0];
    expect(post.body.messages).toEqual([
      {
        content: "my question",
        peer_id: "user-t",
        metadata: { role: "user", type: "prompt", has_images: true, intended_model: "test-provider/test-model" },
      },
    ]);
  });
});