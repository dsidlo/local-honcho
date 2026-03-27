# Honcho Extension for pi-mono - README

A pi-mono extension that captures the **complete ReAct cycle** for maximum Dreamer + Dialectic intelligence, with **intelligent content chunking** for large messages and documents.

## Quick Reference

- **Extension File**: `~/.pi/agent/extensions/honcho.ts`
- **Configuration**: `~/.env`
- **Services**: `honcho-api.service`, `honcho-deriver.service`
- **API URL**: http://localhost:8000

---

## Architecture & Program Flow

### System Architecture

```mermaid
graph TB
    subgraph "pi-mono Environment"
        USER[User Input]
        PI[Pi Agent Core]
        EXT[Honcho Extension]
    end
    
    subgraph "Extension Internals"
        EVT[Event Handlers]
        QUEUE[Message Queue]
        CHUNK[Intelligent Chunker<br/>Paragraph/Sentence Split]
        BATCH[Batch Sizer<br/>Max 5 msgs/batch]
        FLUSH[Async Flusher]
    end
    
    subgraph "Honcho Services"
        API[Honcho API<br/>Port 8000]
        DERIVER[Deriver Worker]
        DB["PostgreSQL + pgvector"]
    end
    
    subgraph "Memory Layers"
        RAW[Raw Messages]
        OBS[Observations<br/>Explicit/Deductive]
        DERIVED[Derived Insights<br/>Dreamer]
    end
    
    USER --> PI
    PI --> EXT
    EXT --> EVT
    EVT --> QUEUE
    QUEUE --> CHUNK
    CHUNK --> BATCH
    BATCH --> FLUSH
    FLUSH -. Async .-> API
    API --> DB
    DB --> RAW
    DB --> OBS
    DERIVER --> OBS
    DERIVER --> DERIVED
```

### Event-Driven Data Flow with Chunking

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant Pi as Pi Core
    participant Ext as Honcho Extension
    participant Queue as Message Queue
    participant Chunker as Content Chunker
    participant BATCH as Batch Processor
    participant API as Honcho API
    participant DB as PostgreSQL

    User->>Pi: Submit prompt
    Pi->>Ext: before_agent_start event
    activate Ext
    Ext->>Queue: Queue user message
    Ext->>Ext: setTimeout(flush, 0)
    Ext-->>Pi: Return immediately
    deactivate Ext
    
    Pi->>Pi: Agent generates response
    
    Note over Ext: Large tool output detected<br/>e.g., honcho.ts (8KB+)
    
    Pi->>Ext: tool_result event  
    activate Ext
    Ext->>Queue: Queue large observation
    Note over Queue: Content = 15,000 chars
    Ext-->>Pi: Return immediately
    deactivate Ext
    
    Pi->>Ext: turn_end event
    activate Ext
    Ext->>Queue: Queue final response
    Ext->>Ext: setTimeout(flush, 0)
    Ext-->>Pi: Response visible!
    deactivate Ext
    
    Note right of Queue: Background Processing Begins
    
    Queue->>Chunker: prepareMessageBatches()
    activate Chunker
    Chunker->>Chunker: splitContentIntoChunks()
    Note over Chunker: Paragraph-first splitting<br/>Fallback to sentences<br/>MAX_CONTENT_LENGTH = 8000
    Chunker-->>Queue: 2 chunks: 8K + 7K chars
    deactivate Chunker
    
    Queue->>BATCH: Batch by MAX_MESSAGES_PER_BATCH = 5
    
    par Background Flush (Batched)
        BATCH->>API: HTTP POST /messages (chunk 1)
        API->>DB: INSERT with chunk metadata
        Note over DB: Stores: chunk_index=1, total_chunks=2
        
        Note over BATCH: 50ms delay between batches
        
        BATCH->>API: HTTP POST /messages (chunk 2)
        API->>DB: INSERT with chunk metadata
        Note over DB: Stores: chunk_index=2, total_chunks=2
    and
        Pi->>User: Streaming response (already visible)
    end
