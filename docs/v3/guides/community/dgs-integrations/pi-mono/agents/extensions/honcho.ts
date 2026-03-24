import type { ExtensionAPI, ExtensionContext } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";

/**
 * Honcho Extension for pi-mono - FULL REASONING TRACE VERSION
 *
 * Captures complete ReAct cycle for maximum Dreamer + Dialectic intelligence:
 * - User prompts
 * - Agent thoughts (reasoning)
 * - Tool calls (intent)
 * - Tool outputs (observations)
 * - Final responses
 *
 * Environment variables:
 *   HONCHO_BASE_URL=http://localhost:8000 (default)
 *   HONCHO_USER=<USER> (default)
 *   HONCHO_AGENT_ID=agent-pi-mono (default)
 *   HONCHO_WORKSPACE_MODE=auto | static (default: auto)
 *   HONCHO_WORKSPACE=default (used when mode=static)
 */

// Configuration from environment
const HONCHO_BASE_URL = process.env.HONCHO_BASE_URL || "http://localhost:8000";
const HONCHO_USER = process.env.HONCHO_USER || "user";
const HONCHO_AGENT_ID = process.env.HONCHO_AGENT_ID || "agent-pi-mono";
// Dynamic peer ID - allows subagents/team members to identify themselves
const HONCHO_PEER_ID = process.env.HONCHO_PEER_ID || HONCHO_AGENT_ID;
const HONCHO_WORKSPACE_MODE = process.env.HONCHO_WORKSPACE_MODE || "auto";

// Dynamic workspace - will be set based on context
let HONCHO_WORKSPACE: string = process.env.HONCHO_WORKSPACE || "default";

// In-memory session tracking
let currentSessionId: string | null = null;

// Message queue for batching related messages
interface PendingMessage {
  content: string;
  peer_id: string;
  h_metadata?: Record<string, any>;
}
let messageQueue: PendingMessage[] = [];

/**
 * Detect workspace from git repository or current directory
 */
