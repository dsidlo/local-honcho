import { describe, it, expect, beforeAll, afterAll } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import { join } from "node:path";
import { MockFetch } from "./helpers/mock-fetch";
import { importFresh, UPLOAD_SRC, applyEnv, clearHonchoEnv } from "./helpers/env";

/**
 * Tests for the honcho-upload-document CLI module. Covers the whole uploadDocument flow
 * (validation, path expansion, name derivation/sanitization, PDF extraction via real
 * LiteParse subprocesses, peer/session/conclusions HTTP calls, and chunking) plus the
 * error paths. PDF cases use real subprocesses against LiteParse (~1-5s each).
 */

const tempDirs: string[] = [];
function sandboxDir(prefix = "honcho-up-"): string {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  tempDirs.push(dir);
  return dir;
}

afterAll(() => {
  for (const dir of tempDirs) {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  }
  clearHonchoEnv();
});

// A minimal valid one-page PDF; LiteParse extracts its single text run.
const MINI_PDF = `%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj
4 0 obj<</Length 45>>stream
BT /F1 24 Tf 72 700 Td (Hello Honcho) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f
trailer<</Size 6/Root 1 0 R>>
%%EOF
`;

let mod: any;
let mf: MockFetch;
const peerBehavior = { status: 200, body: "peer-x" };

beforeAll(async () => {
  applyEnv(); // clear HONCHO_* -> upload defaults (localhost:8300, "default", "dsidlo")
  mod = await importFresh(UPLOAD_SRC);
});

function installDefaultRoutes() {
  mf = new MockFetch().install();
  peerBehavior.status = 200;
  peerBehavior.body = "peer-x";
  mf.on(
    (c) => c.method === "POST" && /\/v3\/workspaces\/[^/]+\/peers$/.test(c.url),
    () =>
      peerBehavior.status === 200
        ? MockFetch.json({ id: peerBehavior.body })
        : new Response(peerBehavior.body, { status: peerBehavior.status }),
  );
  mf.on(
    (c) => c.method === "POST" && /\/v3\/workspaces\/[^/]+\/sessions$/.test(c.url),
    () => MockFetch.json({ id: "doc-sess" }),
  );
  mf.on(
    (c) => c.method === "POST" && /\/conclusions$/.test(c.url),
    (c) => MockFetch.json((c.body?.conclusions ?? []).map((_b: any, i: number) => ({ id: `c${i + 1}` }))),
  );
}

const peerCalls = () => mf.calls.filter((c) => /\/peers$/.test(c.url));
const sessionCalls = () => mf.calls.filter((c) => /\/sessions$/.test(c.url));
const conclusionsCalls = () => mf.calls.filter((c) => /\/conclusions$/.test(c.url));

/* ------------------------------------------------------------------ */