```

### Object Interactions

```mermaid
classDiagram
    class ExtensionAPI {
        +on(event, handler)
        +registerTool(definition)
        +registerCommand(name, options)
    }
    
    class ExtensionContext {
        +model: ModelInfo
        +sessionManager: SessionManager
        +ui: UIProxy
        +cwd: string
    }
    
    class HonchoExtension {
        -messageQueue: PendingMessage[]
        -currentSessionId: string
        -currentModel: string
        +queueMessage(content, peer_id, metadata)
        +flushMessages()
        +splitContentIntoChunks(content, maxSize)
        +prepareMessageBatches(messages)
        +detectWorkspaceFromContext(ctx)
    }
    
    class PendingMessage {
        +content: string
        +peer_id: string
        +h_metadata: Map
        +chunk_index: number (optional)
        +total_chunks: number (optional)
        +is_chunk: boolean (optional)
    }
    
    class ChunkingConfig {
        +MAX_MESSAGES_PER_BATCH: 5
        +MAX_CONTENT_LENGTH: 8000
        +MAX_DOC_SIZE: 100000
    }
    
    class HonchoAPI {
        +createSession(workspace, peers)
        +storeMessages(sessionId, messages)
        +queryDialectic(workspace, peer, query)
    }
    
    class EventHandlers {
        +before_agent_start(event, ctx)
        +turn_start(event, ctx)
        +context(event, ctx)
        +tool_call(event, ctx)
        +tool_result(event, ctx)
        +turn_end(event, ctx)
        +agent_end(event, ctx)
    }
    
    ExtensionAPI --> HonchoExtension : uses
    HonchoExtension --> ExtensionContext : receives
    HonchoExtension --> PendingMessage : queues
    HonchoExtension --> ChunkingConfig : configures
    HonchoExtension --> HonchoAPI : calls
    HonchoExtension --> EventHandlers : implements
```

### Chunking Algorithm Flow

```mermaid
flowchart TD
    A[Content > MAX_CONTENT_LENGTH?] -->|Yes| B[Start Chunking]
    A -->|No| Z[Return as single chunk]
    
    B --> C[Split by paragraphs<br/>/\\n\\n+/]
    C --> D[For each paragraph]
    
    D --> E{Paragraph > maxSize?}
    E -->|Yes| F[Flush current chunk]
    F --> G["Split by sentences\nusing regex pattern"]
    G --> H{Sentence > maxSize?}
    H -->|Yes| I[Force split at char boundary]
    H -->|No| J[Add to current chunk]
    I --> K[Next sentence]
    J --> K
    K --> L{More sentences?}
    L -->|Yes| G
    L -->|No| M[Next paragraph]
    
    E -->|No| N{Fits in current chunk?}
    N -->|Yes| O[Add paragraph + \\n\\n]
    N -->|No| P[Flush current chunk]
    P --> Q[Start new chunk]
    O --> M
    Q --> M
    
    M --> R{More paragraphs?}
    R -->|Yes| D
    R -->|No| S[Flush final chunk]
    
    S --> T[Return chunks array]
    Z --> T
    
    T --> U[Create batch metadata]
    U --> V["Add metadata fields"]
