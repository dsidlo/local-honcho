import type { ExtensionAPI, ExtensionContext } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

// DEBUG LOGGING SETUP
const HONCHO_LOG_FILE = "/tmp/honcho.log";
let honchoLogBuffer: string[] = [];
let logFlushTimer: any = null;

async function flushHonchoLog() {
  try {
    const fs = await import("node:fs/promises");
    const content = honchoLogBuffer.join("");
    honchoLogBuffer = [];
    await fs.appendFile(HONCHO_LOG_FILE, content);
  } catch (e) {
    // Silent fail
  }
}

function honchoLog(level: "DEBUG" | "INFO" | "WARN" | "ERROR", msg: string, data?: any) {
  const ts = new Date().toISOString().slice(0, 19);
  let line = `[${ts}] [${level}] ${msg}`;
  if (data !== undefined) {
    const dataStr = typeof data === "object" ? JSON.stringify(data).slice(0, 500) : String(data);
    line += ` | data=${dataStr}`;
  }
  line += "\n";
  honchoLogBuffer.push(line);
  
  // Flush periodically
  if (honchoLogBuffer.length > 5) {
    flushHonchoLog();
  } else if (!logFlushTimer) {
    logFlushTimer = setTimeout(() => { flushHonchoLog(); logFlushTimer = null; }, 100);
  }
}

// Init log file on load
(async () => {
  try {
    const fs = await import("node:fs/promises");
    await fs.writeFile(HONCHO_LOG_FILE, `=== Honcho Extension Started at ${new Date().toISOString()} ===\n`);
  } catch (e) {}
})();

/**
 * Honcho Extension for pi-mono - FULL REASONING TRACE VERSION
 */

honchoLog("INFO", "Extension loading");

// Configuration interface for honcho.json
type HonchoConfig = {
  honcho?: {
    port?: number;
    base_url?: string;
    user?: string;
    agent_id?: string;
    peer_id?: string;
    workspace_mode?: "auto" | "static";
    workspace?: string;
  };
};

async function loadConfigFile(): Promise<HonchoConfig> {
  honchoLog("DEBUG", "loadConfigFile: starting");
  const configPaths = [
    join(process.cwd(), "honcho.json"),
    join(homedir(), ".pi", "honcho.json"),
    join(homedir(), ".config", "pi", "honcho.json"),
    join(homedir(), ".honcho.json"),
  ];

  for (const configPath of configPaths) {
    if (existsSync(configPath)) {
      try {
        const content = await readFile(configPath, "utf-8");
        const parsed = JSON.parse(content) as HonchoConfig;
        honchoLog("INFO", `loadConfigFile: loaded from ${configPath}`);
        return parsed;
      } catch (err: any) {
        honchoLog("ERROR", `loadConfigFile: failed to parse ${configPath}`, err.message);
      }
    }
  }

  honchoLog("WARN", "loadConfigFile: no config file found");
  return {};
}

// Load config file (sync for module initialization)
let fileConfig: HonchoConfig = {};
try {
  honchoLog("DEBUG", "Loading config sync");
  const { readFileSync } = await import("node:fs");
  const { join } = await import("node:path");
  const { homedir } = await import("node:os");
  
  const configPaths = [
    join(process.cwd(), "honcho.json"),
    join(homedir(), ".pi", "honcho.json"),
    join(homedir(), ".config", "pi", "honcho.json"),
    join(homedir(), ".honcho.json"),
  ];

  for (const configPath of configPaths) {
    if (existsSync(configPath)) {
      try {
        const content = readFileSync(configPath, "utf-8");
        fileConfig = JSON.parse(content) as HonchoConfig;
        honchoLog("INFO", `Config loaded from ${configPath}`);
        break;
      } catch (e: any) {
        honchoLog("ERROR", `Failed to parse ${configPath}`, e.message);
      }
    }
  }
} catch (e: any) {
  honchoLog("WARN", "Config file loading error", e.message);
}