describe("uploadDocument: validation and basic flow", () => {
  it("rejects calls without filePath or content", async () => {
    installDefaultRoutes();
    await expect(mod.uploadDocument({})).rejects.toThrow("Either filePath or content must be provided");
  });

  it("uploads inline content with a derived and sanitized peer name", async () => {
    installDefaultRoutes();
    const result = await mod.uploadDocument({ content: "hello world", name: "My Note!!" });

    // sanitization is a plain charset replace: non [a-zA-Z0-9_-] chars become "-", no collapse or trim
    expect(result.peerId).toBe("My-Note--");
    expect(result.chunkCount).toBe(1);
    expect(result.conclusionIds).toEqual(["c1"]);
    expect(result.sessionId).toMatch(/^doc-\d+-My-Note--$/);

    const peer = peerCalls()[0];
    expect(peer.url).toBe("http://localhost:8300/v3/workspaces/default/peers");
    expect(peer.body.name).toBe("My-Note--");
    expect(peer.body.metadata.type).toBe("document");
    expect(peer.body.metadata.uploaded_at).toMatch(/^\d{4}-\d{2}-\d{2}T/);

    const session = sessionCalls()[0];
    expect(session.url).toBe("http://localhost:8300/v3/workspaces/default/sessions");
    expect(Object.keys(session.body.peers)).toEqual(["dsidlo", "agent-pi-mono"]);
    expect(session.body.id).toMatch(/^doc-\d+-My-Note--$/);

    const concl = conclusionsCalls()[0];
    expect(concl.body.conclusions).toEqual([
      {
        content: "[Part 1/1 | Document]\n\nhello world",
        observer_id: "agent-pi-mono",
        observed_id: "My-Note--",
        session_id: result.sessionId,
      },
    ]);
  });

  it("passes category and subcategory into peer metadata", async () => {
    installDefaultRoutes();
    await mod.uploadDocument({ content: "x", name: "cat-doc", category: "reference", subcategory: "api" });
    expect(peerCalls()[0].body.metadata).toMatchObject({ category: "reference", subcategory: "api" });
  });

  it("reads text files and derives the name from the basename", async () => {
    installDefaultRoutes();
    const dir = sandboxDir();
    const file = join(dir, "my report.md");
    writeFileSync(file, "file body text");

    const result = await mod.uploadDocument({ filePath: file });
    expect(result.peerId).toBe("my-report");
    expect(conclusionsCalls()[0].body.conclusions[0].content).toContain("file body text");
  });

  it("expands ~/ paths against the user's home directory", async () => {
    installDefaultRoutes();
    // os.homedir() results are cached by the runtime, so mutate HOME instead of relying on it:
    // write a uniquely named file into the real home and reference it via ~/.
    require("node:fs").appendFileSync("/tmp/probe.log", `H1 homedir=${homedir()} TEST_HOME=${process.env.HONCHO_TEST_HOME}\n`);
    const nonce = `pi-mono-upload-test-${Date.now()}.txt`;
    require("node:fs").appendFileSync("/tmp/probe.log","H2\n");
    const homeFile = join(homedir(), nonce);
    writeFileSync(homeFile, "home note");
    try {
      require("node:fs").appendFileSync("/tmp/probe.log","H3\n");
    const result = await mod.uploadDocument({ filePath: `~/${nonce}` });
    require("node:fs").appendFileSync("/tmp/probe.log",`H4 ${JSON.stringify(result)}\n`);
      expect(result.peerId).toBe(nonce.replace(/\.[a-z]+$/, ""));
      expect(conclusionsCalls()[0].body.conclusions[0].content).toContain("home note");
    } finally {
      rmSync(homeFile, { force: true });
    }
  });

  it("honors workspace and observerId overrides", async () => {
    installDefaultRoutes();
    await mod.uploadDocument({ content: "y", name: "overrides", workspace: "cust-ws", observerId: "obs-x" });
    expect(peerCalls()[0].url).toBe("http://localhost:8300/v3/workspaces/cust-ws/peers");
    expect(conclusionsCalls()[0].body.conclusions[0].observer_id).toBe("obs-x");
  });
});