```

### Message Lifecycle with Chunking

```mermaid
stateDiagram-v2
    [*] --> Created: User/Agent generates content
    
    Created --> Queued: queueMessage() called
    Note right of Created
        Original content preserved
        in queue (may be 10K+ chars)
    End note
    
    Queued --> Processing: flushMessages() called
    
    Processing --> Chunked: splitContentIntoChunks()
    Note right of Chunked
        Paragraph boundaries preferred
        Sentence boundaries fallback
        Force split if needed
    End note
    
    Chunked --> Batched: prepareMessageBatches()
    Note right of Batched
        MAX_MESSAGES_PER_BATCH = 5
        Each chunk becomes
        separate message
    End note
    
    Batched --> Sending: HTTP POST per batch
    
    Sending --> Stored: PostgreSQL INSERT
    Note right of Stored
        Each chunk stored with:
        - chunk_index (1-based)
        - total_chunks
        - is_chunk: true
        - original_length
    End note
    
    Stored --> Processing2: Deriver picks up
    Processing2 --> Vectorized: Generate embeddings
    Note right of Vectorized
        Smaller chunks = better
        embedding quality due
        to token limit alignment
    End note
    
    Vectorized --> Indexed: HNSW index
    Indexed --> Available: Query via Dialectic
    
    Available --> Search: User queries
    Search --> Reconstruct: Group by original
    Note right of Reconstruct
        Application layer can
        reassemble chunks using
        chunk_index/total_chunks
    End note
    
    Reconstruct --> [*]
```

### Workspace Detection Flow

```mermaid
flowchart TD
    A[User starts pi in directory] --> B{HONCHO_WORKSPACE_MODE}
    
    B -->|static| C[Use HONCHO_WORKSPACE env var]
    B -->|auto| D[Walk up directory tree]
    
    D --> E{Find .git/config?}
    E -->|Yes| F[Extract remote origin URL]
    F --> G[Parse repo name]
    G --> H[Workspace = repo-name]
    
    E -->|No| I{Generic dir name?}
    I -->|Yes| J[Use parent-dir-name]
    I -->|No| K[Use current dir name]
    
    C --> L[Ensure workspace exists]
    H --> L
    J --> L
    K --> L
    
    L --> M{Workspace in Honcho?}
    M -->|Yes| N[Use existing workspace]
    M -->|No| O[POST /workspaces]
    
    O --> N
    N --> P[Create session]
    P --> Q[Ready to capture!]
```

### Async Flush Pattern with Retry

```mermaid
sequenceDiagram
    autonumber
    participant Handler as Event Handler
    participant Stack as Call Stack
    participant Timer as setTimeout
    participant Chunker as Content Chunker
    participant API as Honcho API
    participant Queue as Failed Queue

    Note over Handler: User sees spinner
    
    Handler->>Stack: Queue messages
    Handler->>Timer: setTimeout(flush, 0)
    Handler->>Handler: Return immediately
    
    Note over Handler: Spinner clears!<br/>User sees response
    
    Timer->>Stack: Timeout fires
    Stack->>Chunker: prepareMessageBatches()
    Chunker->>Chunker: splitContentIntoChunks()
    
    loop For each batch (max 5 messages)
        Stack->>API: HTTP POST /messages
        
        alt Success
            API-->>Stack: 201 Created
        else Failure
            API-->>Stack: Error/Timeout
            Stack->>Queue: Re-queue non-chunked messages
            Note over Queue: Chunks not re-queued<br/>to avoid infinite loops
        end
        
        Note over Stack: 50ms delay between batches
    end
    
    Note over Handler: No UI impact from flush
```

### Document Upload Chunking

```mermaid
sequenceDiagram
    participant User
    participant Tool as honcho_upload_document
    participant Chunker as Content Chunker
    participant API as Honcho API
    participant DB as PostgreSQL

    User->>Tool: Upload large file (150KB)
    
    Tool->>Tool: Read file content
    Note over Tool: content.length = 150,000
    
    Tool->>Chunker: splitContentIntoChunks(MAX_DOC_SIZE=100K)
    activate Chunker
    Chunker->>Chunker: Paragraph-based splitting
    Chunker-->>Tool: chunks[0], chunks[1] (~75K each)
    deactivate Chunker
    
    Tool->>Tool: Create chunk metadata
    Note over Tool: {is_chunked: true, total_chunks: 2}
    
    loop For each chunk
        Tool->>API: POST /documents (chunk 1)
        API->>DB: INSERT with metadata
        DB-->>API: document_id
        Note over API: Name: "file.ts (chunk 1/2)"
        
        Note over Tool: 50ms delay
        
        Tool->>API: POST /documents (chunk 2)
        API->>DB: INSERT with metadata
        DB-->>API: document_id
        Note over API: Name: "file.ts (chunk 2/2)"
    end
    
    Tool-->>User: "Uploaded in 2 chunks"
