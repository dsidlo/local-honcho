# Honcho Extension for pi-mono - README

A pi-mono extension that captures the **complete ReAct cycle** for maximum Dreamer + Dialectic intelligence.

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
        FLUSH[Async Flusher]
    end
    
    subgraph "Honcho Services"
        API[Honcho API<br/>Port 8000]
        DERIVER[Deriver Worker]
        DB[(PostgreSQL + pgvector)]
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
    QUEUE --> FLUSH
    FLUSH -. Async .-> API
    API --> DB
    DB --> RAW
    DB --> OBS
    DERIVER --> OBS
    DERIVER --> DERIVED
```

### Event-Driven Data Flow

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant Pi as Pi Core
    participant Ext as Honcho Extension
    participant Queue as Message Queue
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
    
    Pi->>Ext: turn_start event
    activate Ext
    Ext->>Queue: Queue turn metadata
    Ext-->>Pi: Return immediately
    deactivate Ext
    
    Pi->>Ext: tool_call event
    activate Ext
    Ext->>Queue: Queue tool intent
    Ext-->>Pi: Return immediately
    deactivate Ext
    
    Pi->>Ext: tool_result event  
    activate Ext
    Ext->>Queue: Queue observation
    Ext-->>Pi: Return immediately
    deactivate Ext
    
    Pi->>Ext: turn_end event
    activate Ext
    Ext->>Queue: Queue final response
    Ext->>Ext: setTimeout(flush, 0)
    Ext-->>Pi: Return (spinner clears!)
    deactivate Ext
    
    Note right of Pi: User sees response immediately
    
    par Background Flush
        Queue->>API: HTTP POST /messages
        API->>DB: INSERT messages
    and
        Pi->>User: Streaming response
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
        +detectWorkspaceFromContext(ctx)
    }
    
    class PendingMessage {
        +content: string
        +peer_id: string
        +h_metadata: Map
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
    HonchoExtension --> HonchoAPI : calls
    HonchoExtension --> EventHandlers : implements
```

### Message Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Created: User/Agent generates content
    
    Created --> Queued: queueMessage() called
    
    Queued --> Scheduled: setTimeout(flush, 0)
    
    Scheduled --> Flushing: Next event loop tick
    
    Flushing --> Sent: HTTP POST to /messages
    
    Sent --> Stored: PostgreSQL INSERT
    
    Stored --> Processing: Deriver picks up
    
    Processing --> Observed: Extract observations
    
    Observed --> Vectorized: Generate embeddings
    
    Vectorized --> Indexed: HNSW index
    
    Indexed --> Available: Query via Dialectic
    
    Available --> [*]
    
    Note right of Created
        In-memory only
    End note
    
    Note right of Queued
        Extension queues
        before async flush
    End note
    
    Note right of Stored
        Now durable in
        PostgreSQL
    End note
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

### Async Flush Pattern

```mermaid
sequenceDiagram
    autonumber
    participant Handler as Event Handler
    participant Stack as Call Stack
    participant Timer as setTimeout
    participant API as Honcho API

    Note over Handler: User sees spinner
    
    Handler->>Stack: Queue messages
    Handler->>Timer: setTimeout(flush, 0)
    Note over Timer: Schedules for next tick
    Handler->>Handler: Return immediately
    
    Note over Handler: Spinner clears!<br/>User sees response
    
    Timer->>Stack: Timeout fires
    Stack->>API: HTTP POST begins
    Note over API: Runs in background
    API-->>Stack: Response received
    
    Note over Handler: No UI impact
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

### Full ReAct Trace Capture

| Step | What's Captured | Metadata |
|------|-----------------|----------|
| User Prompt | Full text + images | `type: "prompt"`, `intended_model` |
| Agent Thought | Reasoning/planning | `type: "thought"`, `step: "planning"` |
| Tool Call | Tool name + args | `type: "tool_call"`, `tool: "bash"` |
| Tool Output | stdout/stderr | `type: "observation"`, `status: "success"` |
| Final Response | Complete output | `type: "final"`, `role: "assistant"` |

---

## Configuration

Add to your `~/.env`:

```bash
# Required
HONCHO_BASE_URL=http://localhost:8000
HONCHO_USER=<user>

# Optional (defaults shown)  
HONCHO_AGENT_ID=agent-pi-mono
HONCHO_WORKSPACE_MODE=auto       # "auto" or "static"
HONCHO_WORKSPACE=default         # Used when mode=static
```

---

## Available Tools

### `honcho_store`
Manually store a message in Honcho.

```
honcho_store
  content: "Important information to remember"
  peer_id: "user" (optional)
  metadata: { custom: "data" }
```

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
Retrieve recent conversation context.

```
honcho_context
  tokens: 4000
  include_summary: true
```

### `honcho_search`
Search across all sessions.

```
honcho_search
  query: "jwt authentication"
  limit: 10
```

---

## Commands

| Command | Description |
|---------|-------------|
| `/honcho-start` | Create new session (flushes current) |
| `/honcho-status` | Show connection + pending messages |
| `/honcho-flush` | Manually flush pending messages |

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

### "Working" spinner stuck
- Fixed: All flushes now use `setTimeout(..., 0)`
- Extension returns immediately, flush runs in background

---

## Implementation Notes

### Async Design Pattern

All event handlers use **fire-and-forget** pattern:

```typescript
// Don't block UI
setTimeout(() => {
  flushMessages().catch(err => console.error(err));
}, 0);
```

This ensures pi's "Working" spinner clears immediately while HTTP requests run in the background.

### Message Queue

Messages are batched in memory until flush:
- User messages queued first
- Tool calls/observations queued as they happen  
- Final response queued on `turn_end`
- Single HTTP POST sends all messages atomically

### Error Handling

- Failed flushes are caught and logged to console only
- No UI interruption on network errors
- Messages remain in queue for next flush attempt

---

## References

- [Honcho Documentation](https://docs.honcho.dev)
- [pi-mono Extensions](https://github.com/mariozechner/pi-coding-agent/blob/main/docs/extensions.md)
- [systemd User Services](https://wiki.archlinux.org/title/Systemd/User)