// Build base URL from config
function buildBaseUrl(): string {
  if (process.env.HONCHO_BASE_URL) {
    honchoLog("DEBUG", "Using HONCHO_BASE_URL env var", process.env.HONCHO_BASE_URL);
    return process.env.HONCHO_BASE_URL;
  }
  if (fileConfig.honcho?.base_url) {
    honchoLog("DEBUG", "Using base_url from config", fileConfig.honcho.base_url);
    return fileConfig.honcho.base_url;
  }
  if (fileConfig.honcho?.port) {
    const url = `http://localhost:${fileConfig.honcho.port}`;
    honchoLog("DEBUG", "Using port from config", url);
    return url;
  }
  honchoLog("DEBUG", "Using default URL");
  return "http://localhost:8300";
}

// Configuration from environment or config file
const HONCHO_BASE_URL = buildBaseUrl();
const HONCHO_USER = process.env.HONCHO_USER || fileConfig.honcho?.user || "dsidlo";
const HONCHO_AGENT_ID = process.env.HONCHO_AGENT_ID || fileConfig.honcho?.agent_id || "agent-pi-mono";
const HONCHO_PEER_ID = process.env.HONCHO_PEER_ID || fileConfig.honcho?.peer_id || HONCHO_AGENT_ID;
const HONCHO_WORKSPACE_MODE = process.env.HONCHO_WORKSPACE_MODE || fileConfig.honcho?.workspace_mode || "auto";
let HONCHO_WORKSPACE: string = process.env.HONCHO_WORKSPACE || fileConfig.honcho?.workspace || "default";

honchoLog("INFO", "Configuration", {
  HONCHO_BASE_URL,
  HONCHO_USER,
  HONCHO_AGENT_ID,
  HONCHO_WORKSPACE_MODE,
  HONCHO_WORKSPACE
});

// In-memory session tracking
let currentSessionId: string | null = null;

// Observational Memory Additions
const DETAILS_SCHEMA_VERSION = 2;
const DEFAULT_RESERVE_TOKENS = 16384;
const DEFAULT_OBSERVER_TRIGGER_TOKENS = 30000;
const DEFAULT_REFLECTOR_TRIGGER_TOKENS = 40000;
const DEFAULT_RAW_TAIL_RETAIN_TOKENS = 8000;
const AUTO_COMPACT_COOLDOWN_MS = 5000;
const MAX_CONTENT_LENGTH = 8000;

const REFLECT_LIMITS_THRESHOLD = { red: 96, yellow: 40, green: 16 } as const;
const REFLECT_LIMITS_FORCED = { red: 72, yellow: 28, green: 8 } as const;

type ReflectionMode = "none" | "threshold" | "forced";
type ObservationPriority = "red" | "yellow" | "green";

let observationOverlay: string[] = [];
let forceReflectNextCompaction = false;
let autoCompactInFlight = false;
let lastCompactTime = 0;
let gitBranch: string | null = null;

const OBS_SUMMARIZATION_SYSTEM_PROMPT = `You are a context summarization assistant...`;

interface PendingMessage { content: string; peer_id: string; h_metadata?: Record<string, any>; }
let messageQueue: PendingMessage[] = [];