```

---

## Features

### Automatic Session Management
- Automatically creates a new Honcho session when pi starts
- Tracks conversations and stores them in Honcho  
- Session ID persists until pi reload

### Dynamic Workspace Detection
- **Mode: `auto` (default)**
- Detects git repository name from `remote origin`
- Falls back to directory name with parent context
- Auto-creates workspace in Honcho if missing

Example:
```bash
cd ~/projects/honcho && pi     # Workspace: "honcho"
cd ~/my-api/src && pi         # Workspace: "my-api-src"
```

### Full ReAct Trace Capture with Chunking

| Step | What's Captured | Metadata |
|------|-----------------|----------|
| User Prompt | Full text + images | `type: "prompt"`, `intended_model` |
| Agent Thought | Reasoning/planning | `type: "thought"`, `step: "planning"` |
| Tool Call | Tool name + args | `type: "tool_call"`, `tool: "bash"` |
| Tool Output | stdout/stderr | `type: "observation"`, `will_be_chunked: true` |
| Final Response | Complete output | `type: "final"`, `role: "assistant"` |

### Intelligent Content Chunking

**For Large Messages:**
- **Threshold**: `MAX_CONTENT_LENGTH = 8000` chars (~250-400 tokens)
- **Strategy**: Paragraph boundaries → Sentence boundaries → Character boundaries
- **Batch Size**: `MAX_MESSAGES_PER_BATCH = 5` messages per HTTP request
- **Metadata**: Each chunk includes `chunk_index`, `total_chunks`, `original_length`, `is_chunk`

**For Document Uploads:**
- **Threshold**: `MAX_DOC_SIZE = 100000` chars (~100KB)
- **Strategy**: Same paragraph-based chunking as messages
- **Storage**: Each chunk becomes separate document with linking metadata

---

## Configuration

Add to your `~/.env`:

```bash
# Required
HONCHO_BASE_URL=http://localhost:8000
HONCHO_USER=dsidlo

# Optional (defaults shown)  
HONCHO_AGENT_ID=agent-pi-mono
HONCHO_WORKSPACE_MODE=auto       # "auto" or "static"
HONCHO_WORKSPACE=default         # Used when mode=static
```

---

## Available Tools

### `honcho_store`
Manually store a message in Honcho. Large content is automatically chunked.

```
honcho_store
  content: "Important information to remember... (can be 10K+ chars)"
  peer_id: "user" (optional)
  metadata: { custom: "data" }
```

**Response includes:**
- `chunked: true/false` - Whether content was split
- `chunks: N` - Number of chunks created

### `honcho_chat`
Query Honcho's Dialectic for answers about stored memories.

```
honcho_chat
  query: "What approach did I use for database migrations?"
  reasoning_level: "low" (optional: minimal/low/medium/high/max)
```

### `honcho_insights`
Get personalization insights about your coding style.

```
honcho_insights
  question: "What are my common debugging patterns?"
```

### `honcho_context`
Retrieve recent conversation context (reconstructed from chunks).

```
honcho_context
  tokens: 4000
  include_summary: true
```

### `honcho_search`
Search across all sessions (handles chunked content).

```
honcho_search
  query: "jwt authentication"
  limit: 10
```

### `honcho_upload_document`
Upload a file or document. Large files are intelligently chunked.

```
honcho_upload_document
  file_path: "/path/to/large-file.ts"
  name: "my-file" (optional)
  metadata: { language: "typescript" }
  level: "session" (user/session/workspace)
```

**Response includes:**
- `is_chunked: true/false` - Whether file was split
- `total_chunks: N` - Number of chunks created
- `document_ids: ["id1", "id2", ...]` - IDs of all chunks

### `honcho_list_documents`
List all documents with chunk info.

```
honcho_list_documents
  limit: 20
  include_deleted: false
