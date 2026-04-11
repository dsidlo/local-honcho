import { promises as fs } from "node:fs";
import { basename, extname, resolve } from "node:path";
import { homedir } from "node:os";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const execFileAsync = promisify(execFile);

// Configuration
const HONCHO_BASE_URL = process.env.HONCHO_BASE_URL || "http://localhost:8300";
const HONCHO_WORKSPACE = process.env.HONCHO_WORKSPACE || "default";
const HONCHO_USER = process.env.HONCHO_USER || "dsidlo";
const DEFAULT_OBSERVER = "agent-pi-mono";
const MAX_CHUNK_SIZE = 4000;
const LITEPARSE_PATH = "/home/dsidlo/.pi/agent/skills/liteparse/parse.js";

interface UploadOptions {
  filePath?: string;
  content?: string;
  name?: string;
  category?: string;
  subcategory?: string;
  observerId?: string;
  workspace?: string;
}

interface UploadResult {
  peerId: string;
  chunkCount: number;
  conclusionIds: string[];
  sessionId: string;
}

/**
 * Expand ~ in paths to home directory
 */
function expandPath(inputPath: string): string {
  if (inputPath.startsWith("~/")) {
    return join(homedir(), inputPath.slice(2));
  }
  return resolve(inputPath);
}

/**
 * Check if file is PDF by extension
 */
function isPdf(filePath: string): boolean {
  return extname(filePath).toLowerCase() === ".pdf";
}

/**
 * Extract text from PDF using LiteParse
 */
async function extractPdfText(filePath: string): Promise<string> {
  try {
    // Use LiteParse with OCR (default for PDFs)
    const { stdout } = await execFileAsync("node", [LITEPARSE_PATH, filePath], {
      timeout: 60000, // 60 second timeout
      maxBuffer: 50 * 1024 * 1024, // 50MB buffer
    });
    return stdout;
  } catch (error: any) {
    // If OCR fails, try without OCR
    try {
      const { stdout } = await execFileAsync(
        "node",
        [LITEPARSE_PATH, "--no-ocr", filePath],
        { timeout: 30000, maxBuffer: 50 * 1024 * 1024 }
      );
      return stdout;
    } catch (fallbackError: any) {
      throw new Error(
        `Failed to extract PDF text: ${error.message}. Fallback also failed: ${fallbackError.message}`
      );
    }
  }
}

/**
 * Read file content (handles PDFs automatically)
 */
async function getFileContent(filePath: string): Promise<string> {
  const expanded = expandPath(filePath);
  
  if (isPdf(expanded)) {
    console.log(`[honcho-upload] PDF detected, extracting text with LiteParse...`);
    return extractPdfText(expanded);
  }
  
  // Regular text file
  return fs.readFile(expanded, "utf-8");
}

/**
 * Make Honcho API request
 */
