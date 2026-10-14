/**
 * A recording mock for globalThis.fetch.
 *
 * Routes are checked latest-first so tests can override earlier (default) routes.
 * Unmatched calls answer HTTP 200 with an empty JSON body.
 */

export interface FetchCall {
  url: string;
  method: string;
  /** Parsed JSON body string when possible; otherwise the raw string. */
  body: any;
  init?: RequestInit;
  t: number;
}

type Route = { test: (c: FetchCall) => boolean; respond: (c: FetchCall) => Response };

let globalOriginal: typeof fetch | undefined;
let activeMock: MockFetch | undefined;

export class MockFetch {
  calls: FetchCall[] = [];
  private routes: Route[] = [];

  install(): this {
    if (activeMock && activeMock !== this) activeMock.restore();
    if (globalOriginal === undefined) globalOriginal = globalThis.fetch;
    activeMock = this;
    const self = this;
    const mocked = async (input: any, init?: any): Promise<Response> => {
      const url = input instanceof Request ? input.url : String(input);
      const method = String(init?.method ?? input?.method ?? "GET").toUpperCase();
      let body: any;
      if (typeof init?.body === "string") {
        try {
          body = JSON.parse(init.body);
        } catch {
          body = init.body;
        }
      }
      const call: FetchCall = { url, method, body, init, t: Date.now() };
      self.calls.push(call);
      for (const route of self.routes) {
        if (route.test(call)) return route.respond(call);
      }
      return MockFetch.json({});
    };
    (globalThis as any).fetch = mocked;
    return this;
  }

  restore() {
    if (globalOriginal && activeMock === this) {
      (globalThis as any).fetch = globalOriginal;
      activeMock = undefined;
    }
  }

  /** Register a route; latest registrations win (routes are checked newest-first). */
  on(test: (c: FetchCall) => boolean, respond: (c: FetchCall) => Response) {
    this.routes.unshift({ test, respond });
  }

  static json(value: any, status = 200): Response {
    return new Response(JSON.stringify(value), {
      status,
      headers: { "content-type": "application/json" },
    });
  }

  /** Turn a route option into a Response: raw Response passthrough, "404"/"500" helpers, else JSON. */
  static respondFor(v: any, emptyDefault: any): Response {
    if (v instanceof Response) return v;
    if (v === "404") return MockFetch.json({ error: "not found" }, 404);
    if (v === "500") return MockFetch.json({ error: "boom" }, 500);
    return MockFetch.json(v ?? emptyDefault);
  }

  bodyOf(c: FetchCall): any {
    return c.body;
  }

  callsWhere(sub: string): FetchCall[] {
    return this.calls.filter((c) => c.url.includes(sub));
  }

  /**
   * Standard route set for honcho.ts black-box tests with a static workspace.
   * Served URLs follow honchoFetch: {HONCHO_BASE_URL}/v3/{path}.
   */
  addHonchoDefaults(
    opts: {
      conclusionsQuery?: any;
      documentsSearch?: any;
      conclusionsList?: any;
      chat?: any;
    } = {},
  ) {
    let sessionN = 0;

    // ensureWorkspaceExists: POST /v3/workspaces
    this.on(
      (c) => c.method === "POST" && /\/v3\/workspaces\/?$/.test(c.url),
      () => MockFetch.json({ id: "ws-test" }),
    );

    // getOrCreateSession: POST /v3/workspaces/{ws}/sessions
    this.on(
      (c) => c.method === "POST" && /\/v3\/workspaces\/[^/]+\/sessions$/.test(c.url),
      () => MockFetch.json({ id: `sess-${++sessionN}` }),
    );

    // flushMessages: POST /v3/workspaces/{ws}/sessions/{id}/messages
    this.on(
      (c) => c.method === "POST" && /\/v3\/workspaces\/[^/]+\/sessions\/[^/]+\/messages$/.test(c.url),
      () => MockFetch.json({ results: [] }),
    );

    // conclusions/query (compaction, tree, search fallback)
    this.on((c) => c.url.endsWith("/conclusions/query"), () => MockFetch.respondFor(opts.conclusionsQuery, []));

    // documents/search (hybrid search)
    this.on((c) => c.url.endsWith("/documents/search"), () => MockFetch.respondFor(opts.documentsSearch, []));

    // conclusions/list (honcho_list_documents)
    this.on(
      (c) => c.url.endsWith("/conclusions/list"),
      () => MockFetch.respondFor(opts.conclusionsList, { items: [] }),
    );

    // dialectic chat
    this.on(
      (c) => c.url.includes("/peers/") && c.url.endsWith("/chat"),
      () => MockFetch.respondFor(opts.chat, { content: "dialectic-answer" }),
    );
  }
}