async function detectWorkspaceFromContext(ctx: ExtensionContext): Promise<string> {
  const fs = await import("node:fs/promises");
  const path = await import("node:path");

  const cwd = ctx.cwd;

  // Try to find git repo name
  try {
    let dir = cwd;
    const root = path.parse(dir).root;

    while (dir !== root) {
      const gitConfigPath = path.join(dir, ".git", "config");

      try {
        const gitConfig = await fs.readFile(gitConfigPath, "utf-8");

        // Extract repo name from remote origin URL
        // Handles: https://github.com/user/repo.git or git@github.com:user/repo.git
        const originMatch = gitConfig.match(
          /\[remote "origin"\][^\[]*url\s*=\s*.*(?:\/|:)([^\/]+?)(?:\.git)?\s*$/m
        );

        if (originMatch) {
          return originMatch[1].trim().toLowerCase().replace(/[^a-z0-9_-]/g, "-");
        }

        // Fallback: use directory name of git root
        return path.basename(dir).toLowerCase().replace(/\s+/g, "-");
      } catch {
        // .git/config doesn't exist here, go up
      }

      dir = path.dirname(dir);
    }
  } catch {
    // Git detection failed
  }

  // Fall back to current directory name
  const baseName = path.basename(cwd).toLowerCase().replace(/\s+/g, "-");

  // If directory name is generic, add parent context
  if (["src", "test", "tests", "lib", "app", "server", "client", "web"].includes(baseName)) {
    const parentDir = path.basename(path.dirname(cwd)).toLowerCase().replace(/\s+/g, "-");
    if (parentDir && parentDir !== ".") {
      return `${parentDir}-${baseName}`;
    }
  }

  return baseName || "default";
}

/**
 * Ensure workspace exists in Honcho (create if needed)
 */
async function ensureWorkspaceExists(workspaceName: string): Promise<void> {
  // POST /workspaces is get-or-create, so we can just call it
  await honchoFetch("/workspaces", {
    method: "POST",
    body: JSON.stringify({ id: workspaceName }),
  });
}
async function honchoFetch(path: string, options: RequestInit = {}): Promise<any> {
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
 * Initialize or get the current session
 */
async function getOrCreateSession(): Promise<string> {
  if (currentSessionId) {
    return currentSessionId;
  }

  // Create a new session with timestamp-based name
  const sessionName = `pi-${Date.now()}`;
  const session = await honchoFetch(`/workspaces/${HONCHO_WORKSPACE}/sessions`, {
    method: "POST",
    body: JSON.stringify({
      id: sessionName,
      peers: {
        [HONCHO_USER]: {},
        [HONCHO_PEER_ID]: {}
      }
    }),
  });

  currentSessionId = session.id;

  return session.id;
}

/**
 * Queue a message for batch storage
 */
async function queueMessage(content: string, peer_id: string, metadata?: Record<string, any>) {
  messageQueue.push({
    content,
    peer_id,
    h_metadata: metadata
  });
}

/**
 * Flush queued messages to Honcho
 */
async function flushMessages() {
  if (messageQueue.length === 0) return;

  const sessionId = await getOrCreateSession();

  try {
    await honchoFetch(
      `/workspaces/${HONCHO_WORKSPACE}/sessions/${sessionId}/messages`,
      {
        method: "POST",
        body: JSON.stringify({
          messages: messageQueue.map(m => ({
            content: m.content,
            peer_id: m.peer_id,
            metadata: m.h_metadata || {}
          })),
        }),
      }
    );

    // Silent success - no console output to avoid terminal clutter
    messageQueue = [];
  } catch (error) {
    console.error("[Honcho] Failed to store messages:", error);
  }
}

export default function (pi: ExtensionAPI) {
  // Track current model across events
  let currentModel: string | null = null;

  /**
   * Event: Session start - detect workspace and notify user
   */
  pi.on("session_start", async (_event, ctx) => {
    // Detect workspace based on mode
    if (HONCHO_WORKSPACE_MODE === "auto") {
      const detectedWorkspace = await detectWorkspaceFromContext(ctx);
      HONCHO_WORKSPACE = detectedWorkspace;

      // Ensure workspace exists
      await ensureWorkspaceExists(HONCHO_WORKSPACE);
    }

    const mode = HONCHO_WORKSPACE_MODE === "auto" ? "auto" : "static";
    ctx.ui.notify(
      `Honcho: ${HONCHO_WORKSPACE} (${mode})`,
      "info",
      3000
    );
  });

  /**
   * Event: Before agent start - capture user prompt
   */
  pi.on("before_agent_start", async (event, ctx) => {
    await getOrCreateSession();

    // Capture current model for this turn
    const model = ctx.model;
    currentModel = model ? `${model.provider}/${model.id}` : "unknown";

    // Capture user prompt with metadata including model context
    await queueMessage(
      event.prompt,
      HONCHO_USER,
      {
        role: "user",
        type: "prompt",
        has_images: !!event.images?.length,
        intended_model: currentModel
      }
    );

    // Flush immediately so user message is stored before processing
    // Non-blocking to avoid interrupting UI flow
    setTimeout(() => {
      flushMessages().catch(err => console.error("[Honcho] Flush failed:", err));
    }, 0);

    return {};
  });

  /**
   * Event: Turn start - capture agent reasoning/thoughts
   */
  pi.on("turn_start", async (event, ctx) => {
    // Store turn index and model for context
    await queueMessage(
      `Starting turn ${event.turnIndex}`,
      HONCHO_PEER_ID,
      {
        type: "turn_start",
        turn_index: event.turnIndex,
        model: currentModel
      }
    );
  });

  /**
   * Event: Context - capture agent's planned actions (thoughts)
   * This happens after the LLM decides what to do but before tool execution
   */
  pi.on("context", async (event, ctx) => {
    // Look for assistant message with tool calls (the "thought")
    const assistantMessages = event.messages.filter(m =>
      m.role === "assistant" &&
      (m.tool_calls || m.content?.some(c => c.type === "text"))
    );

    const lastAssistant = assistantMessages[assistantMessages.length - 1];
    if (lastAssistant && lastAssistant.content) {
      const thoughtText = lastAssistant.content
        .filter(c => c.type === "text")
        .map(c => c.text)
        .join("");

      if (thoughtText) {
        await queueMessage(
          `Thought: ${thoughtText}`,
          HONCHO_PEER_ID,
          {
            type: "thought",
            step: "planning",
            model: currentModel
          }
        );
      }
    }
  });

  /**
   * Event: Tool call - capture tool intent
   */
  pi.on("tool_call", async (event, ctx) => {
    const toolCallData = {
      tool: event.toolName,
      tool_call_id: event.toolCallId,
      input: event.input
    };

    await queueMessage(
      JSON.stringify(toolCallData),
      HONCHO_PEER_ID,
      {
        type: "tool_call",
        tool: event.toolName,
        tool_call_id: event.toolCallId,
        model: currentModel
      }
    );
  });

  /**
   * Event: Tool result - capture tool output/observation
   */
  pi.on("tool_result", async (event, ctx) => {
    // Get output from result
    const outputText = event.result?.content
      ?.map((c: any) => c.type === "text" ? c.text : "")
      .join("") || "";

    // Truncate very long outputs but keep full data in metadata
    const truncatedOutput = outputText.length > 10000
      ? outputText.substring(0, 10000) + "\n...[truncated]"
      : outputText;

    await queueMessage(
      `Observation (${event.toolName}):\n${truncatedOutput}`,
      HONCHO_PEER_ID,
      {
        type: "observation",
        tool: event.toolName,
        tool_call_id: event.toolCallId,
        is_error: event.isError,
        status: event.isError ? "error" : "success",
        output_length: outputText.length,
        model: currentModel
      }
    );
  });

  /**
   * Event: Turn end - capture final response and flush all messages
   */
  pi.on("turn_end", async (event, ctx) => {
    if (!event.message) return;

    // Capture final assistant response
    if (event.message.role === "assistant") {
      const responseText = event.message.content
        ?.map(c => c.type === "text" ? c.text : "")
        .join("") || "";

      await queueMessage(
        responseText,
        HONCHO_PEER_ID,
        {
          role: "assistant",
          type: "final",
          turn_index: event.turnIndex,
          model: currentModel
        }
      );
    }

    // Flush all queued messages for this turn
    // Schedule flush outside current tick to not block TUI spinner
    setTimeout(() => {
      flushMessages().catch(err => console.error("[Honcho] Flush failed:", err));
    }, 0);
  });

  /**
   * Event: Agent end - ensure any remaining messages are flushed
   */
  pi.on("agent_end", async (event, ctx) => {
    // Non-blocking flush - runs after response, shouldn't delay UI
    setTimeout(() => {
      flushMessages().catch(err => console.error("[Honcho] Flush failed:", err));
    }, 0);
  });

  /**
   * Event: Session shutdown - flush messages before pi terminates
   */
  pi.on("session_shutdown", async (_event, ctx) => {
    // Flush any pending messages before shutdown
    if (messageQueue.length > 0) {
      try {
        await flushMessages();
        console.log(`[Honcho] Flushed ${messageQueue.length} messages before shutdown`);
      } catch (error) {
        console.error("[Honcho] Final flush failed:", error);
      }
    }
  });

  /**
   * Tool: honcho_store - Manually store a message
   */
  pi.registerTool({
    name: "honcho_store",
    label: "Store in Honcho",
    description: "Store a message in Honcho memory system for the current session",
    promptSnippet: "Store important information in Honcho memory",
    parameters: Type.Object({
      content: Type.String({ description: "Content to store" }),
      peer_id: Type.String({
        description: "Peer ID (user or assistant)",
        default: HONCHO_USER
      }),
      metadata: Type.Optional(Type.Record(Type.String(), Type.Any(), {
        description: "Optional metadata as JSON object"
      })),
    }),
    async execute(_toolCallId, params) {
      await queueMessage(params.content, params.peer_id, params.metadata);
      await flushMessages();

      return {
        content: [{ type: "text", text: `Message stored in Honcho` }],
        details: { stored: true },
      };
    },
  });

  /**
   * Tool: honcho_chat - Query Honcho Dialectic
   */
  pi.registerTool({
    name: "honcho_chat",
    label: "Honcho Chat",
    description: "Query Honcho's Dialectic API to ask questions about stored memories",
    promptSnippet: "Ask Honcho about past conversations or stored information",
    promptGuidelines: [
      "Use this tool when you need to recall information from previous conversations",
      "Ask natural language questions like 'What approach did I use for X?'",
      "Query your coding patterns, preferences, and past decisions",
    ],
    parameters: Type.Object({
      query: Type.String({
        description: "Natural language question to ask about stored memories"
      }),
      reasoning_level: Type.String({
        enum: ["minimal", "low", "medium", "high", "max"],
        default: "low"
      }),
    }),
    async execute(_toolCallId, params) {
      const url = `/workspaces/${HONCHO_WORKSPACE}/peers/${HONCHO_USER}/chat`;

      const body: any = {
        query: params.query,
        reasoning_level: params.reasoning_level,
        stream: false,
      };

      if (currentSessionId) body.session_id = currentSessionId;

      const result = await honchoFetch(url, {
        method: "POST",
        body: JSON.stringify(body),
      });

      return {
        content: [{
          type: "text",
          text: result.content || "No relevant information found"
        }],
        details: result,
      };
    },
  });

  /**
   * Tool: honcho_insights - Get personalization insights
   */
  pi.registerTool({
    name: "honcho_insights",
    label: "Honcho Insights",
    description: "Get personalization insights about the user based on conversation history",
    promptSnippet: "Query user preferences and patterns from Honcho",
    parameters: Type.Object({
      question: Type.String({
        description: "Question about user preferences, style, or patterns"
      }),
    }),
    async execute(_toolCallId, params) {
      const url = `/workspaces/${HONCHO_WORKSPACE}/peers/${HONCHO_USER}/chat`;

      const body = {
        query: params.question,
        session_id: currentSessionId,
        reasoning_level: "medium",
        stream: false,
      };

      const result = await honchoFetch(url, {
        method: "POST",
        body: JSON.stringify(body),
      });

      return {
        content: [{
          type: "text",
          text: result.content || "No insights available yet"
        }],
        details: result,
      };
    },
  });

  /**
   * Tool: honcho_context - Get session context
   */
  pi.registerTool({
    name: "honcho_context",
    label: "Honcho Context",
    description: "Retrieve recent conversation context from Honcho",
    parameters: Type.Object({
      tokens: Type.Number({ default: 4000 }),
      include_summary: Type.Boolean({ default: true }),
    }),
    async execute(_toolCallId, params) {
      const sessionId = currentSessionId || await getOrCreateSession();

      const result = await honchoFetch(
        `/workspaces/${HONCHO_WORKSPACE}/sessions/${sessionId}/context?` +
        `tokens=${params.tokens || 4000}&summary=${params.include_summary}`,
        { method: "GET" }
      );

      const messages = result.messages
        ?.map((m: any) => `[${m.peer_id}]: ${m.content?.substring(0, 500)}`)
        .join("\n\n");

      return {
        content: [{
          type: "text",
          text: messages || "No context available"
        }],
        details: result,
      };
    },
  });

  /**
   * Tool: honcho_search - Search across all sessions
   */
  pi.registerTool({
    name: "honcho_search",
    label: "Honcho Search",
    description: "Search for messages across all Honcho sessions",
    parameters: Type.Object({
      query: Type.String({ description: "Search query" }),
      limit: Type.Number({ default: 10 }),
    }),
    async execute(_toolCallId, params) {
      const url = `/workspaces/${HONCHO_WORKSPACE}/peers/${HONCHO_USER}/search`;

      const result = await honchoFetch(url, {
        method: "POST",
        body: JSON.stringify({
          query: params.query,
          limit: params.limit || 10,
        }),
      });

      const messages = result
        ?.map((m: any) => `[${m.session_id}] ${m.peer_id}: ${m.content?.substring(0, 200)}`)
        .join("\n\n");

      return {
        content: [{
          type: "text",
          text: messages || "No results found"
        }],
        details: { count: result?.length || 0 },
      };
    },
  });

  /**
   * Tool: honcho_upload_document - Upload a document to Honcho
   */
  pi.registerTool({
    name: "honcho_upload_document",
    label: "Upload Document to Honcho",
    description: "Upload a file or document content to Honcho for semantic search and retrieval",
    promptSnippet: "Upload document to Honcho for RAG and search",
    promptGuidelines: [
      "Use this to store files, code, documentation, or any text content",
      "Documents are embedded and available for semantic search via honcho_search",
      "Large files are automatically chunked for better retrieval",
    ],
    parameters: Type.Object({
      file_path: Type.String({
        description: "Path to file to upload (optional if content provided)"
      }),
      content: Type.Optional(Type.String({
        description: "Direct content to upload (optional if file_path provided)"
      })),
      name: Type.Optional(Type.String({
        description: "Document name (defaults to filename or 'untitled')"
      })),
      metadata: Type.Optional(Type.Record(Type.String(), Type.Any(), {
        description: "Optional metadata (e.g., file_type, language, tags)"
      })),
      level: Type.Optional(Type.String({
        enum: ["user", "session", "workspace"],
        default: "session",
        description: "Document visibility level"
      })),
    }),
    async execute(_toolCallId, params, ctx) {
      const fs = await import("node:fs/promises");
      const path = await import("node:path");

      let content: string;
      let docName: string;

      // Get content from file or direct input
      if (params.file_path) {
        const fullPath = path.resolve(params.file_path);
        content = await fs.readFile(fullPath, "utf-8");
        docName = params.name || path.basename(params.file_path);
      } else if (params.content) {
        content = params.content;
        docName = params.name || "untitled";
      } else {
        throw new Error("Either file_path or content must be provided");
      }

      // Truncate very large files
      const MAX_SIZE = 100000; // ~100KB
      const isTruncated = content.length > MAX_SIZE;
      if (isTruncated) {
        content = content.substring(0, MAX_SIZE) + "\n...[truncated for storage]";
      }

      const sessionId = currentSessionId || await getOrCreateSession();

      // Create document via API
      const result = await honchoFetch(
        `/workspaces/${HONCHO_WORKSPACE}/documents`,
        {
          method: "POST",
          body: JSON.stringify({
            name: docName,
            content: content,
            metadata: {
              ...params.metadata,
              source: params.file_path || "direct",
              truncated: isTruncated,
              uploaded_by: HONCHO_PEER_ID,
              session_id: sessionId,
            },
            level: params.level || "session",
          }),
        }
      );

      return {
        content: [{
          type: "text",
          text: `Document "${docName}" uploaded to Honcho (${content.length} chars${isTruncated ? ", truncated" : ""})`
        }],
        details: {
          document_id: result.id,
          name: docName,
          size: content.length,
          truncated: isTruncated,
        },
      };
    },
  });

  /**
   * Tool: honcho_list_documents - List documents in workspace
   */
  pi.registerTool({
    name: "honcho_list_documents",
    label: "List Honcho Documents",
    description: "List all documents stored in the current Honcho workspace",
    parameters: Type.Object({
      limit: Type.Number({ default: 20 }),
      include_deleted: Type.Boolean({ default: false }),
    }),
    async execute(_toolCallId, params) {
      const result = await honchoFetch(
        `/workspaces/${HONCHO_WORKSPACE}/documents?limit=${params.limit || 20}`,
        { method: "GET" }
      );

      const docs = result
        ?.filter((d: any) => params.include_deleted || !d.deleted_at)
        ?.map((d: any) => `- ${d.name} (${d.content?.length || 0} chars, level: ${d.level})`)
        ?.join("\n");

      return {
        content: [{
          type: "text",
          text: docs || "No documents found"
        }],
        details: { count: result?.length || 0 },
      };
    },
  });

  /**
   * Tool: honcho_search_documents - Search documents
   */
  pi.registerTool({
    name: "honcho_search_documents",
    label: "Search Honcho Documents",
    description: "Search for documents in Honcho using semantic/vector search",
    promptSnippet: "Search documents in Honcho",
    parameters: Type.Object({
      query: Type.String({ description: "Search query" }),
      limit: Type.Number({ default: 5 }),
      level: Type.Optional(Type.String({
        enum: ["user", "session", "workspace"],
        description: "Filter by document level"
      })),
    }),
    async execute(_toolCallId, params) {
      const url = `/workspaces/${HONCHO_WORKSPACE}/documents/search`;

      const body: any = {
        query: params.query,
        limit: params.limit || 5,
      };
      if (params.level) body.level = params.level;

      const result = await honchoFetch(url, {
        method: "POST",
        body: JSON.stringify(body),
      });

      const docs = result
        ?.map((d: any) => `[${d.score?.toFixed(2) || "N/A"}] ${d.name}:\n${d.content?.substring(0, 300)}...`)
        ?.join("\n\n");

      return {
        content: [{
          type: "text",
          text: docs || "No matching documents found"
        }],
        details: result,
      };
    },
  });

  /**
   * Command: /honcho-status - Show current session status
   */
  pi.registerCommand("honcho-status", {
    description: "Show Honcho connection status",
    handler: async (_args, ctx) => {
      const status = currentSessionId
        ? `Session: ${currentSessionId}`
        : "No active session";
      const pending = messageQueue.length > 0
        ? ` (${messageQueue.length} pending)`
        : "";

      const mode = HONCHO_WORKSPACE_MODE === "auto" ? "🔄 auto" : "📌 static";

      ctx.ui.notify(
        `${status}${pending}\n` +
        `API: ${HONCHO_BASE_URL}\n` +
        `Workspace: ${HONCHO_WORKSPACE} ${mode}\n` +
        `User: ${HONCHO_USER}\n` +
        `Agent: ${HONCHO_AGENT_ID}`,
        "info"
      );
    },
  });

  /**
   * Command: /honcho-flush - Manually flush pending messages
   */
  pi.registerCommand("honcho-flush", {
    description: "Manually flush pending messages to Honcho",
    handler: async (_args, ctx) => {
      const count = messageQueue.length;
      await flushMessages();
      ctx.ui.notify(`Flushed ${count} messages to Honcho`, "success");
    },
  });

}