describe("uploadDocument: chunking", () => {
  it("merges small paragraphs and splits when the limit is exceeded", async () => {
    installDefaultRoutes();
    const paras = ["para-one ".repeat(40).trim(), "para-two ".repeat(40).trim(), "para-three ".repeat(40).trim()];
    const content = paras.join("\n\n"); // ~3000 chars total -> 2 chunks (limit 4000)

    const result = await mod.uploadDocument({ content, name: "chunked" });
    expect(result.chunkCount).toBe(1); // 3 ~1000-char paragraphs fit under 4000 after merge
    let merged = conclusionsCalls()[0].body.conclusions[0].content;
    expect(merged.startsWith("[Part 1/1 | Document]")).toBe(true);
    expect(merged).toContain("para-two");

    // Now push past 4000 -> split into two chunks
    const big = ["a ".repeat(1400).trim(), "b ".repeat(1400).trim(), "c ".repeat(200).trim()].join("\n\n");
    const result2 = await mod.uploadDocument({ content: big, name: "chunked2" });
    expect(result2.chunkCount).toBe(2);
    const body = conclusionsCalls().at(-1)!.body.conclusions;
    expect(body[0].content.startsWith("[Part 1/2 | Document]")).toBe(true);
    expect(body[1].content.startsWith("[Part 2/2 | Document]")).toBe(true);
    expect(body.every((c: any) => c.content.replace("[Part 1/2 | Document]\n\n", "").replace("[Part 2/2 | Document]\n\n", "").length <= 4000)).toBe(true);
  });

  it("splits a single oversized paragraph by sentences", async () => {
    installDefaultRoutes();
    const sentences = Array.from({ length: 120 }, (_, i) => `Sentence number ${i} says something moderately long here. `).join("");
    const result = await mod.uploadDocument({ content: sentences, name: "sentences" });
    expect(result.chunkCount).toBeGreaterThan(1);
    const body = conclusionsCalls().at(-1)!.body.conclusions;
    const total = body.reduce((acc: number, c: any) => acc + c.content.replace(/^\[Part \d+\/\d+ \| Document\]\n\n/, "").length, 0);
    expect(total).toBeGreaterThanOrEqual(sentences.length - 200); // no significant text loss
    expect(result.conclusionIds).toEqual(body.map((_c: any, i: number) => `c${i + 1}`));
  });

  it("falls back to a single empty chunk when the file is empty", async () => {
    installDefaultRoutes();
    const dir = sandboxDir();
    const file = join(dir, "empty.txt");
    writeFileSync(file, "");

    const result = await mod.uploadDocument({ filePath: file });
    expect(result.chunkCount).toBe(1);
    const body = conclusionsCalls()[0].body.conclusions;
    expect(body[0].content).toBe("[Part 1/1 | Document]\n\n");
  });
});

describe("uploadDocument: error paths", () => {
  it("reuses the peer when the API reports it already exists (409)", async () => {
    installDefaultRoutes();
    peerBehavior.status = 409;
    peerBehavior.body = "Peer already exists";
    const result = await mod.uploadDocument({ content: "dup", name: "dup-doc" });
    expect(result.peerId).toBe("dup-doc");
    expect(result.conclusionIds.length).toBe(1);
  });

  it("surfaces peer-creation failures", async () => {
    installDefaultRoutes();
    peerBehavior.status = 500;
    peerBehavior.body = "nope";
    await expect(mod.uploadDocument({ content: "x", name: "broken" })).rejects.toThrow(
      "Honcho API error: 500 - nope",
    );
  });

  it("extracts text from a valid PDF via LiteParse and uploads the result", async () => {
    installDefaultRoutes();
    const dir = sandboxDir();
    const pdf = join(dir, "mini.pdf");
    writeFileSync(pdf, MINI_PDF);

    const result = await mod.uploadDocument({ filePath: pdf });
    expect(result.peerId).toBe("mini");
    const sent = conclusionsCalls()[0].body.conclusions;
    expect(sent.length).toBeGreaterThanOrEqual(1);
    expect(sent.map((c: any) => c.content).join(" ")).toContain("Hello Honcho");
  }, 60000);

  it("composes a clear error when both OCR passes fail on a garbage PDF", async () => {
    installDefaultRoutes();
    const dir = sandboxDir();
    const pdf = join(dir, "garbage.pdf");
    writeFileSync(pdf, "This is not a real PDF. Just text pretending.");

    await expect(mod.uploadDocument({ filePath: pdf })).rejects.toThrow(
      /Failed to extract PDF text[\s\S]*Fallback also failed/,
    );
  }, 60000);
});

describe("uploadDocument: configuration", () => {
  it("picks up HONCHO_* env vars set before import", async () => {
    applyEnv({ HONCHO_BASE_URL: "http://up.test", HONCHO_WORKSPACE: "env-ws", HONCHO_USER: "env-user" });
    const modEnv = await importFresh(UPLOAD_SRC);
    installDefaultRoutes();

    await modEnv.uploadDocument({ content: "z", name: "env-doc" });
    expect(peerCalls()[0].url).toBe("http://up.test/v3/workspaces/env-ws/peers");
    expect(Object.keys(sessionCalls()[0].body.peers)).toEqual(["env-user", "agent-pi-mono"]);
  });
});