```

### `honcho_search_documents`
Search documents using semantic/vector search.

```
honcho_search_documents
  query: "error handling patterns"
  limit: 5
  level: "session" (optional filter)
```

---

## Commands

| Command | Description |
|---------|-------------|
| `/honcho-status` | Show connection + pending messages + chunk stats |
| `/honcho-flush` | Manually flush pending messages (respects chunking) |

---

## Systemd Integration

Services run automatically via systemd user services:

```bash
# Status
systemctl --user status honcho-api honcho-deriver

# Logs
journalctl --user -u honcho-api -f

# Restart
systemctl --user restart honcho-api
```

---

## What Gets Learned

With the full trace, Honcho's Dreamer extracts:

- **Your coding style** - preferred patterns, naming conventions
- **Common pitfalls** - errors you hit frequently  
- **Project architecture** - how you structure code
- **Debugging patterns** - what you check first
- **Tool preferences** - when you use grep vs find vs read
- **Decision rationale** - why you chose approach X over Y
- **Model effectiveness** - which LLM performs best for you

### Better Embeddings with Chunking

Smaller chunks result in:
- **Higher quality embeddings** - Model can focus on specific concepts
- **Better semantic search** - More precise retrieval of relevant info
- **Reduced token overflow** - No more "No embedding returned from Ollama" errors
- **Improved context windows** - Each chunk fits comfortably in embedding model limits

---

## Troubleshooting

### Extension not loading
- Check TypeScript syntax: `pi -e ./honcho.ts` to test
- Verify `HONCHO_BASE_URL` is set correctly
- Ensure Honcho API is running: `curl http://localhost:8000`

### Messages not appearing
- Check `/honcho-status` for pending count
- Run `/honcho-flush` to force store
- Verify workspace exists in Honcho

### "No embedding returned from Ollama" errors
- **Fixed by chunking**: Large messages are now automatically split
- Check Honcho API logs: `journalctl --user -u honcho-api -e`
- Verify Ollama is accessible at configured embed endpoint

### "Working" spinner stuck
- **Fixed**: All flushes use `setTimeout(..., 0)`
- Extension returns immediately, flush runs in background

### Chunks in search results
- Chunks include metadata for reconstruction: `chunk_index`, `total_chunks`
- Use chunk metadata to reassemble full content when needed
- Search returns all chunks; filter/group by `original_doc_name`

---

## Implementation Notes

### Chunking Configuration

```typescript
const MAX_MESSAGES_PER_BATCH = 5;      // Messages per HTTP request
const MAX_CONTENT_LENGTH = 8000;         // ~250-400 tokens per message
const MAX_DOC_SIZE = 100000;             // ~100KB for documents
```

### Chunking Algorithm

1. **Paragraph Splitting**: First attempt to split at `\n\n+` (double newlines)
2. **Sentence Fallback**: If paragraph > max, split at sentence boundaries (`[^.!?]+[.!?]+`)
3. **Force Split**: If single sentence > max, split at character boundary
4. **Batch Assembly**: Group processed messages into batches of max 5

### Async Design Pattern

All event handlers use **fire-and-forget** pattern:

```typescript
// Queue first, return immediately
await queueMessage(content, peer_id, metadata);

// Let browser/runtime flush in background
setTimeout(() => {
  flushMessages().catch(err => console.error(err));
}, 0);
```

### Error Handling

- **Failed flushes**: Logged to console only, no UI interruption
- **Chunk retry policy**: Non-chunked messages re-queued; chunks dropped to avoid loops
- **Batch isolation**: One batch failure doesn't affect others
- **Queue clearing**: Queue cleared at flush start to prevent duplicates on partial failure

---

## References

- [Honcho Documentation](https://docs.honcho.dev)
- [pi-mono Extensions](https://github.com/mariozechner/pi-coding-agent/blob/main/docs/extensions.md)
- [systemd User Services](https://wiki.archlinux.org/title/Systemd/User)