async function detectWorkspaceFromContext(ctx: ExtensionContext): Promise<string> {
  honchoLog("DEBUG", "detectWorkspaceFromContext: starting");
  const fs = await import("node:fs/promises");
  const path = await import("node:path");
  const cwd = ctx.cwd;
  
  try {
    let dir = cwd;
    const root = path.parse(dir).root;
    
    while (dir !== root) {
      const gitConfigPath = path.join(dir, ".git", "config");
      
      try {
        const gitConfig = await fs.readFile(gitConfigPath, "utf-8");
        const originMatch = gitConfig.match(/\[remote "origin"\][^\[]*url\s*=\s*.*(?:\/|:)([^\/]+?)(?:\.git)?\s*$/m);
        
        if (originMatch) {
          const result = originMatch[1].trim().toLowerCase().replace(/[^a-z0-9_-]/g, "-");
          honchoLog("INFO", "Workspace from git origin", result);
          return result;
        }
        return path.basename(dir).toLowerCase().replace(/\s+/g, "-");
      } catch {
        // Continue up
      }
      dir = path.dirname(dir);
    }
  } catch (e: any) {
    honchoLog("WARN", "Git detection failed", e.message);
  }
  
  const baseName = path.basename(cwd).toLowerCase().replace(/\s+/g, "-");
  if (["src", "test", "tests", "lib", "app", "server", "client", "web"].includes(baseName)) {
    const parentDir = path.basename(path.dirname(cwd)).toLowerCase().replace(/\s+/g, "-");
    if (parentDir && parentDir !== ".") {
      return `${parentDir}-${baseName}`;
    }
  }
  
  return baseName || "default";
}

async function ensureWorkspaceExists(workspaceName: string): Promise<void> {
  honchoLog("DEBUG", "ensureWorkspaceExists: starting", workspaceName);
  try {
    await honchoFetch("/workspaces", {
      method: "POST",
      body: JSON.stringify({ id: workspaceName }),
    });
    honchoLog("INFO", "ensureWorkspaceExists: success", workspaceName);
  } catch (e: any) {
    honchoLog("ERROR", "ensureWorkspaceExists: failed", { workspaceName, error: e.message });
    throw e;
  }
}

async function honchoFetch(path: string, options: RequestInit = {}): Promise<any> {
  const url = `${HONCHO_BASE_URL}/v3${path}`;
  honchoLog("DEBUG", `honchoFetch: ${options.method || 'GET'} ${path}`, { url });
  
  try {
    const response = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
    
    honchoLog("DEBUG", `honchoFetch response: ${response.status}`, { ok: response.ok, path });
    
    if (!response.ok) {
      const text = await response.text();
      honchoLog("ERROR", `honchoFetch error: ${response.status}`, { path, text: text.slice(0, 200) });
      throw new Error(`Honcho API error: ${response.status} - ${text}`);
    }
    
    const result = await response.json();
    honchoLog("DEBUG", "honchoFetch: success", { path });
    return result;
  } catch (e: any) {
    honchoLog("ERROR", `honchoFetch exception`, { path, error: e.message });
    throw e;
  }
}

async function getOrCreateSession(): Promise<string> {
  honchoLog("DEBUG", "getOrCreateSession: starting", { currentSessionId });
  
  if (currentSessionId) {
    honchoLog("DEBUG", "getOrCreateSession: using existing", currentSessionId);
    return currentSessionId;
  }
  
  const sessionName = `pi-${Date.now()}`;
  honchoLog("DEBUG", "getOrCreateSession: creating new", sessionName);
  
  try {
    const session = await honchoFetch(`/workspaces/${HONCHO_WORKSPACE}/sessions`, {
      method: "POST",
      body: JSON.stringify({
        id: sessionName,
        peers: { [HONCHO_USER]: {}, [HONCHO_PEER_ID]: {} }
      }),
    });
    
    currentSessionId = session.id;
    honchoLog("INFO", "getOrCreateSession: created", session.id);
    return session.id;
  } catch (e: any) {
    honchoLog("ERROR", "getOrCreateSession: failed", e.message);
    throw e;
  }
}

async function queueMessage(content: string, peer_id: string, metadata?: Record<string, any>) {
  honchoLog("DEBUG", "queueMessage", { peer_id, type: metadata?.type, queueLength: messageQueue.length + 1 });
  messageQueue.push({ content, peer_id, h_metadata: metadata });
}

const MAX_MESSAGES_PER_BATCH = 5;

function splitContentIntoChunks(content: string, maxChunkSize: number = MAX_CONTENT_LENGTH): string[] {
  honchoLog("DEBUG", "splitContentIntoChunks", { contentLength: content.length, maxChunkSize });
  
  if (content.length <= maxChunkSize) return [content];
  
  const chunks: string[] = [];
  const paragraphs = content.split(/\n\n+/);
  let currentChunk = "";
  
  for (const paragraph of paragraphs) {
    if (paragraph.length > maxChunkSize) {
      if (currentChunk) { chunks.push(currentChunk.trim()); currentChunk = ""; }
      const sentences = paragraph.match(/[^.!?]+[.!?]+["\']?\s*/g) || [paragraph];
      for (const sentence of sentences) {
        if ((currentChunk + sentence).length > maxChunkSize) {
          if (currentChunk) { chunks.push(currentChunk.trim()); currentChunk = ""; }
          if (sentence.length > maxChunkSize) {
            for (let i = 0; i < sentence.length; i += maxChunkSize) {
              chunks.push(sentence.slice(i, i + maxChunkSize));
            }
          } else {
            currentChunk = sentence;
          }
        } else {
          currentChunk += sentence;
        }
      }
    } else {
      if ((currentChunk + paragraph + "\n\n").length > maxChunkSize) {
        if (currentChunk) { chunks.push(currentChunk.trim()); currentChunk = ""; }
        currentChunk = paragraph + "\n\n";
      } else {
        currentChunk += paragraph + "\n\n";
      }
    }
  }
  
  if (currentChunk) chunks.push(currentChunk.trim());
  honchoLog("DEBUG", "splitContentIntoChunks: done", { chunkCount: chunks.length });
  return chunks.length > 0 ? chunks : [content.slice(0, maxChunkSize)];
}

function prepareMessageBatches(messages: PendingMessage[]): Array<Array<{content: string; peer_id: string; metadata: Record<string, any>}>> {
  honchoLog("DEBUG", "prepareMessageBatches", { messageCount: messages.length });
  const processedMessages: Array<{content: string; peer_id: string; metadata: Record<string, any>}> = [];
  
  for (const msg of messages) {
    const baseMetadata = msg.h_metadata || {};
    if (msg.content.length > MAX_CONTENT_LENGTH) {
      const chunks = splitContentIntoChunks(msg.content, MAX_CONTENT_LENGTH);
      const totalChunks = chunks.length;
      chunks.forEach((chunk, idx) => {
        processedMessages.push({
          content: chunk,
          peer_id: msg.peer_id,
          metadata: { ...baseMetadata, chunk_index: idx + 1, total_chunks: totalChunks, original_length: msg.content.length, is_chunk: true }
        });
      });
    } else {
      processedMessages.push({ content: msg.content, peer_id: msg.peer_id, metadata: baseMetadata });
    }
  }
  
  const batches: Array<Array<{content: string; peer_id: string; metadata: Record<string, any>}>> = [];
  for (let i = 0; i < processedMessages.length; i += MAX_MESSAGES_PER_BATCH) {
    batches.push(processedMessages.slice(i, i + MAX_MESSAGES_PER_BATCH));
  }
  
  honchoLog("DEBUG", "prepareMessageBatches: done", { batchCount: batches.length });
  return batches;
}

async function flushMessages() {
  honchoLog("DEBUG", "flushMessages: starting", { queueLength: messageQueue.length });
  if (messageQueue.length === 0) {
    honchoLog("DEBUG", "flushMessages: empty queue, returning");
    return;
  }
  
  let sessionId: string;
  try {
    sessionId = await getOrCreateSession();
    honchoLog("DEBUG", "flushMessages: got session", sessionId);
  } catch (e: any) {
    honchoLog("ERROR", "flushMessages: failed to get session", e.message);
    return;
  }
  
  const originalQueue = [...messageQueue];
  messageQueue = [];
  const batches = prepareMessageBatches(originalQueue);
  
  honchoLog("DEBUG", "flushMessages: processing batches", { batchCount: batches.length });
  let successCount = 0;
  let failCount = 0;
  
  for (const batch of batches) {
    try {
      honchoLog("DEBUG", "flushMessages: sending batch", { batchSize: batch.length });
      await honchoFetch(`/workspaces/${HONCHO_WORKSPACE}/sessions/${sessionId}/messages`, {
        method: "POST",
        body: JSON.stringify({ messages: batch }),
      });
      successCount += batch.length;
      honchoLog("DEBUG", "flushMessages: batch success");
      if (batches.length > 1) await new Promise(r => setTimeout(r, 50));
    } catch (error: any) {
      failCount += batch.length;
      honchoLog("ERROR", "flushMessages: batch failed", error.message);
      for (const msg of batch) {
        if (!msg.metadata?.is_chunk) messageQueue.push({ content: msg.content, peer_id: msg.peer_id, h_metadata: msg.metadata });
      }
    }
  }
  
  honchoLog("INFO", "flushMessages: complete", { successCount, failCount, requeued: messageQueue.length });
  if (failCount > 0) {
  }
}

export default function (pi: ExtensionAPI) {
  honchoLog("INFO", "Extension export function called");
  let currentModel: string | null = null;

  pi.on("session_start", async (_event, ctx) => {
    honchoLog("INFO", "EVENT: session_start", { workspaceMode: HONCHO_WORKSPACE_MODE });
    try {
      if (HONCHO_WORKSPACE_MODE === "auto") {
        const detected = await detectWorkspaceFromContext(ctx);
        HONCHO_WORKSPACE = detected;
        honchoLog("INFO", "Auto-detected workspace", detected);
      }
      await ensureWorkspaceExists(HONCHO_WORKSPACE);
      const mode = HONCHO_WORKSPACE_MODE === "auto" ? "🔄 auto" : "📌 static";
      ctx.ui.notify(`Honcho: ${HONCHO_WORKSPACE} (${mode})`, "info", 3000);
      honchoLog("INFO", "session_start: complete");
    } catch (e: any) {
      honchoLog("ERROR", "session_start: failed", e.message);
    }
  });

  pi.on("before_agent_start", async (event, ctx) => {
    honchoLog("DEBUG", "EVENT: before_agent_start");
    try {
      await getOrCreateSession();
      currentModel = ctx.model ? `${ctx.model.provider}/${ctx.model.id}` : "unknown";
      honchoLog("DEBUG", "before_agent_start: model", currentModel);
      
      await queueMessage(event.prompt, HONCHO_USER, {
        role: "user", type: "prompt", has_images: !!event.images?.length, intended_model: currentModel
      });
      
      setTimeout(() => {
        flushMessages().catch(err => honchoLog("ERROR", "before_agent_start flush failed", err.message));
      }, 0);
    } catch (e: any) {
      honchoLog("ERROR", "before_agent_start: error", e.message);
    }
    return {};
  });

  pi.on("turn_start", async (event, ctx) => {
    honchoLog("DEBUG", "EVENT: turn_start", { turnIndex: event.turnIndex });
    await queueMessage(`Starting turn ${event.turnIndex}`, HONCHO_PEER_ID, { type: "turn_start", turn_index: event.turnIndex, model: currentModel });
  });

  pi.on("context", async (event, ctx) => {
    honchoLog("DEBUG", "EVENT: context");
    const assistantMessages = event.messages.filter(m => m.role === "assistant" && (m.tool_calls || m.content?.some(c => c.type === "text")));
    const lastAssistant = assistantMessages[assistantMessages.length - 1];
    if (lastAssistant?.content) {
      const thoughtText = lastAssistant.content.filter(c => c.type === "text").map(c => c.text).join("");
      if (thoughtText) {
        honchoLog("DEBUG", "context: captured thought", { length: thoughtText.length });
        await queueMessage(`Thought: ${thoughtText}`, HONCHO_PEER_ID, { type: "thought", step: "planning", model: currentModel });
      }
    }
  });

  pi.on("tool_call", async (event, ctx) => {
    honchoLog("DEBUG", "EVENT: tool_call", { tool: event.toolName });
    const toolCallData = { tool: event.toolName, tool_call_id: event.toolCallId, input: event.input };
    await queueMessage(JSON.stringify(toolCallData), HONCHO_PEER_ID, { type: "tool_call", tool: event.toolName, tool_call_id: event.toolCallId, model: currentModel });
  });

  pi.on("tool_result", async (event, ctx) => {
    const outputText = event.result?.content?.map((c: any) => c.type === "text" ? c.text : "").join("") || "";
    const willBeChunked = outputText.length > MAX_CONTENT_LENGTH;
    honchoLog("DEBUG", "EVENT: tool_result", { tool: event.toolName, outputLength: outputText.length, willBeChunked });
    
    await queueMessage(`Observation (${event.toolName}):\n${outputText}`, HONCHO_PEER_ID, {
      type: "observation", tool: event.toolName, tool_call_id: event.toolCallId, is_error: event.isError,
      status: event.isError ? "error" : "success", output_length: outputText.length, will_be_chunked: willBeChunked, model: currentModel
    });
  });

  pi.on("turn_end", async (event, ctx) => {
    honchoLog("DEBUG", "EVENT: turn_end", { hasMessage: !!event.message });
    if (!event.message) return;
    
    if (event.message.role === "assistant") {
      const responseText = event.message.content?.map(c => c.type === "text" ? c.text : "").join("") || "";
      honchoLog("DEBUG", "turn_end: captured response", { length: responseText.length });
      await queueMessage(responseText, HONCHO_PEER_ID, { role: "assistant", type: "final", turn_index: event.turnIndex, model: currentModel });
    }
    
    setTimeout(() => {
      flushMessages().catch(err => honchoLog("ERROR", "turn_end flush failed", err.message));
    }, 0);
  });

  pi.on("agent_end", async (event, ctx) => {
    honchoLog("DEBUG", "EVENT: agent_end");
    setTimeout(() => {
      flushMessages().catch(err => honchoLog("ERROR", "agent_end flush failed", err.message));
    }, 0);
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    honchoLog("INFO", "EVENT: session_shutdown", { queueSize: messageQueue.length });
    if (messageQueue.length > 0) {
      try {
        await flushMessages();
        honchoLog("INFO", "session_shutdown: flushed messages", messageQueue.length);
      } catch (error: any) {
        honchoLog("ERROR", "session_shutdown: flush failed", error.message);
      }
    }
    if (observationOverlay.length > 0) {
      const summary = observationOverlay.join('\n');
      await queueMessage(summary, HONCHO_PEER_ID, { type: 'obs_summary', level: 'synthesis' });
      await flushMessages();
    }
  });

  // Observational Memory Hooks
  function estimateRawTailTokens(entries: any[]): number {
    let total = 0;
    for (let i = entries.length - 10; i < entries.length; i++) {
      if (entries[i]) total += entries[i].content?.length / 4 || 0;
    }
    return total;
  }

  function reflectObservations(obsText: string, mode: ReflectionMode): { summary: string, dropped: number } {
    honchoLog("DEBUG", "reflectObservations", { mode, textLength: obsText.length });
    const lines = obsText.split('\n').filter(l => l.includes('🔴') || l.includes('🟡') || l.includes('🟢'));
    const parsed = lines.map((line, idx) => ({
      priority: line.includes('🔴') ? 'red' : line.includes('🟡') ? 'yellow' : 'green',
      body: line.replace(/^[\s*-]+/, '').trim(),
      key: line.toLowerCase().replace(/[^a-z0-9]/g, ''), index: idx
    }));

    const unique = new Map();
    parsed.forEach(obs => {
      const existing = unique.get(obs.key);
      if (!existing || priorityRank(obs.priority) > priorityRank(existing.priority) || (priorityRank(obs.priority) === priorityRank(existing.priority) && obs.index > existing.index)) {
        unique.set(obs.key, obs);
      }
    });

    const limits = mode === 'forced' ? REFLECT_LIMITS_FORCED : REFLECT_LIMITS_THRESHOLD;
    const counts = { red: 0, yellow: 0, green: 0 };
    const kept = Array.from(unique.values()).filter(obs => {
      if (counts[obs.priority] < limits[obs.priority as keyof typeof limits]) { counts[obs.priority]++; return true; }
      return false;
    }).sort((a, b) => priorityRank(b.priority) - priorityRank(a.priority));

    const dropped = parsed.length - kept.length;
    const summary = kept.map(obs => `- ${obs.priority === 'red' ? '🔴' : obs.priority === 'yellow' ? '🟡' : '🟢'} ${obs.body}`).join('\n');
    honchoLog("DEBUG", "reflectObservations: done", { kept: kept.length, dropped });
    return { summary: `## Observations\n${summary}`, dropped };
  }

  function priorityRank(p: ObservationPriority): number { return p === 'red' ? 3 : p === 'yellow' ? 2 : 1; }

  function formatFileOperations(fileOps: any, previousSummary?: string): string {
    const read = fileOps.read || [];
    const modified = [...(fileOps.edited || []), ...(fileOps.written || [])];
    let tags = '';
    if (read.length > 0) tags += `<read-files>\n${read.join('\n')}\n</read-files>`;
    if (modified.length > 0) tags += `<modified-files>\n${modified.join('\n')}\n</modified-files>`;
    return tags;
  }

  // session_before_compact hook
  pi.on("session_before_compact", async (event, ctx) => {
    honchoLog("INFO", "EVENT: session_before_compact");
    const { preparation } = event;
    const { messagesToSummarize, turnPrefixMessages, previousSummary } = preparation;

    if (!ctx.model) {
      honchoLog("WARN", "session_before_compact: no model available");
      return;
    }

    const allMessages = [...messagesToSummarize, ...turnPrefixMessages];
    const convText = allMessages.map(m => `[${m.role}]: ${m.content?.map((c: any) => c.text || '').join('')}`).join('\n');
    honchoLog("DEBUG", "session_before_compact: queuing messages", { messageCount: allMessages.length });
    
    await queueMessage(convText, HONCHO_PEER_ID, { type: 'obs_extraction', session_part: 'compact' });
    await flushMessages();

    let searchResult: any[] = [];
    try {
      honchoLog("DEBUG", "session_before_compact: querying conclusions");
      searchResult = await honchoFetch(`/workspaces/${HONCHO_WORKSPACE}/conclusions/query`, {
        method: 'POST',
        body: JSON.stringify({ query: 'recent observations pi session', top_k: 10, filters: { level: 'synthesis' } })
      });
      honchoLog("INFO", "session_before_compact: conclusions query success", { results: searchResult.length });
    } catch (e: any) {
      if (e.message?.includes('404')) {
        honchoLog("WARN", "session_before_compact: conclusions/query 404, using empty results");
      } else {
        honchoLog("ERROR", "session_before_compact: query failed", e.message);
      }
    }

    let summary = '';
    let details: any = { strategy: 'honcho-obs' };
    if (searchResult?.length > 0) {
      const honchoObs = searchResult.map((d: any) => d.content).join('\n');
      const { summary: reflected, dropped } = reflectObservations(honchoObs, 'threshold');
      summary = `${reflected}\n\n## Open Threads\n- Continue from Honcho synthesis.\n\n## Next Action Bias\n1. Use retrieved patterns from memory.`;
      details = { ...details, honcho_hits: searchResult.length, dropped };
    } else {
      summary = `## Observations\n- No Honcho insights; using raw context.\n\n## Open Threads\n- Recent messages.\n\n## Next Action Bias\n1. Proceed with current task.`;
    }
    summary += formatFileOperations(preparation.fileOps, previousSummary || '');

    honchoLog("INFO", "session_before_compact: returning compaction");
    return { compaction: { summary, firstKeptEntryId: preparation.firstKeptEntryId, tokensBefore: preparation.tokensBefore, details } };
  });

  // session_before_tree hook
  pi.on("session_before_tree", async (event, ctx) => {
    honchoLog("INFO", "EVENT: session_before_tree");
    const { preparation } = event;
    if (!preparation.userWantsSummary || preparation.entriesToSummarize.length === 0) {
      honchoLog("DEBUG", "session_before_tree: no summary needed or empty entries");
      return;
    }
    if (!ctx.model) return;

    const branchId = `branch-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const branchText = preparation.entriesToSummarize.map((e: any) => JSON.stringify(e)).join('\n');
    await queueMessage(branchText, HONCHO_PEER_ID, { type: 'obs_tree', branch_id: branchId, raw: true });

    const convText = preparation.entriesToSummarize.map((e: any) => e.content || '').join('\n');
    const { summary: reflected } = reflectObservations(`## Raw Branch Obs\n${convText.substring(0, 2000)}`, 'threshold');
    const branchSummary = `${reflected}\n\n## Branch File Ops\n${formatFileOperations(preparation.fileOps || {})}`;

    await queueMessage(branchSummary, HONCHO_PEER_ID, { type: 'obs_tree', branch_id: branchId, level: 'inductive' });
    await flushMessages();

    let searchResult: any[] = [];
    try {
      searchResult = await honchoFetch(`/workspaces/${HONCHO_WORKSPACE}/conclusions/query`, {
        method: 'POST', body: JSON.stringify({ query: 'branch observations tree merge', top_k: 5, filters: { level: 'inductive' } })
      });
    } catch (e: any) {
      if (e.message?.includes('404')) honchoLog("WARN", "session_before_tree: conclusions/query 404");
    }

    let finalSummary = branchSummary;
    if (searchResult?.length > 0) {
      const similarPatterns = searchResult.map((d: any) => `- Pattern from ${d.metadata?.branch_id}: ${d.content.substring(0, 100)}`).join('\n');
      finalSummary += `\n\n## Similar Branch Patterns\n${similarPatterns}`;
    }

    const cacheEntry = finalSummary || `Empty branch merge on ${new Date().toISOString()}`;
    observationOverlay.push(cacheEntry);
    honchoLog("INFO", "session_before_tree: cached branch entry", { branchId });

    return { summary: { summary: finalSummary, details: { strategy: 'honcho-obs-tree', branch_id: branchId, honcho_hits: searchResult?.length || 0, entries_summarized: preparation.entriesToSummarize.length } } };
  });

  // Commands and Tools
  pi.registerCommand("honcho-status", { description: "Show Honcho connection status", handler: async (_args, ctx) => {
    const status = currentSessionId ? `Session: ${currentSessionId}` : "No active session";
    const pending = messageQueue.length > 0 ? ` (${messageQueue.length} pending)` : "";
    ctx.ui.notify(`${status}${pending}\nAPI: ${HONCHO_BASE_URL}\nWorkspace: ${HONCHO_WORKSPACE}`, "info");
  }});

  pi.registerCommand("honcho-flush", { description: "Manually flush pending messages", handler: async (_args, ctx) => {
    const count = messageQueue.length;
    await flushMessages();
    ctx.ui.notify(`Flushed ${count} messages`, "success");
  }});

  pi.registerTool({
    name: "honcho_store", label: "Store in Honcho", description: "Store a message in Honcho",
    parameters: Type.Object({ content: Type.String(), peer_id: Type.String({ default: HONCHO_USER }), metadata: Type.Optional(Type.Record(Type.String(), Type.Any())) }),
    async execute(_toolCallId, params) {
      honchoLog("INFO", "TOOL: honcho_store", { peer_id: params.peer_id });
      await queueMessage(params.content, params.peer_id, params.metadata);
      await flushMessages();
      return { content: [{ type: "text", text: "Message stored" }] };
    }
  });

  pi.registerTool({
    name: "honcho_chat", label: "Honcho Chat", description: "Query Honcho's Dialectic API",
    parameters: Type.Object({ query: Type.String(), reasoning_level: Type.String({ default: "low" }) }),
    async execute(_toolCallId, params) {
      honchoLog("INFO", "TOOL: honcho_chat", { query: params.query.slice(0, 50) });
      const url = `/workspaces/${HONCHO_WORKSPACE}/peers/${HONCHO_USER}/chat`;
      try {
        const result = await honchoFetch(url, { method: "POST", body: JSON.stringify({ query: params.query, reasoning_level: params.reasoning_level, stream: false, session_id: currentSessionId }) });
        return { content: [{ type: "text", text: result.content || "No results" }], details: result };
      } catch (e: any) {
        honchoLog("ERROR", "honcho_chat failed", e.message);
        return { content: [{ type: "text", text: `Error: ${e.message}` }] };
      }
    }
  });

  pi.registerTool({
    name: "honcho_list_documents", label: "List Documents",
    parameters: Type.Object({ limit: Type.Number({ default: 20 }) }),
    async execute(_toolCallId, params) {
      honchoLog("INFO", "TOOL: honcho_list_documents");
      try {
        const result = await honchoFetch(`/workspaces/${HONCHO_WORKSPACE}/documents?limit=${params.limit}`, { method: "GET" });
        return { content: [{ type: "text", text: result.map((d: any) => `- ${d.name}`).join("\n") }] };
      } catch (e: any) {
        honchoLog("ERROR", "honcho_list_documents failed", e.message);
        if (e.message.includes('404')) return { content: [{ type: "text", text: "API not ready (404)" }] };
        throw e;
      }
    }
  });

  pi.registerTool({
    name: "honcho_search_documents", label: "Search Documents",
    parameters: Type.Object({ query: Type.String(), limit: Type.Number({ default: 5 }) }),
    async execute(_toolCallId, params) {
      honchoLog("INFO", "TOOL: honcho_search_documents", { query: params.query.slice(0, 50) });
      try {
        const result = await honchoFetch(`/workspaces/${HONCHO_WORKSPACE}/conclusions/query`, {
          method: "POST", body: JSON.stringify({ query: params.query, top_k: params.limit })
        });
        return { content: [{ type: "text", text: result.map((d: any) => `[${d.score?.toFixed(2)}] ${d.name}`).join("\n") }] };
      } catch (e: any) {
        honchoLog("ERROR", "honcho_search_documents failed", e.message);
        if (e.message.includes('404')) return { content: [{ type: "text", text: "Search API not ready (404)" }] };
        throw e;
      }
    }
  });

  honchoLog("INFO", "Extension registration complete");
}