async function honchoFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${HONCHO_BASE_URL}/v3${path}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Honcho API error: ${response.status} - ${text}`);
  }

  return response.json();
}

/**
 * Create/get peer in Honcho
 */
async function ensurePeer(
  workspace: string,
  peerId: string,
  metadata?: Record<string, any>
): Promise<string> {
  try {
    const result = await honchoFetch<{ id: string }>(
      `/workspaces/${workspace}/peers`,
      {
        method: "POST",
        body: JSON.stringify({
          name: peerId,
          metadata: metadata || { type: "document" },
        }),
      }
    );
    return result.id;
  } catch (error: any) {
    // Peer may already exist
    if (error.message.includes("already exists") || error.message.includes("409")) {
      return peerId;
    }
    throw error;
  }
}

/**
 * Create session for document upload
 */
async function createSession(
  workspace: string,
  documentName: string,
  userId: string,
  observerId: string
): Promise<string> {
  const sessionId = `doc-${Date.now()}-${documentName.slice(0, 20).replace(/[^a-zA-Z0-9]/g, "-")}`;
  
  await honchoFetch(`/workspaces/${workspace}/sessions`, {
    method: "POST",
    body: JSON.stringify({
      id: sessionId,
      peers: {
        [userId]: {},
        [observerId]: {},
      },
    }),
  });
  
  return sessionId;
}

/**
 * Split content into chunks at paragraph boundaries
 */
function chunkContent(content: string, maxChunkSize = MAX_CHUNK_SIZE): string[] {
  const paragraphs = content.split(/\n\n+/).filter((p) => p.trim());
  const chunks: string[] = [];
  let currentChunk = "";

  for (const para of paragraphs) {
    // If single paragraph exceeds limit, split by sentences
    if (para.length > maxChunkSize) {
      if (currentChunk) {
        chunks.push(currentChunk.trim());
        currentChunk = "";
      }

      // Split by sentences
      const sentences = para.match(/[^.!?]+[.!?]+["']?\s*/g) || [para];
      for (const sentence of sentences) {
        if ((currentChunk + sentence).length > maxChunkSize) {
          if (currentChunk) chunks.push(currentChunk.trim());
          currentChunk = sentence;
        } else {
          currentChunk += sentence;
        }
      }
    } else {
      // Paragraph fits
      if ((currentChunk + "\n\n" + para).length > maxChunkSize) {
        if (currentChunk) chunks.push(currentChunk.trim());
        currentChunk = para;
      } else {
        currentChunk += (currentChunk ? "\n\n" : "") + para;
      }
    }
  }

  if (currentChunk) chunks.push(currentChunk.trim());
  return chunks.length > 0 ? chunks : [content.slice(0, maxChunkSize)];
}

/**
 * Upload chunks as conclusions to Honcho
 */
async function uploadChunks(
  workspace: string,
  sessionId: string,
  peerId: string,
  observerId: string,
  chunks: string[]
): Promise<string[]> {
  const conclusions = chunks.map((chunk, idx) => ({
    content: `[Part ${idx + 1}/${chunks.length} | Document]\n\n${chunk}`,
    observer_id: observerId,
    observed_id: peerId,
    session_id: sessionId,
  }));

  const result = await honchoFetch<Array<{ id: string }>>(
    `/workspaces/${workspace}/conclusions`,
    {
      method: "POST",
      body: JSON.stringify({ conclusions }),
    }
  );

  return result.map((c) => c.id);
}

/**
 * Main upload function
 */
export async function uploadDocument(options: UploadOptions): Promise<UploadResult> {
  // Validate inputs
  if (!options.filePath && !options.content) {
    throw new Error("Either filePath or content must be provided");
  }

  // Get content
  let content: string;
  let docName: string;

  if (options.content) {
    content = options.content;
    docName = options.name || "untitled";
  } else {
    const expandedPath = expandPath(options.filePath!);
    content = await getFileContent(expandedPath);
    docName = options.name || basename(expandedPath, extname(expandedPath));
  }

  // Sanitize names
  const sanitizedName = docName.replace(/[^a-zA-Z0-9_-]/g, "-").slice(0, 100);
  const peerId = sanitizedName;
  const workspace = options.workspace || HONCHO_WORKSPACE;
  const observerId = options.observerId || DEFAULT_OBSERVER;

  console.log(`[honcho-upload] Uploading "${docName}" to workspace "${workspace}"...`);

  // Step 1: Ensure peer exists
  const metadata: Record<string, any> = {
    type: "document",
    uploaded_at: new Date().toISOString(),
  };
  if (options.category) metadata.category = options.category;
  if (options.subcategory) metadata.subcategory = options.subcategory;

  await ensurePeer(workspace, peerId, metadata);

  // Step 2: Create session
  const sessionId = await createSession(workspace, sanitizedName, HONCHO_USER, observerId);

  // Step 3: Chunk and upload
  const chunks = chunkContent(content);
  console.log(`[honcho-upload] Chunked into ${chunks.length} parts`);

  const conclusionIds = await uploadChunks(
    workspace,
    sessionId,
    peerId,
    observerId,
    chunks
  );

  console.log(`[honcho-upload] Created ${conclusionIds.length} conclusions`);

  return {
    peerId,
    chunkCount: chunks.length,
    conclusionIds,
    sessionId,
  };
}

// CLI interface for testing
if (import.meta.main) {
  const filePath = process.argv[2];
  if (!filePath) {
    console.error("Usage: tsx upload.ts <file-path> [name]");
    process.exit(1);
  }

  uploadDocument({
    filePath,
    name: process.argv[3],
  })
    .then((result) => {
      console.log("\n✓ Upload successful!");
      console.log(`  Peer ID: ${result.peerId}`);
      console.log(`  Chunks: ${result.chunkCount}`);
      console.log(`  Conclusions: ${result.conclusionIds.length}`);
    })
    .catch((error) => {
      console.error("\n✗ Upload failed:", error.message);
      process.exit(1);
    });
}