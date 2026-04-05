# Honcho - Technical Requirements Document

## Version: 3.0.3
## Date: March 2025

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Core Concepts](#2-core-concepts)
3. [Data Model & Entity Relationships](#3-data-model--entity-relationships)
4. [API Architecture](#4-api-architecture)
5. [Background Processing Pipeline](#5-background-processing-pipeline)
6. [Agent Systems](#6-agent-systems)
7. [Memory Formation Process](#7-memory-formation-process)
8. [Query & Retrieval System](#8-query--retrieval-system)
9. [Session Management](#9-session-management)
   - [Context Management & Token Limits](#94-context-management--token-limits)
10. [Configuration System](#10-configuration-system)
11. [Security & Authentication](#11-security--authentication)
12. [Telemetry & Monitoring](#12-telemetry--monitoring)
13. [Caching System](#13-caching-system)

---

## 1. System Overview

Honcho is an open-source memory infrastructure layer for building AI agents with persistent identity and social cognition. It provides APIs for storing conversation history, extracting insights about peers (users and agents), and retrieving contextual information to personalize AI interactions.

### Key Capabilities

- **Peer-Centric Memory**: Store and retrieve observations about any entity (users, agents, groups, ideas)
- **Multi-Participant Sessions**: Support conversations with multiple peers (humans and AI agents)
- **Background Reasoning**: Derive insights from conversations through asynchronous processing
- **Dialectic API**: Natural language query interface for asking questions about peers
- **Dream Processing**: Consolidate and refine observations through scheduled background tasks

### Architecture Components

```mermaid
graph TB
    subgraph \"API Layer\"
        API[FastAPI Application]
        ROUTERS[Router Modules]
        SCHEMAS[Pydantic Schemas]
    end

    subgraph \"Core Services\"
        AGENTS[Agent Systems]
        DIALECTIC[Dialectic Service]
        DREAMER[Dream Orchestrator]
        DERIVER[Message Deriver]
    end

    subgraph \"Background Processing\"
        QUEUE[Task Queue]
        CONSUMER[Queue Consumer]
        WORKER[Deriver Worker]
    end

    subgraph \"Storage Layer\"
        DB[(PostgreSQL + pgvector)]
        CACHE[Redis Cache]
        VECTOR[Vector Store<br/>pgvector/Turbopuffer/LanceDB]
    end

    subgraph \"External Services\"
        LLM[LLM Providers<br/>Anthropic/OpenAI/Gemini/Groq]
        EMBED[Embedding Services]
        SENTRY[Sentry Error Tracking]
        TELEMETRY[CloudEvents Telemetry]
    end

    API --> ROUTERS
    ROUTERS --> AGENTS
    AGENTS --> LLM
    AGENTS --> DB
    
    API --> QUEUE
    QUEUE --> CONSUMER
    CONSUMER --> WORKER
    WORKER --> DERIVER
    WORKER --> DREAMER
    
    DERIVER --> EMBED
    DREAMER --> EMBED
    DIALECTIC --> EMBED
    
    DB --> CACHE
    DB --> VECTOR

```

---

## 2. Core Concepts

### 2.1 Peer Paradigm

Honcho uses a unified \"Peer\" model where both users and AI agents are treated equally as participants in the system.

```mermaid
graph LR
    subgraph \"Traditional Model\"
        USER_TRAD[User] --> |\\\"interacts with\\\"| AGENT_TRAD[AI Agent]
        AGENT_TRAD --> |\\\"remembers\\\"| USER_MEM[User Memory Only]
    end

    subgraph \"Honcho Peer Model\"
        USER[Peer: User] --> |\\\"interacts with\\\"| AGENT[Peer: AI Agent]
        AGENT --> |\\\"observes\\\"| USER_OBS[\\\"Observation Collection<br/>(User from Agent's view)\\\"]
        USER --> |\\\"observes\\\"| AGENT_OBS[\\\"Observation Collection<br/>(Agent from User's view)\\\"]
    end
```

### 2.2 Observation Model

Observations are stored at different levels of inference:

| Level | Description | Example |
|-------|-------------|---------|
| **Explicit** | Direct facts from messages | \"User said they live in NYC\" |
| **Deductive** | Logical inferences from explicit facts | \"User has urban lifestyle preferences\" |
| **Inductive** | Patterns across multiple observations | \"User prefers concise responses\" |
| **Contradiction** | Conflicting statements that need resolution | \"User said they love/hate coffee\" |

### 2.3 Configuration Hierarchy

Configuration is resolved hierarchically from most-specific to most-general:

```mermaid
graph BT
    MSG_CONF[\\\"Message-level Configuration\\\"]
    SESS_CONF[\\\"Session-level Configuration\\\"]
    PEER_CONF[\\\"Peer-level Configuration\\\"]
    WKSP_CONF[\\\"Workspace-level Configuration\\\"]
    DEFAULT[\\\"Default Settings\\\"]

    MSG_CONF --> SESS_CONF
    SESS_CONF --> PEER_CONF
    PEER_CONF --> WKSP_CONF
    WKSP_CONF --> DEFAULT
```

---

## 3. Data Model & Entity Relationships

### 3.1 Entity Relationship Diagram

```mermaid
erDiagram
    WORKSPACE {
        string id PK
        string name UK
        datetime created_at
        jsonb metadata
        jsonb internal_metadata
        jsonb configuration
    }

    PEER {
        string id PK
        string name \\\"UK with workspace\\\"
        string workspace_name FK
        datetime created_at
        jsonb metadata
        jsonb internal_metadata
        jsonb configuration
    }

    SESSION {
        string id PK
        string name \\\"UK with workspace\\\"
        string workspace_name FK
        boolean is_active
        datetime created_at
        jsonb metadata
        jsonb internal_metadata
        jsonb configuration
    }

    SESSION_PEERS {
        string workspace_name PK,FK
        string session_name PK,FK
        string peer_name PK,FK
        jsonb configuration
        jsonb internal_metadata
        datetime joined_at
        datetime left_at
    }

    MESSAGE {
        bigint id PK
        string public_id UK
        string session_name FK
        string workspace_name FK
        bigint seq_in_session
        string peer_name FK
        text content
        integer token_count
        datetime created_at
        jsonb metadata
        jsonb internal_metadata
    }

    MESSAGE_EMBEDDING {
        bigint id PK
        text content
        vector embedding
        string message_id FK
        string workspace_name FK
        string session_name
        string peer_name
        datetime created_at
        string sync_state
    }

    COLLECTION {
        string id PK
        string observer \\\"UK with observed+workspace\\\"
        string observed \\\"UK with observer+workspace\\\"
        string workspace_name FK
        datetime created_at
        jsonb metadata
        jsonb internal_metadata
    }

    DOCUMENT {
        string id PK
        string collection_ref FK
        text content
        string level \\\"explicit/deductive/inductive\\\"
        integer times_derived
        vector embedding
        jsonb source_ids
        jsonb internal_metadata
        datetime created_at
        string session_name FK
        datetime deleted_at
        string sync_state
    }

    QUEUE_ITEM {
        bigint id PK
        string session_id FK
        string work_unit_key
        string task_type
        jsonb payload
        boolean processed
        text error
        datetime created_at
        string workspace_name
        bigint message_id
    }

    PEER_CARD {
        string workspace_name PK,FK
        string observer PK,FK
        string observed PK,FK
        text content
        datetime created_at
        datetime updated_at
    }

    WEBHOOK_ENDPOINT {
        string id PK
        string workspace_name FK
        string url
        datetime created_at
    }

    WORKSPACE ||--o{ PEER : contains
    WORKSPACE ||--o{ SESSION : contains
    WORKSPACE ||--o{ COLLECTION : contains
    WORKSPACE ||--o{ QUEUE_ITEM : contains
    WORKSPACE ||--o{ WEBHOOK_ENDPOINT : has

    SESSION ||--o{ SESSION_PEERS : has
    SESSION ||--o{ MESSAGE : contains

    PEER ||--o{ SESSION_PEERS : participates_in

    SESSION_PEERS }o--|| PEER : references
    SESSION_PEERS }o--|| SESSION : references

    PEER ||--o{ COLLECTION : observes_as_observer
    PEER ||--o{ COLLECTION : observed_in

    COLLECTION ||--o{ DOCUMENT : contains

    MESSAGE ||--o| MESSAGE_EMBEDDING : has_embedding
    MESSAGE }o--|| SESSION : belongs_to
    MESSAGE }o--|| PEER : sent_by

    PEER ||--o| PEER_CARD : has_card
```

### 3.2 Key Constraints

- **Workspace-scoped uniqueness**: Peer names, session names are unique within a workspace
- **Composite foreign keys**: Messages link to sessions and peers via composite keys (name + workspace_name)
- **Collection Uniqueness**: Each observer-observed pair in a workspace has exactly one collection
- **Message Embeddings**: Optional vector embeddings for semantic search of messages

---

## 4. API Architecture

### 4.1 API Endpoint Structure

All API routes follow the pattern: `/v3/{resource}/{id}/{action}`

```mermaid
graph TD
    subgraph \"Workspaces\"
        W_POST(\"/POST /v3/workspaces<br/>Create workspace\")
        W_LIST(\"/POST /v3/workspaces/list<br/>List workspaces\")
    end

    subgraph \"Peers\"
        P_CREATE(\"/POST /v3/workspaces/{id}/peers<br/>Get or create peer\")
        P_LIST(\"/POST /v3/workspaces/{id}/peers/list<br/>List peers\")
        P_REP(\"/POST /v3/workspaces/{id}/peers/{peer_id}/representation<br/>Get representation\")
        P_CHAT(\"/POST /v3/workspaces/{id}/peers/{peer_id}/chat<br/>Chat/dialectic query\")
        P_CARD_GET(\"/GET /v3/workspaces/{id}/peers/{peer_id}/card<br/>Get peer card\")
        P_CARD_SET(\"/PUT /v3/workspaces/{id}/peers/{peer_id}/card<br/>Set peer card\")
        P_CTX(\"/GET /v3/workspaces/{id}/peers/{peer_id}/context<br/>Get peer context\")
    end

    subgraph \"Sessions\"
        S_CREATE(\"/POST /v3/workspaces/{id}/sessions<br/>Create session\")
        S_LIST(\"/POST /v3/workspaces/{id}/sessions/list<br/>List sessions\")
        S_PEERS_GET(\"/GET /v3/workspaces/{id}/sessions/{s_id}/peers<br/>Get session peers\")
        S_PEERS_ADD(\"/POST /v3/workspaces/{id}/sessions/{s_id}/peers<br/>Add peers\")
        S_CTX(\"/GET /v3/workspaces/{id}/sessions/{s_id}/context<br/>Get session context\")
    end

    subgraph \"Messages\"
        M_CREATE(\"/POST /v3/workspaces/{id}/sessions/{s_id}/messages<br/>Create messages\")
        M_LIST(\"/POST /v3/workspaces/{id}/sessions/{s_id}/messages/list<br/>List messages\")
        M_SEARCH(\"/POST /v3/workspaces/{id}/sessions/{s_id}/messages/search<br/>Search messages\")
    end
    
    %% Force vertical arrangement
    W_POST ~~~ P_CREATE
    P_CREATE ~~~ S_CREATE
    S_CREATE ~~~ M_CREATE
```

### 4.2 Request/Response Flow

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant API as FastAPI Router
    participant Auth as JWT Auth
    participant CRUD as CRUD Layer
    participant DB as PostgreSQL
    participant Queue as Task Queue

    Client->>API: POST /v3/workspaces/{w}/sessions/{s}/messages
    API->>Auth: Validate JWT
    Auth-->>API: JWT Valid
    API->>CRUD: create_messages()
    CRUD->>DB: INSERT messages
    DB-->>CRUD: Messages created
    CRUD-->>API: Return created messages
    API->>Queue: enqueue(payloads)
    Queue-->>API: Enqueued
    API-->>Client: 201 Created + messages
```

### 4.3 Authentication Flow

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant API as API Gateway
    participant Auth as JWT Validator
    participant Endpoint

    Client->>API: Request with Authorization header
    API->>Auth: require_auth(workspace, peer, session)
    Auth->>Auth: Decode JWT
    Auth->>Auth: Verify workspace match
    alt JWT has admin scope
        Auth-->>API: Authorized (admin)
    else JWT is scoped
        Auth->>Auth: Verify peer matches
        Auth->>Auth: Verify session matches
        Auth-->>API: Authorized (scoped)
    else No auth required
        Auth-->>API: Authorized (no auth)
    end
    API->>Endpoint: Process request
    Endpoint-->>Client: Response
```

---

## 5. Background Processing Pipeline

### 5.1 Queue Architecture

The deriver system processes messages asynchronously through a PostgreSQL-backed queue.

```mermaid
graph TB
    subgraph \"Message Ingestion\"
        MSG[Message Created]
        ENQUEUE[Enqueue Task]
    end

    subgraph \"Queue Table\"
        QUEUE[(QueueItem Table)]
        ACTIVE[(ActiveQueueSession<br/>In-progress tracking)]
    end

    subgraph \"Task Types\"
        REP[\\\"representation<br/>Derive observations\\\"]
        SUM[\\\"summary<br/>Generate session summaries\\\"]
        DREAM[\\\"dream<br/>Consolidate observations\\\"]
        DEL[\\\"deletion<br/>Async deletion tasks\\\"]
        WEBHOOK[\\\"webhook<br/>Deliver webhooks\\\"]
        RECON[\\\"reconciler<br/>Vector sync cleanup\\\"]
    end

    subgraph \"Worker Pool\"
        WORKER1[Deriver Worker 1]
        WORKER2[Deriver Worker 2]
        WORKERN[Deriver Worker N]
    end

    MSG --> ENQUEUE
    ENQUEUE --> QUEUE
    QUEUE --> REP
    QUEUE --> SUM
    QUEUE --> DREAM
    QUEUE --> DEL
    QUEUE --> WEBHOOK
    QUEUE --> RECON
    
    REP --> WORKER1
    SUM --> WORKER1
    DREAM --> WORKER2
    DEL --> WORKERN
    WEBHOOK --> WORKERN
    RECON --> WORKER1
    
    WORKER1 -.-> ACTIVE
    WORKER2 -.-> ACTIVE
```

### 5.2 Queue Item Processing Flow

```mermaid
sequenceDiagram
    autonumber
    participant Worker as Deriver Worker
    participant QM as QueueManager
    participant DB as PostgreSQL
    participant Processor as Task Processor
    participant LLM as LLM Service

    loop Polling Loop
        Worker->>QM: get_next_batch()
        QM->>DB: SELECT unprocessed items
        DB-->>QM: Return queue items
        QM-->>Worker: Return batch

        loop Each Queue Item
            Worker->>Worker: Create ActiveQueueSession
            
            alt Task Type = representation
                Worker->>Processor: process_representation_batch()
                Processor->>DB: Get message context
                DB-->>Processor: Return messages
                Processor->>LLM: Generate observations
                LLM-->>Processor: Observations
                Processor->>DB: Save documents
            else Task Type = summary
                Worker->>Processor: process_summary()
                Processor->>DB: Get recent messages
                Processor->>LLM: Generate summary
                Processor->>DB: Store summary metadata
            else Task Type = dream
                Worker->>Processor: process_dream()
                Processor->>Processor: Run specialists
            else Task Type = deletion
                Worker->>Processor: process_deletion()
                Processor->>DB: Cascade delete
            end
            
            Worker->>DB: Mark item processed
            Worker->>DB: Remove ActiveQueueSession
        end
    end
```

### 5.3 Representation Task Flow with LLM Processing

```mermaid
flowchart TD
    START([Queue Item Received]) --> CONFIG{Resolve Configuration}
    CONFIG -->|reasoning.enabled=false| SKIP[Skip Processing]
    CONFIG -->|reasoning.enabled=true| OBSERVE{Check observe_me}
    
    OBSERVE -->|false| SKIP
    OBSERVE -->|true| COLLECT[Collect Observers]
    
    COLLECT --> PEERS[Get Session Peers]
    PEERS --> SELF_OBS[Add Self-observation]
    PEERS --> PEER_OBS[Add Peer-observations
    observe_others=true]
    
    SELF_OBS --> BATCH[Create Batch Task]
    PEER_OBS --> BATCH
    
    BATCH --> FETCH[Fetch Message Context
Include interleaved messages]
    FETCH --> FORMAT[Format Messages
with Timestamps]
    
    FORMAT --> TOKEN_CALC[Calculate Input Tokens
Estimate prompt + messages]
    TOKEN_CALC --> LLM_CALL[\\\"Minimal Deriver LLM Call
JSON Mode + Structured Output\\\"]
    
    subgraph \\\"LLM Call Details\\\"
        PROVIDER{Select Provider
Google/Anthropic/OpenAI} --> CONFIG_CALL[Configure Call:
- Model: gemini-2.5-flash-lite
- Max tokens: 4096
- Temperature: null
- Thinking budget: 1024
- JSON mode: true]
        CONFIG_CALL --> RETRY[\\\"Execute with Retry Logic
Max 3 attempts\\\"]
        RETRY --> PARSE_RESP[\\\"Parse Response
Validate JSON Schema\\\"]
        PARSE_RESP --> TOKEN_TRACK[\\\"Track Token Usage
Input/Output/Thinking\\\"]
    end
    
    LLM_CALL --> PARSE[Parse PromptRepresentation
Extract explicit + deductive observations]
    
    PARSE --> SAVE{Save Observations}
    SAVE -->|for each observer| SAVE_COLL[Save to Collection
Deduplicate if enabled]
    SAVE_COLL --> INDEX[Vector Index Update]
    
    INDEX --> CHECK_DREAM{Check Dream Schedule
Document threshold met?}
    CHECK_DREAM -->|yes| SCHED_DREAM[Schedule Dream Task]
    CHECK_DREAM -->|no| DONE
    SCHED_DREAM --> DONE([Done])
    SKIP --> DONE
```

**LLM Configuration for Deriver:**

| Parameter | Value | Purpose |
|-----------|-------|---------|
| Provider | Google (default) | Cost-efficient bulk processing |
| Model | gemini-2.5-flash-lite | Fast inference for observation extraction |
| Max Output Tokens | 4096 | Cap observation generation |
| Thinking Budget | 1024 tokens | Allow reasoning within limits |
| Temperature | null | Deterministic output preferred |
| JSON Mode | true | Structured output validation |
| Retry Attempts | 3 | Handle transient failures |
| Stop Sequences | [\\\"   \\n\\\", \\\"\\n\\n\\n\\n\\\"] | Prevent hallucinated continuations |

---

## 6. Agent Systems

### 6.1 Agent Architecture Overview

Honcho employs three specialized agent types that work together:

```mermaid
graph TB
    subgraph \\\"Agent Ecosystem\\\"
        direction TB
        
        subgraph \\\"Deriver Agent\\\"
            DERIVER[\\\"📝 Deriver<br/>Memory Formation\\\"]
            DERIVER_DESC[\\\"Extracts observations from messages<br/>Creates explicit & deductive facts<br/>Updates peer cards\\\"]
        end
        
        subgraph \\\"Dialectic Agent\\\"
            DIALECTIC[\\\"🔍 Dialectic<br/>Analysis & Recall\\\"]
            DIALECTIC_DESC[\\\"Answers questions about peers<br/>Uses tools to gather context<br/>Creates deductive observations\\\"]
        end
        
        subgraph \\\"Dreamer Agents\\\"
            DREAMER[\\\"💭 Dreamer<br/>Consolidation\\\"]
            DEDUCTION[\\\"Deduction Specialist<br/>Logical inference\\\"]
            INDUCTION[\\\"Induction Specialist<br/>Pattern recognition\\\"]
        end
    end
    
    subgraph \\\"Shared Infrastructure\\\"
        TOOLS[\\\"Tool Executor
 create_observations, search_memory, etc.\\\"]
        CLIENTS[\\\"LLM Clients
Multi-provider support\\\"]
        TELEMETRY[\\\"Telemetry Events\\\"]
    end
    
    DERIVER --> TOOLS
    DIALECTIC --> TOOLS
    DEDUCTION --> TOOLS
    INDUCTION --> TOOLS
    
    DERIVER --> CLIENTS
    DIALECTIC --> CLIENTS
    DREAMER --> CLIENTS
    
    DERIVER --> TELEMETRY
    DIALECTIC --> TELEMETRY
    DREAMER --> TELEMETRY
```

### 6.2 Deriver Agent - LLM Memory Formation

The Deriver is a minimal-overhead memory formation agent optimized for high-throughput batch processing.

**Characteristics:**
- Single LLM call per message batch (no tool calls for efficiency)
- JSON mode with structured Pydantic output (PromptRepresentation)
- Creates explicit observations and simple deductive inferences
- Provider: Google Gemini by default (cost-efficient for bulk processing)
- Updates peer cards with biographical facts

**Detailed LLM Flow:**

```mermaid
flowchart TD
    A[Message Batch Received] --> B[\\\"Format Messages
    Example: 'User (2024-01-15T10:30Z): I live in NYC'\\\"]
    B --> C[\\\"Build System Prompt
    - Peer identity context
    - Instruction to extract facts
    - JSON schema requirements\\\"]
    
    C --> TOKEN_CALC[\\\"Calculate Token Budgets
    - Prompt overhead: ~500 tokens
    - Messages: sum of token_count
    - Max input: 23,000 tokens (configurable)\\\"]
    
    TOKEN_CALC --> D[\\\"Execute honcho_llm_call
    Provider: Google
    Model: gemini-2.5-flash-lite\\\"]
    
    subgraph \\\"LLM Request Configuration\\\"
        D --> CFG[\\\"Configure Parameters:
        - max_tokens: 4096
        - temperature: null
        - response_model: PromptRepresentation
        - json_mode: true
        - stop_seqs: ['   \\n', '\\n\\n\\n\\n']
        - thinking_budget_tokens: 1024
        - enable_retry: true
        - retry_attempts: 3\\\"]
    end
    
    CFG --> E[\\\"Parse Structured Output
    Validate against Pydantic schema:
    - explicit: list[str]
    - deductive: list[DeductiveObservation]\\\"]
    
    E --> F{Validation Success?}
    F -->|Yes| G[\\\"Create Observations
    - Explicit: direct facts
    - Deductive: with premise linkage\\\"]
    F -->|No| RETRY[\\\"Retry with exponential backoff
    Log validation error\\\"]
    RETRY --> D
    
    G --> H{\\\"Batch Token Threshold Met?
    (if FLUSH_ENABLED, skip)\\\"}
    H -->|Yes| FLUSH[\\\"Flush Current Work Unit
    Process accumulated batch\\\"]
    H -->|No| ACCUM[\\\"Accumulate in Work Unit
    Await more messages\\\"]
    FLUSH --> I[Save to Collections]
    ACCUM --> I
    
    I --> TELEM[\\\"Emit RepresentationCompletedEvent
    with token counts, timing\\\"]
```

**Deriver LLM Response Format:**

```json
{
  \\\"explicit\\\": [
    \\\"User mentioned living in New York City\\\",
    \\\"User works as a software engineer at Google\\\"
  ],
  \\\"deductive\\\": [
    {
      \\\"conclusion\\\": \\\"User has software engineering skills\\\",
      \\\"premises\\\": [\\\"User works as a software engineer at Google\\\"],
      \\\"confidence\\\": \\\"high\\\"
    }
  ]
}
```

### 6.3 Dialectic Agent - Detailed LLM Process

The Dialectic answers natural language queries about peers using iterative tool-based context gathering with configurable reasoning depth.

**Characteristics:**
- Multi-iteration tool calling (configurable: 1-10 iterations based on reasoning level)
- Prefetches semantically relevant observations using embeddings
- Can create new deductive observations during reasoning
- Supports streaming and non-streaming responses
- Five reasoning levels with different LLM configurations

```mermaid
flowchart TD
    A[User Query] --> B[Initialize Session History
    Inject peer cards + session context]
    B --> C[Prefetch Semantically Relevant Observations
    Separate explicit + derived searches]
    C --> D{Select Reasoning Level}
    
    D -->|minimal| MIN[\\\"Level: minimal
    Model: gemini-2.5-flash-lite
    Max iterations: 1
    Tools: search_memory, search_messages\\\"]
    
    D -->|low| LOW[\\\"Level: low
    Model: gemini-2.5-flash-lite
    Max iterations: 5
    Tools: All dialectic tools\\\"]
    
    D -->|medium| MED[\\\"Level: medium
    Model: claude-haiku-4-5
    Max iterations: 2
    Thinking budget: 1024\\\"]
    
    D -->|high| HIGH[\\\"Level: high
    Model: claude-haiku-4-5
    Max iterations: 4
    Thinking budget: 1024\\\"]
    
    D -->|max| MAX[\\\"Level: max
    Model: claude-haiku-4-5
    Max iterations: 10
    Thinking budget: 2048\\\"]
    
    MIN --> LOOP[\\\"Tool Iteration Loop\\\"]
    LOW --> LOOP
    MED --> LOOP
    HIGH --> LOOP
    MAX --> LOOP
```

```mermaid
flowchart TD
    LOOP[\\\"Start Iteration Loop\\\"] --> CHECK{\\\"Iteration < Max AND
    Last response had tool calls?\\\"}
    
    CHECK -->|Yes| PREPARE[\\\"Prepare LLM Call\\\"]
    PREPARE --> SETUP[\\\"Configure Provider:
    - Anthropic: tool_choice, thinking budget
    - OpenAI: tool_choice
    - Google: response_modalities\\\"]
    
    SETUP --> CALL[\\\"Execute honcho_llm_call
    with tool_executor\\\"]
    
    CALL --> PROCESS_RESP[\\\"Process Response\\\"]
    PROCESS_RESP --> EXTRACT[\\\"Extract Tool Calls
    from finish_reason == 'tool_calls'\\\"]
    
    EXTRACT --> ITERATE[\\\"Increment iteration
    Track tokens\\\"]
    ITERATE --> CHECK
    
    CHECK -->|No| SYNTHESIZE[\\\"Synthesize Final Response\\\"]
    SYNTHESIZE --> CACHE[\\\"Extract Thinking Content
    for telemetry\\\"]
    CACHE --> RETURN[\\\"Return Response
    with metadata\\\"]
    
    subgraph \\\"Error Handling\\\"
        RETRY[\\\"Retry with backoff
    Max 3 attempts\\\"]
        FALLBACK[\\\"Switch to backup provider
    if configured\\\"]
    end
    
    CALL -.->|Failure| FALLBACK
    FALLBACK --> RETRY
    RETRY --> CALL
```

**LLM Call Sequence for Dialectic:**

```mermaid
sequenceDiagram
    autonumber
    participant Agent as DialecticAgent
    participant LLM as honcho_llm_call
    participant Provider as LLM Provider
    participant Tool as Tool Executor
    participant Telemetry as Telemetry Emitter
    
    Agent->>Agent: Build messages array
    Agent->>Agent: Get level-specific settings
    
    loop Until max iterations or no tool calls
        Agent->>LLM: Call with messages + tools
        Note over Agent,LLM: Include tool_executor callback
        
        LLM->>Provider: Send request with tool definitions
        Provider-->>LLM: Response (content + tool_calls?)
        
        alt Response contains tool calls
            LLM->>Tool: Execute tool function
            Tool-->>LLM: Return tool results
            LLM->>LLM: Append to messages
            LLM->>Telemetry: Track tool call
        else Final response
            LLM-->>Agent: Return synthesized response
        end
    end
    
    Agent->>Telemetry: Emit DialecticCompletedEvent
    Agent-->>User: Return response
```

**Dialectic LLM Configuration by Level:**

| Level | Provider | Model | Max Tokens | Thinking Budget | Tool Choice | Max Iterations |
|-------|----------|-------|------------|-----------------|-------------|----------------|
| **minimal** | Google | gemini-2.5-flash-lite | 250 | 0 | \"any\" | 1 |
| **low** | Google | gemini-2.5-flash-lite | 8192 | 0 | \"any\" | 5 |
| **medium** | Anthropic | claude-haiku-4-5 | 8192 | 1024 | null | 2 |
| **high** | Anthropic | claude-haiku-4-5 | 8192 | 1024 | null | 4 |
| **max** | Anthropic | claude-haiku-4-5 | 8192 | 2048 | null | 10 |

### 6.4 Dreamer Specialists - Agent-Based Consolidation

Dream processing runs autonomous agent specialists that consolidate observations through iterative exploration and creation.

**Phase 0 (Optional): Surprisal Sampling (Pre-LLM)**

```mermaid
flowchart TD
    A[\\\"Start Dream Cycle\\\"] --> B{\\\"Surprisal Enabled?\\\"}
    B -->|Yes| C[\\\"Build Embedding Tree\\\"]
    C --> C2[\\\"Using configured tree type:\\\"]
    C2 --> C3[\\\"- kdtree (default)\\\"]
    C2 --> C4[\\\"- balltree, rptree\\\"]
    C2 --> C5[\\\"- covertree, lsh\\\"]
    
    C5 --> D[\\\"Sample Observations\\\"]
    D --> D2[\\\"- Recent: Last N observations\\\"]
    D --> D3[\\\"- Random: Sample set\\\"]
    D --> D4[\\\"- All: Full collection\\\"]
    
    D4 --> E[\\\"Compute Surprisal Scores\\\"]
    E --> E2[\\\"Geometric approach per tree type\\\"]
    E2 --> E3[\\\"Higher score = more unusual\\\"]
    
    E3 --> F[\\\"Filter Top 10% High-Surprisal\\\"]
    F --> G[\\\"Create Exploration Hints\\\"]
    G --> G2[\\\"Max 10 hints as search queries\\\"]
    
    B -->|No| H[\\\"No Hints\\\"]
    H --> H2[\\\"Specialists explore freely\\\"]
    
    G2 --> I[\\\"Phase 1: Deduction\\\"]
    H2 --> I
```

**Phase 1-2: Specialist LLM Execution**

```mermaid
flowchart TD
    subgraph \\\"Deduction Specialist\\\"
        D_START[\\\"Start Specialist\\\"] --> D2[\\\"Run ID - uuid\\\"]
        D2 --> D_SETUP[\\\"Configure LLM\\\"]
        D_SETUP --> D_SETUP2[\\\"Model: DEDUCTION_MODEL\\\"]
        D_SETUP2 --> D_SETUP3[\\\"Default: claude-haiku-4-5\\\"]
        D_SETUP3 --> D_SETUP4[\\\"Max tokens: 8192\\\"]
        D_SETUP4 --> D_SETUP5[\\\"Max iterations: 12\\\"]
        
        D_SETUP5 --> D_PROMPT[\\\"Build System Prompt\\\"]
        D_PROMPT --> D_P1[\\\"- Instructions for deduction\\\"]
        D_P1 --> D_P2[\\\"- Tool definitions\\\"]
        D_P2 --> D_P3[\\\"- Peer card pre-fetched\\\"]
        
        D_P3 --> D_USER[\\\"Build User Prompt\\\"]
        D_USER --> D_U1[\\\"- Exploration hints if any\\\"]
        D_U1 --> D_U2[\\\"- Current peer card\\\"]
        D_U2 --> D_U3[\\\"- Instructions to explore\\\"]
        
        D_U3 --> D_LOOP[\\\"Begin Tool Loop\\\"]
        
        D_LOOP --> D_ITER[\\\"Iteration N\\\"]
        D_ITER --> D_ITER2[\\\"Call LLM with available tools\\\"]
        
        D_ITER2 --> D_EXEC[\\\"Execute Tools\\\"]
        D_EXEC --> D_E1[\\\"- get_recent_observations\\\"]
        D_E1 --> D_E2[\\\"- search_memory\\\"]
        D_E2 --> D_E3[\\\"- search_messages\\\"]
        D_E3 --> D_E4[\\\"- create_observations\\\"]
        D_E4 --> D_E5[\\\"- delete_observations\\\"]
        D_E5 --> D_E6[\\\"- update_peer_card\\\"]
        
        D_E6 --> D_CHECK{\\\"More tool calls or Max iterations?\\\"}
        
        D_CHECK -->|Yes| D_LOOP
        D_CHECK -->|No| D_COMPLETE[\\\"Deduction Complete\\\"]
    end
    
    subgraph \\\"Induction Specialist\\\"
        I_START[\\\"Start Specialist\\\"] --> I2[\\\"Same run_id\\\"]
        
        I2 --> I_SETUP[\\\"Configure LLM\\\"]
        I_SETUP --> I_SETUP2[\\\"Model: INDUCTION_MODEL\\\"]
        I_SETUP2 --> I_SETUP3[\\\"Default: claude-haiku-4-5\\\"]
        I_SETUP3 --> I_SETUP4[\\\"Max tokens: 16384\\\"]
        I_SETUP4 --> I_SETUP5[\\\"Max iterations: 15\\\"]
        
        I_SETUP5 --> I_PROMPT[\\\"Build System Prompt\\\"]
        I_PROMPT --> I_P1[\\\"- Pattern recognition focus\\\"]
        I_P1 --> I_P2[\\\"- Tool access\\\"]
        I_P2 --> I_P3[\\\"- Peer card updated from deduction\\\"]
        
        I_P3 --> I_USER[\\\"Build User Prompt\\\"]
        I_USER --> I_U1[\\\"- Same exploration hints\\\"]
        I_U1 --> I_U2[\\\"- Current peer card\\\"]
        I_U2 --> I_U3[\\\"- Access to new deductive obs\\\"]
        
        I_U3 --> I_LOOP[\\\"Begin Tool Loop\\\"]
        
        I_LOOP --> I_ITER[\\\"Iteration N\\\"]
        I_ITER --> I_ITER2[\\\"Call LLM with induction focus\\\"]
        
        I_ITER2 --> I_EXEC[\\\"Execute Tools\\\"]
        I_EXEC --> I_E1[\\\"- Observations with pattern_type\\\"]
        I_E1 --> I_E2[\\\"- create_observations inductive\\\"]
        I_E2 --> I_E3[\\\"- update_peer_card\\\"]
        
        I_E3 --> I_CHECK{\\\"More tool calls or Max iterations?\\\"}
        
        I_CHECK -->|Yes| I_LOOP
        I_CHECK -->|No| I_COMPLETE[\\\"Induction Complete\\\"]
    end
    
    D_COMPLETE --> I_START
    I_COMPLETE --> TELEM[\\\"Emit DreamRunEvent with aggregated metrics\\\"]
```

**Specialist LLM Tool Calling Flow:**

```mermaid
sequenceDiagram
    autonumber
    participant Specialist as BaseSpecialist
    participant LLM as honcho_llm_call
    participant Provider as LLM Provider
    participant Executor as Tool Executor
    participant DB as PostgreSQL
    
    Specialist->>Specialist: Build messages array
    Specialist->>Specialist: Create tool_executor with context
    
    loop Until max iterations
        Specialist->>LLM: Call with messages + tools
        Note over Specialist,LLM: tool_executor passed as callback
        
        LLM->>Provider: Send request
        Provider-->>LLM: Response
        
        alt Response has tool_calls
            LLM->>LLM: Extract tool calls array
            
            loop Each Tool Call
                LLM->>Executor: Execute(tool_name, arguments)
                Executor->>DB: Query/Update database
                Executor-->>LLM: Return tool result
            end
            
            LLM->>LLM: Append results to messages
            LLM->>Specialist: Continue to next iteration
        else Final response
            LLM-->>Specialist: Return synthesized content
        end
    end
    
    Specialist->>Specialist: Emit telemetry event
```

**Dreamer LLM Configuration:**

| Parameter | Deduction Specialist | Induction Specialist |
|-----------|---------------------|---------------------|
| Provider | Anthropic (default) | Anthropic (default) |
| Model | DEDUCTION_MODEL | INDUCTION_MODEL |
| Default Model | claude-haiku-4-5 | claude-haiku-4-5 |
| Max Output Tokens | 8,192 | 16,384 |
| Max Iterations | 12 | 15 |
| Thinking Budget | 8,192 | 8,192 |
| Tools Available | Discovery + Action | Discovery + Creation |
| Peer Card Update | Yes (biographical facts) | Yes (patterns/traits) |

---

## 6.5 LLM Client Architecture

Honcho abstracts LLM interactions through a unified client layer (`honcho_llm_call`) that supports multiple providers with consistent interface.

### 6.5.1 Unified LLM Call Interface

```mermaid
flowchart TD
    CALLER[\\\"Caller (Agent/Deriver/Dialectic/Dreamer)\\\"] --> INTERFACE[\\\"honcho_llm_call()\\\"]
    
    INTERFACE --> PARAMS[\\\"Extract Parameters:
    - llm_settings (provider, model)
    - prompt OR messages
    - max_tokens
    - tools (optional)
    - tool_executor (optional)
    - temperature
    - thinking_budget_tokens
    - json_mode
    - stream\\\"]
    
    PARAMS --> PROVIDER{Select Provider}
    
    PROVIDER -->|anthropic| A[\\\"Anthropic Client
Claude models
Extended thinking\\\"]
    
    PROVIDER -->|openai| O[\\\"OpenAI Client
GPT models
Function calling\\\"]
    
    PROVIDER -->|google| G[\\\"Google Client
Gemini models
JSON mode\\\"]
    
    PROVIDER -->|groq| GR[\\\"Groq Client
Fast inference
Limited tool support\\\"]
    
    PROVIDER -->|vllm| V[\\\"vLLM Client
Local deployment
OpenAI-compatible\\\"]
    
    A --> UNIFIED[\\\"Unified Response Format:
    - content: str
    - input_tokens: int
    - output_tokens: int
    - thinking_content: str?
    - tool_calls_made: list
    - iterations: int\\\"]
    
    O --> UNIFIED
    G --> UNIFIED
    GR --> UNIFIED
    V --> UNIFIED
    
    UNIFIED --> RETURN[\\\"Return to Caller\\\"]
```

### 6.5.2 Tool Calling Loop Architecture

When tools are provided, the LLM client manages an iterative tool execution loop:

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant LLM as honcho_llm_call
    participant Handler as Provider Handler
    participant ToolExec as Tool Executor
    participant Telemetry
    
    Caller->>LLM: Call with tools + tool_executor
    Note over Caller,LLM: max_tool_iterations=N
    
    LLM->>Handler: Send initial request
    Handler-->>LLM: Response
    
    LLM->>LLM: Check finish_reason
    
    alt finish_reason == 'tool_calls'
        loop Up to max_tool_iterations
            LLM->>LLM: Parse tool_calls array
            
            par Parallel Tool Execution
                LLM->>ToolExec: Execute tool 1
                ToolExec-->>LLM: Result 1
                
                LLM->>ToolExec: Execute tool 2
                ToolExec-->>LLM: Result 2
            end
            
            LLM->>LLM: Build messages with results
            LLM->>Telemetry: Emit tool execution event
            
            LLM->>Handler: Send follow-up request
            Handler-->>LLM: Response
            
            LLM->>LLM: Check finish_reason
            
            alt finish_reason != 'tool_calls'
                LLM->>LLM: Break loop
            end
        end
    end
    
    LLM->>LLM: Extract thinking content (if available)
    LLM->>LLM: Count tokens
    LLM-->>Caller: Return HonchoLLMCallResponse
```

### 6.5.3 Provider-Specific Configurations

| Feature | Anthropic | OpenAI | Google | Groq |
|---------|-----------|--------|--------|------|
| **Tool Choice** | `tool_choice` (any/required) | `tool_choice` | Not supported | Not supported |
| **Thinking Budget** | `thinking.budget_tokens` | Not supported | Not supported | Not supported |
| **JSON Mode** | Tool-based or prompt | `response_format: {type: \\\"json_object\\\"}` | `response_mime_type: application/json` | Prompt-based |
| **Structured Output** | Pydantic via tool | `response_format` with schema | Pydantic via schema | Not supported |
| **Streaming** | Yes | Yes | Yes | Limited |
| **Caching** | Prompt caching (beta) | Not supported | Not supported | Not supported |
| **Backup Provider** | Yes | Yes | Yes | No |

### 6.5.4 Retry and Fallback Logic

```mermaid
flowchart TD
    A[LLM Call Initiated] --> B{Check enable_retry}
    B -->|No| CALL[\\\"Single Attempt\\\"]
    B -->|Yes| RETRY[\\\"Setup Retry Loop
    Max attempts: 3\\\"]
    
    CALL --> EXEC[\\\"Execute Request\\\"]
    RETRY --> EXEC
    
    EXEC --> RESULT{Result}
    
    RESULT -->|Success| RETURN[\\\"Return Response\\\"]
    
    RESULT -->|Rate Limit| BACKOFF[\\\"Exponential Backoff
    Wait: 2^attempt * jitter\\\"]
    
    RESULT -->|Auth Error| FALLBACK[\\\"Try Backup Provider
    if configured\\\"]
    
    RESULT -->|Parse Error| FALLBACK
    
    RESULT -->|Other Error| BACKOFF
    
    BACKOFF --> RETRY2{\\\"Attempts left?\\\"}
    FALLBACK --> RETRY2
    
    RETRY2 -->|Yes| EXEC
    RETRY2 -->|No| ERROR[\\\"Return Error
    Log to Sentry\\\"]
    
    RETURN --> DONE([Complete])
    ERROR --> DONE
```

### 6.5.5 Token Tracking and Limits

```mermaid
graph LR
    subgraph \\\"Input Token Calculation\\\"
        A[Prompt/Messages] --> B[Add System Prompt]
        B --> C[Add Tool Definitions]
        C --> D[Calculate Total]
    end
    
    subgraph \\\"Budget Management\\\"
        D --> E{Input < Max Input?}
        E -->|Yes| F[Proceed with Call]
        E -->|No| G[\\\"Truncate History
    (Remove oldest messages)\\\"]
        G --> E
    end
    
    subgraph \\\"Output Management\\\"
        F --> H[Execute LLM Call]
        H --> I[Receive Response]
        I --> J[Count Output Tokens]
        J --> K[Cache Token Info]
    end
```

### 6.5.6 Response Streaming

For streaming responses (Dialectic chat), the LLM client manages a two-phase process:

```mermaid
flowchart TD
    A[Streaming Request] --> B[\\\"Phase 1: Tool Loop
    Execute all tool calls first
    Non-streaming\\\"]
    
    B --> C{\\\"Tools needed?\\\"}
    C -->|Yes| D[\\\"Execute Tools
    Gather all context\\\"]
    D --> E[Rebuild messages with tool results]
    E --> F[\\\"Phase 2: Final LLM Call
    stream=true\\\"]
    C -->|No| F
    
    F --> G[\\\"Stream Response Chunks\\\"]
    G --> H[\\\"Accumulate Content
    Track tokens\\\"]
    H --> I[Return StreamingWithMetadata]
    
    I --> CLIENT[\\\"Client receives:
    - Async iterator of chunks
    - Metadata on completion
    (tokens, thinking content)\\\"]
```

### 6.5.7 LLM Provider Settings by Component

```mermaid
graph TB
    subgraph \\\"LLM Call Configuration Matrix\\\"
        DERIVER[\\\"Deriver Agent
        Provider: DERIVER.PROVIDER
        Model: DERIVER.MODEL
        Features:
        - JSON mode: true
        - Structured output
        - No tools\\\"]
        
        DIALECTIC_MIN[\\\"Dialectic (minimal/low)
        Provider: DIALECTIC.LEVELS[level].PROVIDER
        Model: gemini-2.5-flash-lite
        Features:
        - Tool calling
        - 1-5 iterations\\\"]
        
        DIALECTIC_HIGH[\\\"Dialectic (medium/high/max)
        Provider: DIALECTIC.LEVELS[level].PROVIDER
        Model: claude-haiku-4-5
        Features:
        - Tool calling
        - Thinking budget
        - 2-10 iterations\\\"]
        
        SUMMARY[\\\"Session Summary
        Provider: SUMMARY.PROVIDER
        Model: gemini-2.5-flash
        Features:
        - JSON mode available
        - Token-aware limits\\\"]
        
        DREAM_DEDUCTION[\\\"Deduction Specialist
        Provider: DREAM.PROVIDER
        Model: DREAM.DEDUCTION_MODEL
        Features:
        - Tool calling (15 tools)
        - 12 max iterations\\\"]
        
        DREAM_INDUCTION[\\\"Induction Specialist
        Provider: DREAM.PROVIDER
        Model: DREAM.INDUCTION_MODEL
        Features:
        - Tool calling
        - 15 max iterations\\\"]
    end
```

### 6.5.8 Error Handling and Recovery

| Error Type | Handler Behavior | Fallback Action |
|------------|------------------|-----------------|
| **Rate Limit (429)** | Exponential backoff | Retry up to 3 times |
| **Token Limit (400)** | Truncate context | Retry with reduced input |
| **Auth Error (401/403)** | Immediate fail | Switch to backup provider |
| **Model Overload (503)** | Linear backoff | Retry or use backup |
| **JSON Parse Error** | Log validation error | Retry with same model |
| **Timeout** | Cancel request | Return partial or error |

---

### 7.1 Observation Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Explicit: Message Ingestion
    
    Explicit --> Deductive: Deriver Inference
    Explicit --> Inductive: Pattern Recognition
    Explicit --> Contradiction: Conflict Detection
    
    Deductive --> Inductive: Higher-level Patterns
    Inductive --> Consolidated: Dream Processing
    Deductive --> Consolidated: Dream Processing
    Explicit --> [*]: Pruning/Deletion
    
    Consolidated --> [*]: Archive
    Contradiction --> Resolved: Manual/Agent Resolution
    Resolved --> Consolidated
```

### 7.2 Document Creation with Deduplication

```mermaid
sequenceDiagram
    autonumber
    participant Agent
    participant Lock as Async Lock
    participant Emb as Embedding Service
    participant DB as PostgreSQL
    participant Vec as Vector Store

    Agent->>Emb: Batch embed contents
    Emb-->>Agent: Embeddings

    Agent->>Lock: Acquire (workspace, observer, observed)
    
    Agent->>DB: Begin Transaction
    
    alt Deduplication Enabled
        Agent->>DB: Check existing similar documents
        DB-->>Agent: Existing matches
        
        loop For Each New Document
            Agent->>Agent: Compute similarity
            
            alt Very Similar Exists (>0.95)
                Agent->>DB: Skip Creation
            else Somewhat Similar (0.85-0.95)
                Agent->>DB: Merge/Update existing
            else Novel
                Agent->>DB: INSERT document
            end
        end
    else Deduplication Disabled
        Agent->>DB: Bulk INSERT all documents
    end
    
    Agent->>DB: Commit Transaction
    Agent->>Lock: Release
    
    Agent->>Vec: Async vector sync
```

### 7.3 Peer Card Management

Peer cards store stable biographical facts about peers.

```mermaid
graph TB
    subgraph \\\"Peer Card Facts\\\"
        F1[\\\"Name: Alice Johnson\\\"]
        F2[\\\"Location: NYC\\\"]
        F3[\\\"Occupation: Software Engineer\\\"]
        F4[\\\"INSTRUCTION: Call me AJ\\\"]
        F5[\\\"PREFERENCE: Concise responses\\\"]
        F6[\\\"TRAIT: Detail-oriented\\\"]
    end
    
    subgraph \\\"Sources\\\"
        M1[\\\"Message: 'I'm Alice from NYC'\\\"]
        M2[\\\"Message: 'I work as a SWE'\\\"]
        M3[\\\"Message: 'Please call me AJ'\\\"]
    end
    
    subgraph \\\"Created By\\\"
        D[Deriver] --> F1
        D --> F2
        D --> F3
        DS[Deduction Specialist] --> F4
        DS --> F5
        DS --> F6
    end
    
    M1 -.-> F1
    M1 -.-> F2
    M2 -.-> F3
    M3 -.-> F4
```

---

## 8. Query & Retrieval System

### 8.1 Semantic Search Flow

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant API as API Endpoint
    participant Embed as Embedding Service
    participant CRUD as CRUD Layer
    participant Vec as Vector Store

    Client->>API: Query request with filters
    
    alt Pre-computed embedding
        API->>Embed: Use provided embedding
    else Need embedding
        API->>Embed: Embed query text
    end
    
    Embed-->>API: Query embedding vector
    
    API->>CRUD: query_documents()
    CRUD->>Vec: Vector similarity search
    Vec->>CRUD: Similar documents
    
    alt Filters specified
        CRUD->>CRUD: Apply level/metadata filters
    end
    
    CRUD-->>API: Filtered results
    API->>API: Format as Representation
    API-->>Client: Response
```

### 8.2 Working Representation Assembly

The working representation combines multiple query strategies:

```mermaid
flowchart TD
    A[Request Representation] --> B[Calculate Budgets]
    B --> C{Semantic Query?}
    
    C -->|Yes| D[Semantic Search<br/>~33% of budget]
    C -->|No| E[Skip]
    
    D --> F{Most Derived?}
    E --> F
    
    F -->|Both| G[Derived + Recent<br/>Three-way split]
    F -->|Yes only| H[Derived + Recent<br/>Two-way split]
    F -->|No| I[Recent Only<br/>100% to recent]
    
    G --> J[Merge Results]
    H --> J
    I --> J
    
    J --> K[Deduplicate<br/>Remove overlaps]
    K --> L[Sort by Age/Derivation]
    L --> M[Return Representation]
```

### 8.3 Available Query Methods

| Method | Description | Use Case |
|--------|-------------|----------|
| **Semantic Search** | Vector similarity on observation content | Find relevant observations |
| **Recent Observations** | Most recent by timestamp | Understand recent developments |
| **Most Derived** | By `times_derived` count | Find established facts |
| **Grep Search** | Exact text matching | Find specific phrases |
| **Date Range** | Temporal filtering | Time-bounded queries |
| **Hybrid Search** | Semantic + temporal | Recent mentions of topic |

---

## 9. Session Management

### 9.1 Session Context Retrieval

The `get_session_context` endpoint returns comprehensive session data:

```mermaid
flowchart TD
    A[Context Request] --> B[Token Budget Calculation]
    B --> C[40% for Summary]
    C --> D[60% for Messages]
    
    D --> E{Include Summary?}
    E -->|Yes| F[Fetch Short & Long Summaries]
    F --> G{Select Best Summary}
    G -->|Long fits better| H[Use Long Summary]
    G -->|Short fits better| I[Use Short Summary]
    G -->|None fit| J[No Summary]
    
    H --> K[Adjust Message Budget]
    I --> K
    J --> L[Full Budget for Messages]
    
    K --> M[Fetch Recent Messages]
    L --> M
    
    M --> N{Include Peer Context?}
    N -->|Yes| O[Fetch Representation]
    N -->|Yes| P[Fetch Peer Card]
    N -->|No| Q[Skip]
    
    O --> R[Assemble Response]
    P --> R
    Q --> R
    R --> S[Return SessionContext]
```

### 9.2 Summary Generation with LLM

Session summaries are generated at configurable intervals using LLM calls to condense conversation history.

**Summary Trigger Conditions:**

```mermaid
flowchart TD
    A[Message Created] --> B[Check seq_in_session]
    B --> C{Modulo Check}
    
    C -->|seq % messages_per_short_summary == 0| SHORT[\\\"Trigger SHORT Summary
    Default: Every 20 messages\\\"]
    
    C -->|seq % messages_per_long_summary == 0| LONG[\\\"Trigger LONG Summary
    Default: Every 60 messages\\\"]
    
    C -->|No match| SKIP[\\\"Skip Summary\\\"]
    
    SHORT --> D[\\\"Prepare LLM Call:
    - Provider: SUMMARY.PROVIDER
    - Model: gemini-2.5-flash
    - Max tokens: 1000\\\"]
    
    LONG --> E[\\\"Prepare LLM Call:
    - Provider: SUMMARY.PROVIDER
    - Model: gemini-2.5-flash
    - Max tokens: 4000\\\"]
    
    D --> FORMAT[\\\"Format Messages with Timestamps
    Up to token limit\\\"]
    E --> FORMAT
    
    FORMAT --> LLM_CALL[\\\"Execute honcho_llm_call
    - System prompt: Summarization instructions
    - JSON mode: optional
    - Thinking budget: 512\\\"]
    
    LLM_CALL --> PARSE[\\\"Parse LLM Output\\\"]
    PARSE --> STORE[\\\"Store in internal_metadata:
    - content: summary text
    - message_id: coverage end
    - summary_type: short/long
    - token_count: calculated\\\"]
    
    STORE --> DONE([Complete])
```

**Summary LLM Configuration:**

| Parameter | Short Summary | Long Summary |
|-----------|---------------|--------------|
| Trigger | Every 20 messages | Every 60 messages |
| Provider | Google (default) | Google (default) |
| Model | gemini-2.5-flash | gemini-2.5-flash |
| Max Tokens | 1,000 | 4,000 |
| Thinking Budget | 512 | 512 |
| Temperature | Default | Default |
| JSON Mode | Optional | Optional |

**Two-Tier Summary Strategy:**

```mermaid
timeline
    title Session Summary Generation
    section Init
        Messages 1-20 : Short summary triggered
                      : (after 20 messages)
                      
    section Short
        Messages 21-60 : Short summaries every 20
        
    section Long  
        After 60 messages : Long summary triggered
        Messages 61+      : Long summaries every 60
                         : Short continues every 20
```

### 9.3 Multi-Peer Session Flow

```mermaid
sequenceDiagram
    autonumber
    participant Alice
    participant Bob
    participant System as Honcho System
    participant Queue as Deriver Queue

    Alice->>System: create_session(id=\"meeting-1\")
    System-->>Alice: Session created
    
    Alice->>System: add_peers([\"alice\", \"bob\"])
    System-->>Alice: Peers added
    
    Alice->>System: send_message(\"Let's discuss the project\")
    System->>Queue: Enqueue representation
    Queue->>Queue: Process for Alice→Alice
    Queue->>Queue: Process for Bob→Alice (if observe_others)
    
    Bob->>System: send_message(\"Sure, here's my update...\")
    System->>Queue: Enqueue representation
    Queue->>Queue: Process for Bob→Bob
    Queue->>Queue: Process for Alice→Bob (if observe_others)
    
    Bob->>System: chat(\"What did Alice want to discuss?\")
    System->>System: Query observations
    System-->>Bob: \"Alice wanted to discuss the project\"
```

### 9.4 Context Management & Token Limits

Honcho uses a **three-tier hierarchical approach** to keep agent context below configurable thresholds:

```mermaid
graph TB
    subgraph \\\"Context Management Strategy\\\"
        A[\\\"1. Configuration Level\\\"] --> B[\\\"2. API-Level Budgeting\\\"]
        B --> C[\\\"3. Database-Level Enforced\\\"]
    end
    
    subgraph \\\"Token Budget Allocation\\\"
        D[\\\"Total Budget: 100K tokens default\\\"] 
        E[\\\"40% to Summary\\\"]
        F[\\\"60% to Messages\\\"]
        D --> E
        D --> F
    end
```

#### Configuration-Level Defaults

| Setting | Default | Max | Description |
|---------|---------|-----|-------------|
| `GET_CONTEXT_MAX_TOKENS` | 100,000 | 250,000 | Default limit for context retrieval endpoints |
| `SUMMARY.MAX_TOKENS_SHORT` | 1,000 | 10,000 | Maximum tokens for short summaries |
| `SUMMARY.MAX_TOKENS_LONG` | 4,000 | 20,000 | Maximum tokens for long summaries |

```mermaid
flowchart LR
    subgraph \\\"Configuration Hierarchy\\\"
        A[\\\"Message Config\\\"] --> B[\\\"Session Config\\\"]
        B --> C[\\\"Peer Config\\\"]
        C --> D[\\\"Workspace Config\\\"]
        D --> E[\\\"Default: 100K\\\"]
    end
```

#### API-Level Token Budgeting (40/60 Allocation)

When `/sessions/{id}/context` is called:

```mermaid
flowchart TD
    A[\\\"Context Request Received\\\"] --> B{\\\"Token Limit Specified?\\\"}
    B -->|Yes| C[\\\"Use User-Provided Limit\\\"]
    B -->|No| D[\\\"Use GET_CONTEXT_MAX_TOKENS default\\\"]
    
    C --> E[\\\"Calculate Budgets\\\"]
    D --> E
    
    E --> F[\\\"40% to Summary\\\"]
    E --> G[\\\"60% to Messages\\\"]
    
    F --> H{\\\"Long Summary Fit?\\\"}
    H -->|Yes| I[\\\"Use Long Summary<br/>60 msgs + metadata\\\"]
    H -->|No| J{\\\"Short Summary Fit?\\\"}
    J -->|Yes| K[\\\"Use Short Summary<br/>20 msgs + metadata\\\"]
    J -->|No| L[\\\"Skip Summary<br/>Full budget for messages\\\"]
    
    I --> M[\\\"Adjust Message Budget\\\"]
    K --> M
    L --> N[\\\"Full Budget Available\\\"]
```

#### Database-Level Enforcement

The `_apply_token_limit` function uses SQL window functions to enforce token budgets:

```mermaid
flowchart LR
    A[Database Query] --> B[\\\"Window Function<br/>SUM(token_count)<br/>OVER (ORDER BY id DESC)\\\"]
    B --> C[\\\"WHERE running_sum<br/>&lt;= token_limit\\\"]
    C --> D[\\\"Return Most Recent<br/>Messages Under Budget\\\"]
```

#### Summary Selection Logic

```mermaid
flowchart TD
    A[Fetch Both Summaries] --> B{\\\"Long Summary Exists?\\\"}
    B -->|Yes| C{\\\"Long Len &lt;= 40% Budget?\\\"}
    C -->|Yes| D{\\\"Long Len > Short Len?\\\"}
    D -->|Yes| E[\\\"SELECT Long Summary\\\"]
    D -->|No| F[\\\"Check Short Summary\\\"]
    
    C -->|No| F
    B -->|No| F
    
    F -->|Yes| G[\\\"SELECT Short Summary\\\"]
    G --> D2{\\\"Short Len > 0?\\\"}
    D2 -->|Yes| H[\\\"Use Short and Remaining Budget\\\"]
    D2 -->|No| I[\\\"No Summary and Full Budget\\\"]
```

**Selection Priority:**
1. Long summary if it fits AND is longer than short
2. Short summary if it fits
3. No summary (full token budget for messages)

#### Two-Tier Summary System

| Summary Type | Trigger | Coverage | Token Budget Allocation |
|--------------|---------|----------|------------------------|
| **Short** | Every 20 messages | Last 20 messages | ~1,000 tokens max |
| **Long** | Every 60 messages | Last 60 messages | ~4,000 tokens max |

```mermaid
timeline
    title Session Summary Generation Timeline
    section Message 20
        Short Summary : Generated
                      : Covers 1-20
    section Message 40
        Short Summary : Generated
                      : Covers 21-40
    section Message 60
        Short Summary : Generated
        Long Summary  : Covers 1-60
                      : (supersedes shorts)
    section Continuing
        Every 20 msgs : Short summary
        Every 60 msgs : Long summary
```

#### Agent-Specific Context Limits

Each Honcho agent has its own internal token limits:

| Agent | Context Limit | Notes |
|-------|---------------|-------|
| **Deriver** | 23,000 input / 4,096 output | Batch processing optimized |
| **Dialectic** | 100,000 total (configurable) | Reasoning level affects budget |
| **Dreamer** | 50,000 input / 8,192 output | Specialist-specific limits |
| **Summarizer** | 10,000 input / 4,000 output | Concise output focus |

---

## 10. Configuration System

### 10.1 Hierarchical Configuration Resolution

Honcho resolves configuration using a hierarchical system that merges settings from multiple levels:

```mermaid
flowchart TD
    subgraph \\\"Configuration Resolution Order (Most Specific First)\\\"
        MSG[Message Config<br/>metadata.configuration] --> SESS[Session Config<br/>internal_metadata.configuration]
        SESS --> PEER[Peer Config<br/>internal_metadata.configuration]
        PEER --> WS[Workspace Config<br/>configuration]
        WS --> GLOBAL[Global Config<br/>.env + config.toml]
        GLOBAL --> DEFAULT[Hardcoded Defaults]
    end
    
    subgraph \\\"Merge Strategy\\\"
        MERGE[\\\"Deep Merge with<br/>Type Coercion and Validation\\\"]
        MSG --> MERGE
        SESS --> MERGE
        PEER --> MERGE
        WS --> MERGE
        GLOBAL --> MERGE
        DEFAULT --> MERGE
        MERGE --> EFFECTIVE[\\\"Effective Configuration<br/>for Operation\\\"]
    end
```

### 10.2 Configuration Validation

All configuration values are validated using Pydantic models with custom validators:

```mermaid
flowchart TD
    RAW[Raw Config Input] --> PARSE[Pydantic Parse]
    PARSE --> VALIDATE[Field Validators]
    
    subgraph \\\"Validation Types\\\"
        TYPE[Type Coercion<br/>str to int, etc.]
        RANGE[Range Validation<br/>0 <= value <= max]
        SCHEMA[JSON Schema<br/>Required fields]
        CUSTOM[Custom Logic<br/>Provider compatibility]
    end
    
    VALIDATE --> TYPE
    VALIDATE --> RANGE
    VALIDATE --> SCHEMA
    VALIDATE --> CUSTOM
    
    TYPE --> VALID[Valid Config]
    RANGE --> VALID
    SCHEMA --> VALID
    CUSTOM --> VALID
    
    VALID --> APPLY[Apply to Settings]
    APPLY --> OVERRIDE[Allow Runtime Override]
    
    OVERRIDE --> FINAL[Final Validated Config]
```

### 10.3 Environment Variable Mapping

Honcho uses a structured environment variable prefix system:

| Component | Prefix | Example |
|-----------|--------|---------|
| **Database** | `DB_` | `DB_POOL_SIZE=10` |
| **LLM General** | `LLM_` | `LLM_VLLM_BASE_URL=http://localhost:11434` |
| **Deriver** | `DERIVER_` | `DERIVER_MODEL=kimi-k2.5:cloud` |
| **Dialectic** | `DIALECTIC_LEVELS_` | `DIALECTIC_LEVELS_minimal_MODEL=gemini-2.5-flash-lite` |
| **Summary** | `SUMMARY_` | `SUMMARY_MODEL=gemini-2.5-flash` |
| **Dream** | `DREAM_` | `DREAM_DOCUMENT_THRESHOLD=25` |
| **Cache** | `CACHE_` | `CACHE_ENABLED=true` |
| **Embedding** | `LLM__EMBEDDING_` | `LLM__OLLAMA_EMBEDDING_MODEL=bge-m3` |

### 10.4 TOML Configuration Structure

Configuration can also be specified in `config.toml`:

```toml
[database]
connection_uri = \"postgresql+psycopg://user:pass@localhost:5432/honcho\"
pool_size = 10
max_overflow = 20

[llm]
default_provider = \"google\"
default_model = \"gemini-2.5-flash-lite\"

[llm.providers.anthropic]
api_key = \"${ANTHROPIC_API_KEY}\"
base_url = \"https://api.anthropic.com\"

[deriver]
provider = \"google\"
model = \"gemini-2.5-flash-lite\"
max_output_tokens = 4096
workers = 4

[dialectic.levels.minimal]
provider = \"google\"
model = \"gemini-2.5-flash-lite\"
max_tool_iterations = 1

[cache]
enabled = true
url = \"redis://localhost:6379/0\"
namespace = \"honcho\"
default_ttl_seconds = 300

[embedding]
provider = \"ollama\"
model = \"bge-m3\"
max_tokens = 8192
```

### 10.5 Dynamic Configuration Updates

Configuration can be updated at runtime through API calls:

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Config as Config Manager
    participant Validator as Pydantic Validator
    
    Client->>API: PUT /v3/workspaces/{id}/peers/{peer}/configuration
    API->>Validator: Validate new config
    Validator-->>API: Valid
    
    API->>Config: Update peer configuration
    Config->>Config: Merge with hierarchy
    Config-->>API: Updated config
    
    API-->>Client: 200 OK + new config
    
    Note over Client,Config: Changes take effect immediately
    for new requests
```

### 10.6 Feature Flags

Honcho supports feature flags at workspace, peer, and session levels:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `reasoning.enabled` | bool | true | Enable LLM-based observation creation |
| `observe_others` | bool | false | Create observations about other peers |
| `observe_me` | bool | true | Create self-observations |
| `deduplication.enabled` | bool | true | Deduplicate similar observations |
| `vector_sync.enabled` | bool | true | Async vector indexing |
| `dreaming.enabled` | bool | true | Run scheduled dream processing |
| `telemetry.enabled` | bool | true | Emit CloudEvents telemetry |

---

## 11. Security & Authentication

### 11.1 JWT Authentication System

Honcho uses JWT tokens with scoped permissions:

```mermaid
graph TB
    subgraph \\\"JWT Token Types\\\"
        ADMIN[\\\"Admin Token<br/>Workspace-wide access\\\"]
        PEER[\\\"Peer Token<br/>Peer-scoped access\\\"]
        SESSION[\\\"Session Token<br/>Session-scoped access\\\"]
    end
    
    subgraph \\\"Scopes\\\"
        READ[Read Operations]
        WRITE[Write Operations]
        ADMIN_OP[Admin Operations]
    end
    
    ADMIN --> READ
    ADMIN --> WRITE
    ADMIN --> ADMIN_OP
    
    PEER --> READ
    PEER --> WRITE
    
    SESSION --> READ
    SESSION --> WRITE
```

### 11.2 Scoped JWT Creation

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant Auth as JWT Creator
    participant Validator as Config Validator
    
    Client->>API: POST /v3/keys
    API->>Validator: Validate scopes
    Validator-->>API: Valid
    
    API->>Auth: Create JWT
    Auth->>Auth: Set claims:
    - iss: honcho
    - sub: peer_id
    - aud: workspace_id
    - scope: read,write
    - exp: 24h from now
    - jti: unique id
    
    Auth-->>API: Signed JWT
    API-->>Client: API key response
```

### 11.3 Request Authentication Flow

```mermaid
sequenceDiagram
    participant Client
    participant Middleware as JWT Middleware
    participant Router as API Router
    participant Endpoint as Target Endpoint
    
    Client->>Middleware: Request with Authorization: Bearer {token}
    Middleware->>Middleware: Extract token
    Middleware->>Middleware: Verify signature
    Middleware->>Middleware: Check expiration
    
    alt Invalid Token
        Middleware-->>Client: 401 Unauthorized
    else Valid Token
        Middleware->>Middleware: Extract claims
        Middleware->>Middleware: Resolve scopes
        
        alt Admin Scope
            Middleware->>Router: Pass admin privileges
        else Peer/Session Scope
            Middleware->>Router: Pass scoped access
        end
        
        Router->>Endpoint: Process with resolved permissions
        Endpoint-->>Router: Response
        Router-->>Middleware: Response
        Middleware-->>Client: Response
    end
```

### 11.4 Data Isolation by Workspace

All data is strictly isolated by workspace:

```mermaid
graph LR
    subgraph \\\"Workspace A\\\"
        PEER_A1[Peer: alice]
        PEER_A2[Peer: bob]
        SESS_A1[Session: meeting-1]
        OBS_A[Observations A]
    end
    
    subgraph \\\"Workspace B\\\"
        PEER_B1[Peer: charlie]
        PEER_B2[Peer: david]
        SESS_B1[Session: planning-1]
        OBS_B[Observations B]
    end
    
    subgraph \\\"Database\\\"
        DB[(PostgreSQL)]
    end
    
    PEER_A1 -.->|workspace_name=A| DB
    PEER_A2 -.->|workspace_name=A| DB
    SESS_A1 -.->|workspace_name=A| DB
    OBS_A -.->|workspace_name=A| DB
    
    PEER_B1 -.->|workspace_name=B| DB
    PEER_B2 -.->|workspace_name=B| DB
    SESS_B1 -.->|workspace_name=B| DB
    OBS_B -.->|workspace_name=B| DB
    
    classDef isolated fill:#e1f5fe
    class PEER_A1,PEER_A2,SESS_A1,OBS_A,PEER_B1,PEER_B2,SESS_B1,OBS_B isolated
```

### 11.5 Rate Limiting

Honcho implements per-workspace rate limiting:

| Endpoint | Limit (requests/min) | Scope |
|----------|----------------------|-------|
| `/v3/messages` | 100 | Workspace |
| `/v3/chat` | 30 | Peer |
| `/v3/search` | 50 | Workspace |
| `/v3/representation` | 20 | Peer |

---

## 12. Telemetry & Monitoring

### 12.1 CloudEvents Telemetry System

Honcho emits structured CloudEvents for key operations:

```mermaid
graph TB
    subgraph \\\"Telemetry Events\\\"
        REPRESENTATION[\\\"RepresentationCompletedEvent<br/>Deriver processing\\\"]
        DIALECTIC[\\\"DialecticCompletedEvent<br/>Chat reasoning\\\"]
        DREAM[\\\"DreamRunEvent<br/>Consolidation cycle\\\"]
        SUMMARY[\\\"SummaryGeneratedEvent<br/>Session summaries\\\"]
        ERROR[\\\"ErrorEvent<br/>Exception tracking\\\"]
        API[\\\"APIRequestEvent<br/>Endpoint calls\\\"]
    end
    
    subgraph \\\"Sinks\\\"
        SENTRY[Sentry<br/>Error aggregation]
        CONSOLE[Console<br/>Rich logging]
        CLOUDEVENTS[CloudEvents<br/>External systems]
    end
    
    REPRESENTATION --> SENTRY
    REPRESENTATION --> CONSOLE
    DIALECTIC --> CONSOLE
    DIALECTIC --> CLOUDEVENTS
    DREAM --> CONSOLE
    SUMMARY --> CONSOLE
    ERROR --> SENTRY
    ERROR --> CONSOLE
    API --> CONSOLE
```

### 12.2 Event Schema Examples

**RepresentationCompletedEvent:**

```json
{
  \\\"specversion\\\": \\\"1.0\\\",
  \\\"id\\\": \\\"rep-1234-5678\\\",
  \\\"source\\\": \\\"honcho.deriver.worker.1\\\",
  \\\"type\\\": \\\"honcho.events.RepresentationCompleted\\\",
  \\\"time\\\": \\\"2025-03-15T10:30:00Z\\\",
  \\\"data\\\": {
    \\\"workspace_name\\\": \\\"local-honcho\\\",
    \\\"session_name\\\": \\\"pi-session-123\\\",
    \\\"observer\\\": \\\"dsidlo\\\",
    \\\"observed\\\": \\\"dsidlo\\\",
    \\\"message_count\\\": 5,
    \\\"explicit_observations\\\": 3,
    \\\"deductive_observations\\\": 2,
    \\\"input_tokens\\\": 2345,
    \\\"output_tokens\\\": 890,
    \\\"thinking_tokens\\\": 0,
    \\\"processing_time_ms\\\": 2450,
    \\\"llm_provider\\\": \\\"google\\\",
    \\\"llm_model\\\": \\\"gemini-2.5-flash-lite\\\",
    \\\"run_id\\\": \\\"uuid-1234-5678\\\"
  }
}
```

**DialecticCompletedEvent:**

```json
{
  \\\"specversion\\\": \\\"1.0\\\",
  \\\"id\\\": \\\"dial-9876-5432\\\",
  \\\"source\\\": \\\"honcho.dialectic.worker\\\",
  \\\"type\\\": \\\"honcho.events.DialecticCompleted\\\",
  \\\"time\\\": \\\"2025-03-15T11:15:00Z\\\",
  \\\"data\\\": {
    \\\"workspace_name\\\": \\\"local-honcho\\\",
    \\\"peer_id\\\": \\\"dsidlo\\\",
    \\\"query\\\": \\\"What are my preferences?\\\",
    \\\"reasoning_level\\\": \\\"medium\\\",
    \\\"iterations\\\": 2,
    \\\"tool_calls_made\\\": 3,
    \\\"tools_used\\\": [\\\"search_memory\\\", \\\"get_peer_card\\\"],
    \\\"input_tokens\\\": 3456,
    \\\"output_tokens\\\": 1234,
    \\\"thinking_tokens\\\": 567,
    \\\"total_tokens\\\": 5257,
    \\\"processing_time_ms\\\": 3890,
    \\\"llm_provider\\\": \\\"anthropic\\\",
    \\\"llm_model\\\": \\\"claude-haiku-4-5\\\",
    \\\"response_length\\\": 234,
    \\\"created_observations\\\": 1,
    \\\"run_id\\\": \\\"uuid-9876-5432\\\"
  }
}
```

### 12.3 Performance Monitoring

Key metrics tracked by Honcho:

| Metric | Description | Unit |
|--------|-------------|------|
| `deriver.processing_time` | Time to process message batch | ms |
| `dialectic.iterations` | Number of LLM iterations | count |
| `embedding.batch_size` | Number of texts in batch | count |
| `search.hybrid_time` | Time for hybrid vector/FTS search | ms |
| `api.request_time` | End-to-end request duration | ms |
| `llm.call_duration` | Individual LLM call time | ms |
| `db.query_count` | SQL queries per request | count |
| `cache.hit_rate` | Cache hit percentage | % |
| `queue.processing_lag` | Time from enqueue to process | ms |

### 12.4 Logging Configuration

Honcho uses structured logging with Rich console output:

```toml
[logging]
level = \"INFO\"
format = \"%(asctime)s - %(name)s - %(levelname)s - %(message)s\"
console = true
sentry = true
```

Log levels available:

- **DEBUG**: Detailed operation traces (tool calls, cache hits)
- **INFO**: Normal operation (message ingestion, API calls)
- **WARNING**: Non-critical issues (cache fallbacks, retries)
- **ERROR**: Recoverable errors (LLM failures, validation)
- **CRITICAL**: System failures (DB connection loss)

---

## 13. Caching System

### 13.1 Overview

Honcho uses Redis as an optional distributed cache layer to improve performance for read-heavy operations. The cache is implemented using the `cashews` library with `aioredis` backend, providing asynchronous key-value storage with TTL support and distributed locking capabilities.

**Key Features:**
- **Optional**: Disabled by default (`CACHE_ENABLED=false`) to avoid external dependencies
- **Demand-Driven**: Only activated for specific high-read operations (e.g., session retrieval, search results)
- **Graceful Fallback**: Switches to in-memory caching if Redis unavailable
- **Namespaced**: All keys prefixed with `CACHE_NAMESPACE` (default: \"honcho\")
- **TTL Management**: Automatic expiration (default 300s) to prevent staleness

### 13.2 Configuration

Caching is configured via environment variables or TOML:

#### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_ENABLED` | `false` | Enable/disable caching entirely |
| `CACHE_URL` | `redis://localhost:6379/0?pool_max_size=10&pool_timeout=5` | Redis connection URL with pool settings |
| `CACHE_NAMESPACE` | `honcho` | Key prefix for all cached items |
| `CACHE_DEFAULT_TTL_SECONDS` | `300` (5 min) | Default expiration time |
| `CACHE_DEFAULT_LOCK_TTL_SECONDS` | `5` | Lock acquisition timeout |

#### TOML Configuration

```toml
[cache]
enabled = true
url = \"redis://127.0.0.1:6379/0?pool_max_size=20&pool_timeout=5\"
namespace = \"honcho\"
default_ttl_seconds = 300
default_lock_ttl_seconds = 5
```

**Redis URL Parameters:**
- `pool_max_size`: Maximum connection pool size (default: 10)
- `pool_timeout`: Connection acquisition timeout (default: 5s)
- `socket_connect_timeout`: Connection timeout (default: 5s)
- `socket_timeout`: Read/write timeout (default: 5s)
- `retry_on_timeout`: Enable retry on timeouts (default: true)

### 13.3 Cache Initialization Logic

The cache initializes during server startup (`main.py` lifespan):

```python
async def init_cache() -> None:
    async with _cache_lock:  # Prevent race conditions
        await cache.close()  # Close existing connections
        
        if not settings.CACHE.ENABLED:
            logger.info(\"Cache disabled, using in-memory cache\")
            cache.setup(\"mem://\", pickle_type=PicklerType.SQLALCHEMY)
            return
        
        try:
            # Setup Redis backend
            cache.setup(
                settings.CACHE.URL,
                pickle_type=PicklerType.SQLALCHEMY,  # SQLAlchemy-compatible serialization
            )
            cache.enable()
            
            # Connection test with retries
            async for attempt in AsyncRetrying(
                wait=wait_exponential_jitter(initial=0.2, max=2.0),
                stop=stop_after_delay(5),
                retry=retry_if_exception_type((redis_exc.TimeoutError, redis_exc.ConnectionError)),
            ):
                with attempt:
                    async with asyncio.timeout(2):
                        await cache.ping()
                        logger.info(\"Connected to cache at %s\", settings.CACHE.URL)
                        
        except Exception as e:
            logger.warning(\"Cache setup failed: %s. Falling back to in-memory\", e)
            cache.setup(\"mem://\", pickle_type=PicklerType.SQLALCHEMY)
```

**Special Logic:**
- **Lock Protection**: `_cache_lock` ensures single initialization
- **Connection Pooling**: aioredis manages pooled connections (configurable via URL)
- **Health Check**: `cache.ping()` with retries (3 attempts, exponential backoff)
- **Fallback**: Immediate switch to in-memory on any failure (no service disruption)
- **Serialization**: Uses SQLAlchemy pickler for complex objects (dicts, lists, models)

### 13.4 Cache Operations and Triggers

Caching is demand-driven—only specific read-heavy paths use the cache:

#### 13.4.1 Session Caching

**Path**: `src/routers/sessions.py` (get_session, get_sessions)

**Logic**:
```python
async def get_session(...):
    key = session_cache_key(workspace_id, session_id)  # \"honcho:session:{ws}_{sess}\"
    
    # Try cache first
    cached = await cache.get(key)
    if cached:
        logger.debug(\"Cache hit for session %s\", key)
        return cached  # Deserialize and return
    
    # Cache miss: Query DB
    session = await crud.session.get(db, workspace_name, session_name)
    if not session:
        raise ResourceNotFoundException
    
    # Serialize and cache
    serialized = session.serialize()  # Pydantic model to dict
    await safe_cache_set(key, serialized, expire=60)  # 60s TTL
    
    logger.debug(\"Cached session %s\", key)
    return session
```

**Triggers**:
- GET `/v3/sessions/{id}` or `/v3/sessions/list`
- Context retrieval (`/v3/sessions/{id}/context`)
- **TTL**: 60 seconds (frequent invalidation on updates)

**Invalidation**:
- On session update: `await safe_cache_delete(key)` in `update_session()`
- On deletion: `await safe_cache_delete(key)` in `delete_session()`

#### 13.4.2 Search Result Caching

**Path**: `src/utils/search.py` (hybrid_search)

**Logic**:
```python
async def hybrid_search(query: str, filters: SearchFilters):
    # Generate cache key from query + filters
    query_hash = hashlib.sha256(f\"{query}{filters.model_dump()}\".encode()).hexdigest()[:16]
    key = f\"honcho:search:{query_hash}:{workspace_id}\"
    
    # Check cache
    cached_results = await cache.get(key)
    if cached_results:
        return json.loads(cached_results)  # Deserialize list of dicts
    
    # Execute hybrid search (vector + FTS + trigram)
    results = await execute_hybrid_query(db, query, filters)
    
    # Cache results
    await safe_cache_set(key, json.dumps(results), expire=300)  # 5min TTL
    
    return results
```

**Triggers**:
- Semantic search endpoints (`/v3/peers/search`, `/v3/documents/search`)
- Dialectic agent tool calls (`search_memory`, `search_messages`)
- **TTL**: 300 seconds (search queries are stable)

#### 13.4.3 Representation Caching

**Path**: `src/crud/representation.py` (get_representation)

**Logic**:
```python
async def get_representation(db: AsyncSession, observer: str, observed: str, type_: str = \"working\"):
    key = f\"honcho:rep:{observer}:{type_}:{observed}\"
    
    # Cache check
    cached = await cache.get(key)
    if cached:
        return Representation.parse_obj(json.loads(cached))
    
    # Compute from DB (observations + peer card)
    observations = await crud.document.search_by_observer_observed(db, observer, observed)
    peer_card = await crud.peer_card.get(db, observer, observed)
    
    # Assemble representation
    rep = Representation(
        observations=observations,
        peer_card=peer_card,
        tokens=estimate_tokens(str(observations) + str(peer_card))
    )
    
    # Cache serialized
    await safe_cache_set(key, rep.model_dump_json(), expire=1800)  # 30min TTL
    
    return rep
```

**Triggers**:
- `/v3/peers/{id}/representation`
- Dialectic context gathering
- **TTL**: 1800 seconds (representations stable longer)

#### 13.4.4 Distributed Locking

**Path**: `src/deriver/enqueue.py`, `src/webhooks/webhook_delivery.py`

**Logic**:
```python
async def safe_enqueue(...):
    lock_key = f\"honcho:lock:enqueue:{workspace_id}:{session_id}\"
    
    # Acquire lock to prevent duplicate enqueues
    async with cache.acquire_lock(lock_key, ttl=5):
        # Check if already enqueued
        existing = await db.execute(...)
        if existing:
            return  # Skip duplicate
            
        # Enqueue new task
        await crud.queue_item.create(db, payload)
```

**Triggers**:
- Message enqueue (prevent duplicates)
- Webhook delivery (prevent double-sends)
- **TTL**: 5 seconds (short locks)

### 13.5 Cache Key Strategy

All keys follow the pattern: `{namespace}:{type}:{identifiers}`

| Type | Key Format | Example |
|------|------------|---------|
| **Session** | `session:{workspace_id}_{session_id}` | `honcho:session:local-honcho_pi-123` |
| **Search** | `search:{query_hash}_{workspace_id}` | `honcho:search:a1b2c3_local-honcho` |
| **Representation** | `rep:{observer}:{type}_{observed}` | `honcho:rep:dsidlo:working_dsidlo` |
| **Lock** | `lock:{resource}:{id}` | `honcho:lock:enqueue_local-honcho_pi-123` |

**Hashing for Queries**:
```python
import hashlib
query_hash = hashlib.sha256(f\"{query_text}{filters_json}\".encode()).hexdigest()[:16]
# Limits key to 16 chars for readability
```

### 13.6 Invalidation Strategy

Cache invalidation is explicit and targeted:

```mermaid
flowchart TD
    A[Write Operation] --> B{Which Resource?}
    
    B -->|Session Update| C[Delete session_cache_key]
    B -->|Message Create| D[No direct invalidation<br/>Session TTL handles]
    B -->|Observation Create| E[Delete representation keys]
    B -->|Peer Card Update| F[Delete rep:{observer}:*_keys]
    B -->|Search Index Update| G[No invalidation<br/>TTL natural expiration]
    
    C --> DONE
    D --> DONE
    E --> DONE
    F --> DONE
    G --> DONE
```

**Explicit Invalidation Examples**:

1. **Session Update**:
   ```python
   async def update_session(...):
       # Update in DB
       session = await crud.session.update(db, ...)
       
       # Invalidate cache
       key = session_cache_key(workspace_name, session_name)
       await safe_cache_delete(key)
       
       return session
   ```

2. **Representation Invalidation** (After observation creation):
   ```python
   async def create_observations(...):
       # Create observations
       docs = await crud.document.create_batch(db, observations)
       
       # Invalidate related representations
       rep_keys = [
           f\"honcho:rep:{observer}:{type_}:{observed}\"
           for observer in observers
           for type_ in [\"working\", \"longterm\"]
       ]
       
       await asyncio.gather(*[
           safe_cache_delete(key) for key in rep_keys
       ])
   ```

### 13.7 Monitoring and Metrics

Cache performance is tracked through telemetry:

**Cache Metrics Events**:

| Event Type | Fields | Purpose |
|------------|--------|---------|
| `CacheHit` | `key_type`, `operation`, `tokens_saved` | Track cache effectiveness |
| `CacheMiss` | `key_type`, `db_time_ms`, `cache_time_ms` | Identify cacheable patterns |
| `CacheSet` | `key_type`, `size_bytes`, `ttl_seconds` | Monitor storage usage |
| `CacheDelete` | `key_type`, `reason` | Track invalidation patterns |
| `CacheLockAcquired` | `resource`, `acquire_time_ms` | Monitor concurrency |

**Example CacheHit Event**:
```json
{
  \"type\": \"honcho.events.CacheHit\",
  \"data\": {
    \"key_type\": \"session\",
    \"operation\": \"get_session\",
    \"cache_time_ms\": 2,
    \"db_time_saved_ms\": 45,
    \"tokens_saved\": 2345,
    \"workspace\": \"local-honcho\"
  }
}
```

### 13.8 Production Considerations

#### Scaling Cache Usage

For high-traffic deployments:

1. **Connection Pool Tuning**:
   ```toml
   [cache]
   url = \"redis://localhost:6379/0?pool_max_size=50&pool_min_size=10&pool_timeout=10\"
   ```

2. **Redis Cluster Support**:
   ```toml
   [cache]
   url = \"redis+cluster://node1:6379,node2:6379,node3:6379/0\"
   ```

3. **Memory Management**:
   ```bash
   # Redis config (/etc/redis/redis.conf)
   maxmemory 512mb
   maxmemory-policy allkeys-lru
   ```

#### Security Best Practices

- **Network Binding**: Bind Redis to localhost (`bind 127.0.0.1`)
- **Authentication**: Use Redis ACLs or password
- **TLS**: Enable for production (`rediss://` URL)
- **Namespace Isolation**: Use separate Redis DBs per workspace if multi-tenant

#### Monitoring Setup

**Prometheus Metrics** (via redis_exporter):
```
redis_cache_hits_total{type=\"session\"} 1500
redis_cache_misses_total{type=\"search\"} 200
redis_cache_evictions_total 5
redis_connected_clients 15
redis_memory_used_bytes 245678
```

**Alert Rules**:
- Cache hit rate < 70% → Investigate query patterns
- Evictions > 0 → Increase Redis memory
- Connection pool exhaustion → Scale pool size

### 13.9 Troubleshooting Cache Issues

#### Common Issues and Solutions

| Issue | Symptoms | Solution |
|-------|----------|----------|
| **No Cache Data** | `redis-cli keys \"honcho:*\"` empty | Trigger cacheable operations (session GET, search) |
| **Fallback to In-Memory** | Logs: \"Cache setup failed\" | Check Redis URL syntax, start Redis service |
| **High Miss Rate** | Many DB queries despite cache enabled | Increase TTLs, add more cacheable paths |
| **Stale Data** | Old session data returned | Ensure proper invalidation on writes |
| **Lock Contention** | Deriver delays, webhook duplicates | Increase lock TTL, reduce worker concurrency |
| **Memory Pressure** | Redis evictions | Monitor `INFO memory`, adjust maxmemory |

#### Debug Commands

```bash
# Check cache status
uv run python -c \"from src.cache.client import init_cache; import asyncio; asyncio.run(init_cache()); print('Connected:', cache.ping())\"

# Monitor active keys
redis-cli --scan --pattern \"honcho:*\" | wc -l

# Check specific cache path
redis-cli get \"honcho:session:local-honcho_pi-123\"

# Flush cache (emergency)
redis-cli FLUSHDB

# Monitor real-time
redis-cli monitor | grep \"honcho\"
```

#### Log Analysis

Enable DEBUG logging to see cache operations:

```bash
# Temp enable debug
echo \"LOG_LEVEL=DEBUG\" >> .env
systemctl --user restart honcho-api.service

# Watch cache logs
journalctl --user -u honcho-api.service -f | grep -E \"(cache|redis)\"
```

Expected DEBUG output:
```
DEBUG - Cache hit for key honcho:session:local-honcho_pi-123 (2ms)
DEBUG - Cache miss for search:a1b2c3_local-honcho, querying DB (45ms)
DEBUG - Cached search results, size 2.3KB, TTL 300s
DEBUG - Acquired lock honcho:lock:enqueue_local-honcho_pi-123 (1ms)
DEBUG - Released lock after enqueue
```

---

## 13. Caching System (Detailed Implementation)

### 13.10 Safe Cache Operations

All cache operations use safe wrappers with retry logic:

```python
_TRANSIENT_CACHE_ERRORS = (
    redis_exc.TimeoutError,
    redis_exc.ConnectionError,
    asyncio.TimeoutError,
    TimeoutError,
)

async def safe_cache_set(key: str, value: Any, expire: int | float) -> None:
    \"\"\"Best-effort cache set with retries on transient errors.\"\"\"
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=0.1, max=0.5),
            retry=retry_if_exception_type(_TRANSIENT_CACHE_ERRORS),
            reraise=True,
        ):
            with attempt:
                await cache.set(key, value, expire=expire)
        logger.debug(\"Cached %s (TTL: %ds)\", key, expire)
    except Exception:
        logger.warning(\"Cache set failed for key %s\", key, exc_info=True)
        # Never propagate - use DB fallback

async def safe_cache_delete(key: str) -> None:
    \"\"\"Best-effort cache delete with retries.\"\"\"
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=0.1, max=0.5),
            retry=retry_if_exception_type(_TRANSIENT_CACHE_ERRORS),
        ):
            with attempt:
                deleted = await cache.delete(key)
                if deleted:
                    logger.debug(\"Invalidated cache %s\", key)
    except Exception:
        logger.warning(\"Cache delete failed for key %s\", key, exc_info=True)
```

**Retry Strategy**:
- **3 Attempts**: Exponential backoff (0.1s → 0.5s jitter)
- **Transient Only**: Retries timeouts/connections, not auth errors
- **Non-Blocking**: Failures log but don't crash (DB fallback)
- **Metrics**: Each attempt tracked in telemetry

### 13.11 Cache Statistics and Telemetry

Cache operations emit CloudEvents for monitoring:

**CacheHit Event**:
```json
{
  \"type\": \"honcho.events.CacheHit\",
  \"data\": {
    \"key_type\": \"session\",  # session, search, representation, lock
    \"operation\": \"get_session_context\",
    \"cache_lookup_time_ms\": 1.2,
    \"estimated_db_time_saved_ms\": 35.4,
    \"tokens_cached\": 2345,
    \"workspace_id\": \"local-honcho\",
    \"peer_id\": \"dsidlo\",
    \"run_id\": \"uuid-1234\"
  }
}
```

**CacheMiss Event**:
```json
{
  \"type\": \"honcho.events.CacheMiss\",
  \"data\": {
    \"key_type\": \"search\",
    \"operation\": \"hybrid_search\",
    \"cache_lookup_time_ms\": 0.8,
    \"db_query_time_ms\": 42.1,
    \"cache_set_time_ms\": 3.2,
    \"result_size_bytes\": 2345,
    \"ttl_seconds\": 300,
    \"workspace_id\": \"local-honcho\",
    \"query_hash\": \"a1b2c3d4\"
  }
}
```

**Integration with Prometheus** (via redis_exporter):
- `redis_cache_hits_total{type=\"session\"}`
- `redis_cache_misses_total{type=\"search\"}`
- `redis_cache_evictions_total`
- Custom Honcho metrics for hit rates and savings

### 13.12 Advanced Cache Patterns

#### Write-Through Caching

For critical paths like peer representations:

```python
async def get_peer_representation(...):
    key = f\"honcho:rep:{observer}:{observed}\"
    
    # Read-through: check cache first
    cached = await cache.get(key)
    if cached:
        return Representation.from_cache(cached)
    
    # Compute from DB
    observations = await search_observations(...)
    peer_card = await get_peer_card(...)
    
    rep = Representation(observations=observations, peer_card=peer_card)
    
    # Write-through: always cache result
    await safe_cache_set(key, rep.to_cache(), expire=1800)
    
    return rep
```

#### Cache-Aside Pattern

For search operations (stale OK):

```python
async def search_documents(query, filters):
    key = generate_search_key(query, filters)
    
    # Cache-aside: check, miss → compute → cache
    result = await cache.get(key)
    if result:
        return result
    
    # Expensive computation
    vector_results = await vector_search(...)
    fts_results = await fts_search(...)
    hybrid = combine_results(vector_results, fts_results)
    
    # Cache for future requests
    await safe_cache_set(key, hybrid, expire=300)
    
    return hybrid
```

#### Distributed Locking Pattern

For concurrency control:

```python
async def safe_batch_process(batch_id: str, workspace: str):
    lock_key = f\"honcho:lock:batch:{workspace}:{batch_id}\"
    
    # Try acquire lock
    acquired = await cache.acquire_lock(lock_key, ttl=30)
    if not acquired:
        logger.info(\"Batch %s already processing, skipping\", batch_id)
        return False
    
    try:
        # Critical section
        results = await process_batch(batch_id)
        
        # Update cache with results
        await safe_cache_set(f\"honcho:batch-results:{batch_id}\", results, expire=3600)
        
        return True
        
    finally:
        # Always release lock
        await cache.release_lock(lock_key)
```

### 13.13 Cache Performance Optimization

#### Key Optimization Strategies

1. **Selective Caching**:
   - Cache read-heavy, compute-expensive operations only
   - Skip write-heavy paths (messages, observations)
   - Avoid caching small/simple results (<100B)

2. **TTL Tiering**:
   ```python
   # Short TTL for volatile data
   await cache.set(\"session:active\", data, expire=30)  # Active sessions
   
   # Medium TTL for stable data
   await cache.set(\"search:popular\", results, expire=300)  # Popular queries
   
   # Long TTL for biographical data
   await cache.set(\"rep:longterm\", card, expire=86400)  # Peer cards
   ```

3. **Compression for Large Values**:
   ```python
   import gzip
   async def compressed_cache_set(key: str, value: bytes, expire: int):
       compressed = gzip.compress(value)
       await safe_cache_set(key + \":compressed\", compressed, expire)
       
       # Decompression on get
       data = await cache.get(key + \":compressed\")
       if data:
           return gzip.decompress(data)
   ```

#### Monitoring Cache Effectiveness

**Cache Hit Rate Calculation**:
```
Hit Rate = CacheHits / (CacheHits + CacheMisses)
Target: >70% for production

Savings = Sum(estimated_db_time_saved_ms) / Total Requests
Target: >20% time savings
```

**Redis-Specific Monitoring**:
- `INFO memory`: Track used_memory and maxmemory usage
- `SLOWLOG GET`: Identify slow cache operations
- `MONITOR`: Real-time command tracing for debugging

### 13.14 Integration with Other Components

#### Cache in Agent Tool Execution

Agents use cache-aware tools:

```python
class CachedSearchTool(BaseTool):
    def __init__(self, cache: Cache):
        self.cache = cache
    
    async def execute(self, query: str, filters: dict):
        key = f\"honcho:tool-search:{hash(query + str(filters))}\"
        
        if cached := await self.cache.get(key):
            return json.loads(cached)
        
        results = await self._search_db(query, filters)
        await self.cache.set(key, json.dumps(results), expire=600)
        
        return results
```

#### Cache in Webhook Delivery

```python
async def deliver_webhook(event: WebhookEvent):
    # Cache delivery status to prevent duplicates
    status_key = f\"honcho:webhook:delivery:{event.id}\"
    
    if await cache.get(status_key):
        logger.info(\"Webhook %s already delivered\", event.id)
        return
    
    # Deliver
    response = await http_client.post(event.url, json=event.payload)
    
    # Cache success/failure
    status = {\"status\": response.status, \"delivered_at\": now()}
    await cache.set(status_key, json.dumps(status), expire=86400)
```

#### Cache in Session Context Assembly

```python
async def get_session_context(...):
    # Cache entire context assembly
    context_key = f\"honcho:context:{session_id}:{token_limit}\"
    
    if cached_context := await cache.get(context_key):
        return SessionContext.parse_obj(json.loads(cached_context))
    
    # Assemble from multiple sources
    summary = await get_best_summary(...)
    messages = await get_recent_messages(token_budget)
    representation = await get_peer_representation(...)
    
    context = SessionContext(
        summary=summary,
        messages=messages,
        representation=representation,
        total_tokens=calculate_tokens(context)
    )
    
    # Cache for same token limit
    await cache.set(context_key, context.model_dump_json(), expire=120)
    
    return context
```

### 13.15 Future Cache Enhancements

Planned improvements:

1. **Predictive Pre-warming**:
   - Pre-cache popular peer representations
   - Warm search cache for common queries

2. **Cache Partitioning**:
   - Separate Redis instances by workspace size
   - Per-tenant eviction policies

3. **Advanced Serialization**:
   - Protocol Buffers for smaller payloads
   - Compression for large representations

4. **Cache Analytics**:
   - Automatic TTL adjustment based on access patterns
   - Cache warming based on usage analytics

5. **Multi-Level Caching**:
   - L1: In-memory (Python dict)
   - L2: Redis (distributed)
   - L3: Database (persistent)

---

## 13. Caching System (Production Deployment Guide)

### 13.16 Redis Deployment Recommendations

#### Standalone Redis (Development/Small Scale)

```bash
# Install Redis
sudo apt update && sudo apt install redis-server

# Configure for Honcho
sudo tee -a /etc/redis/redis.conf << EOF
# Bind to localhost only
bind 127.0.0.1

# Memory management
maxmemory 256mb
maxmemory-policy allkeys-lru

# Persistence (optional for cache)
save 900 1
save 300 10
save 60 10000

# Client timeout
timeout 300

# Logging
loglevel notice
logfile /var/log/redis/redis-server.log
EOF

# Start service
sudo systemctl restart redis-server
sudo systemctl enable redis-server
```

#### Redis Cluster (High Availability)

For production with multiple Honcho instances:

```yaml
# docker-compose.yml
version: '3.8'
services:
  redis-cluster:
    image: redis:7.0-alpine
    command: redis-server /usr/local/etc/redis/redis.conf
    volumes:
      - ./redis.conf:/usr/local/etc/redis/redis.conf
    ports:
      - \"7000-7005:7000-7005\"
    deploy:
      replicas: 6
      resources:
        limits:
          memory: 512M

# redis.conf for cluster
bind 0.0.0.0
port 7000
cluster-enabled yes
cluster-config-file nodes.conf
cluster-node-timeout 5000
appendonly yes
```

**Honcho Configuration for Cluster**:
```toml
[cache]
enabled = true
url = \"redis+cluster://redis-cluster:7000,redis-cluster:7001,redis-cluster:7002/0\"
namespace = \"honcho\"
```

### 13.17 Cache Tuning for Different Workloads

#### Light Usage (Development, Single User)

```toml
[cache]
enabled = true
url = \"redis://localhost:6379/0?pool_max_size=5&pool_timeout=3\"
default_ttl_seconds = 180  # 3 minutes
```

#### Moderate Usage (Team Collaboration)

```toml
[cache]
enabled = true
url = \"redis://localhost:6379/0?pool_max_size=20&pool_timeout=5&socket_connect_timeout=5\"
default_ttl_seconds = 300  # 5 minutes
default_lock_ttl_seconds = 10
```

#### High Usage (Enterprise, Multi-Team)

```toml
[cache]
enabled = true
url = \"rediss://redis-cluster:7000/0?pool_max_size=50&pool_min_size=10&pool_timeout=10&socket_connect_timeout=10&ssl_cert_reqs=none\"
default_ttl_seconds = 600  # 10 minutes
default_lock_ttl_seconds = 30

# Separate cache for different workloads
[cache.sessions]
url = \"redis://localhost:6379/1?pool_max_size=30\"
default_ttl_seconds = 120

[cache.search]
url = \"redis://localhost:6379/2?pool_max_size=40\"
default_ttl_seconds = 900
```

### 13.18 Cache Maintenance Tasks

#### Automated Cache Cleanup

```python
# scripts/cache_maintenance.py
import asyncio
from src.cache.client import cache

async def cleanup_expired_keys():
    \"\"\"Remove keys that should have expired but linger.\"\"\"
    # Get all honcho keys
    keys = await cache.keys(\"honcho:*\")

    # Check TTL for each
    expired = []
    for key in keys:
        ttl = await cache.ttl(key)
        if ttl < 0:  # Already expired
            expired.append(key)

    # Bulk delete
    if expired:
        await cache.delete(*expired)
        logger.info(\"Cleaned %d expired keys\", len(expired))

if __name__ == \"__main__\":  
    asyncio.run(cleanup_expired_keys())
```

#### Cache Warming Script

```python
# scripts/cache_warmup.py
async def warm_peer_representations():
    \"\"\"Pre-warm frequently accessed representations.\"\"\"
    # Get active peers
    active_peers = await get_active_peers()
    
    for observer, observed in active_peers:
        key = f\"honcho:rep:{observer}:working:{observed}\"
        
        # Check if already cached
        if await cache.get(key):
            continue
            
        # Compute and cache
        rep = await get_representation(observer, observed, \"working\")
        await cache.set(key, rep.serialize(), expire=1800)
        
        logger.info(\"Warmed representation %s->%s\", observer, observed)
```

**Cron Schedule**:
```bash
# Warm cache daily at 2AM
0 2 * * * cd /path/to/honcho && uv run python scripts/cache_warmup.py

# Cleanup expired keys every 6 hours
0 */6 * * * cd /path/to/honcho && uv run python scripts/cache_maintenance.py
```

### 13.19 Testing Cache Integration

#### Unit Tests for Cache Layer

```python
# tests/cache/test_client.py
import pytest
from fakeredis.aioredis import create_redis_pool

@pytest.mark.asyncio
async def test_cache_initialization():
    # Test successful Redis connection
    settings.CACHE.ENABLED = True
    settings.CACHE.URL = \"redis://localhost:6379/0\"
    
    await init_cache()
    assert cache.ping()  # Should succeed
    
    # Test fallback
    settings.CACHE.URL = \"redis://nonexistent:9999\"
    with pytest.raises(Exception):
        await init_cache()
    assert \"mem://\" in str(cache)  # Should fallback

@pytest.mark.asyncio
async def test_session_caching():
    # Mock DB and session
    mock_session = MockSession(id=\"test-sess\")
    
    key = \"honcho:session:ws_test-sess\"
    
    # First call: cache miss
    result1 = await get_session_cache(\"ws\", \"test-sess\", mock_session)
    assert result1 == mock_session
    
    # Verify cached
    cached = await cache.get(key)
    assert cached is not None
    
    # Second call: cache hit
    result2 = await get_session_cache(\"ws\", \"test-sess\", None)
    assert result2 == mock_session  # From cache
    
    # Test invalidation
    await safe_cache_delete(key)
    assert await cache.get(key) is None
```

#### Integration Tests

```python
@pytest.mark.asyncio
async def test_end_to_end_caching(client: TestClient):
    # Create session (no cache initially)
    response = await client.post(\"/v3/workspaces/test/sessions\", json={...})
    assert response.status_code == 201
    
    # Get session - should cache
    response = await client.get(\"/v3/workspaces/test/sessions/sess-1\")
    assert response.status_code == 200
    
    # Verify in Redis
    key = \"honcho:session:test_sess-1\"
    value = await redis.get(key)
    assert value is not None
    
    # Update session - should invalidate
    response = await client.put(\"/v3/workspaces/test/sessions/sess-1\", json={...})
    assert response.status_code == 200
    
    # Cache should be gone
    value = await redis.get(key)
    assert value is None
```

### 13.20 Cache in Multi-Worker Environments

For horizontal scaling with multiple deriver workers:

#### Distributed Lock Implementation

```python
async def distributed_batch_processing(batch_id: str, max_workers: int = 4):
    \"\"\"Process batches with distributed coordination.\"\"\"
    lock_key = f\"honcho:lock:batch:{batch_id}\"
    leader_key = f\"honcho:leader:batch:{batch_id}\"
    
    # Elect leader using lock
    if await cache.acquire_lock(lock_key, ttl=300):
        # This worker is leader
        logger.info(\"Worker %s elected leader for batch %s\", os.getpid(), batch_id)
        
        # Coordinate workers
        await cache.set(leader_key, {\"leader_pid\": os.getpid(), \"status\": \"processing\"}, expire=300)
        
        try:
            # Process batch
            results = await process_batch(batch_id)
            
            # Notify completion
            await cache.set(leader_key, {\"status\": \"complete\", \"results_hash\": hash(results)}, expire=3600)
            
            return results
            
        finally:
            await cache.release_lock(lock_key)
    else:
        # Follower - wait for completion
        logger.info(\"Worker %s following for batch %s\", os.getpid(), batch_id)
        
        # Poll status
        for _ in range(60):  # 5 minutes max wait
            status = await cache.get(leader_key)
            if status and status.get(\"status\") == \"complete\":
                return \"completed by leader\"
            await asyncio.sleep(5)
        
        raise TimeoutError(\"Batch processing timeout\")
```

#### Worker Coordination Pattern

```python
class BatchCoordinator:
    def __init__(self, batch_id: str, num_workers: int):
        self.batch_id = batch_id
        self.num_workers = num_workers
        self.progress_key = f\"honcho:progress:{batch_id}\"
        self.completion_key = f\"honcho:completion:{batch_id}\"
    
    async def report_progress(self, worker_id: int, progress: float):
        \"\"\"Report worker progress.\"\"\"
        await cache.set(
            f\"{self.progress_key}:{worker_id}\",
            {\"progress\": progress, \"timestamp\": now()},
            expire=600
        )
        
        # Aggregate progress
        total_progress = 0
        for i in range(self.num_workers):
            worker_progress = await cache.get(f\"{self.progress_key}:{i}\")
            total_progress += worker_progress.get(\"progress\", 0) if worker_progress else 0
        
        avg_progress = total_progress / self.num_workers
        await cache.set(self.progress_key, {\"aggregate\": avg_progress}, expire=600)
    
    async def mark_complete(self, worker_id: int, results: dict):
        \"\"\"Mark worker complete and check overall completion.\"\"\"
        await cache.set(
            f\"{self.completion_key}:{worker_id}\",
            {\"complete\": True, \"results\": results, \"timestamp\": now()},
            expire=3600
        )
        
        # Check if all workers done
        completed = 0
        all_results = {}
        for i in range(self.num_workers):
            status = await cache.get(f\"{self.completion_key}:{i}\")
            if status and status.get(\"complete\"):
                completed += 1
                all_results.update(status.get(\"results\", {}))
        
        if completed == self.num_workers:
            await cache.set(self.completion_key, {\"all_complete\": True, \"results\": all_results}, expire=86400)
            logger.info(\"Batch %s fully completed by %d workers\", self.batch_id, completed)
            return True
        
        return False
```

---

## 13. Caching System (Advanced Topics)

### 13.21 Cache-Aware Query Optimization

#### Read-Through vs Write-Through Patterns

```python
# Read-Through (Cache-Aside) - Good for search
class SearchService:
    async def search(self, query: str):
        key = self._search_key(query)
        
        # Read-through: check cache, populate on miss
        if cached := await cache.get(key):
            return self._deserialize(cached)
        
        # Expensive operation
        results = await self._db_search(query)
        
        # Write-through: populate cache
        await cache.set(key, self._serialize(results), expire=300)
        
        return results

# Write-Through - Good for computed values
class RepresentationService:
    async def get_representation(self, observer: str, observed: str):
        key = f\"rep:{observer}:{observed}\"
        
        # Always compute, always cache (write-through)
        observations = await self._gather_observations(observer, observed)
        rep = self._assemble_representation(observations)
        
        await cache.set(key, rep.serialize(), expire=1800)
        
        return rep
```

#### Cache Stampede Prevention

For popular cache keys (e.g., system representations):

```python
import time
from typing import Optional, Any

async def get_or_compute_with_stampede_protection(
    key: str, 
    compute_fn: callable, 
    expire: int = 300,
    stale_threshold: int = 60  # Allow serving stale for 60s
) -> Any:
    \"\"\"Prevent cache stampede with stale-while-revalidate.\"\"\"
    
    # Try get with stale tolerance
    value, timestamp = await cache.get(key + \":data\"), await cache.get(key + \":timestamp\")
    
    if value and timestamp:
        age = time.time() - timestamp
        if age < expire - stale_threshold:
            # Fresh cache hit
            return value
        elif age < expire:
            # Stale but serve while recomputing
            asyncio.create_task(_background_refresh(key, compute_fn, expire))
            return value
    
    # Cache miss or expired - acquire lock
    lock_key = key + \":lock\"
    if await cache.acquire_lock(lock_key, ttl=30):
        try:
            # Double-check pattern
            value = await cache.get(key + \":data\")
            if value:
                return value
            
            # Compute fresh value
            fresh_value = await compute_fn()
            await cache.set(key + \":data\", fresh_value, expire=expire)
            await cache.set(key + \":timestamp\", time.time(), expire=expire)
            
            return fresh_value
            
        finally:
            await cache.release_lock(lock_key)
    else:
        # Lock held by other worker - wait and retry
        await asyncio.sleep(0.1)
        return await get_or_compute_with_stampede_protection(key, compute_fn, expire)
```

### 13.22 Cache in High-Concurrency Scenarios

#### Leader Election for Batch Processing

```python
class DistributedBatchProcessor:
    def __init__(self, batch_id: str, total_workers: int):
        self.batch_id = batch_id
        self.total_workers = total_workers
        self.election_key = f\"honcho:election:batch:{batch_id}\"
        self.progress_key = f\"honcho:progress:batch:{batch_id}\"
    
    async def elect_leader(self) -> bool:
        \"\"\"Elect leader using Redis atomic operations.\"\"\"
        # Use SETNX (set if not exists) for election
        elected = await cache.set(self.election_key, os.getpid(), nx=True, expire=300)
        
        if elected:
            logger.info(\"Elected leader for batch %s (PID: %s)\", self.batch_id, os.getpid())
            return True
        
        # Check if current leader is alive
        leader_pid = await cache.get(self.election_key)
        if leader_pid:
            try:
                # Send signal to check if leader alive
                os.kill(int(leader_pid), signal.SIGUSR1)
                return False  # Leader alive
            except (ProcessLookupError, OSError):
                # Leader dead - steal election
                stolen = await cache.set(self.election_key, os.getpid(), nx=True, expire=300)
                if stolen:
                    logger.info(\"Stolen leadership for batch %s\", self.batch_id)
                    return True
        
        return False
    
    async def coordinate_processing(self):
        \"\"\"Leader coordinates worker assignment.\"\"\"
        if not await self.elect_leader():
            # Follower - wait for work assignment
            await self._wait_for_assignment()
            return
        
        # Leader - assign work
        total_work = await self._calculate_workload()
        work_per_worker = total_work // self.total_workers
        
        assignments = {}
        for worker_id in range(self.total_workers):
            start = worker_id * work_per_worker
            end = start + work_per_worker if worker_id < self.total_workers - 1 else total_work
            assignments[worker_id] = (start, end)
        
        # Broadcast assignments
        await cache.set(self.progress_key, {
            \"leader\": os.getpid(),
            \"assignments\": assignments,
            \"total_work\": total_work,
            \"status\": \"assigned\"
        }, expire=3600)
        
        # Wait for completion
        await self._wait_for_completion()
```

#### Work Stealing Pattern

```python
async def worker_main(batch_id: str):
    processor = DistributedBatchProcessor(batch_id, total_workers=4)
    
    while True:
        # Check for work assignment
        progress = await cache.get(processor.progress_key)
        if not progress or progress.get(\"status\") != \"assigned\":
            await asyncio.sleep(1)
            continue
        
        assignments = progress[\"assignments\"]
        my_work = assignments.get(my_worker_id, (None, None))
        
        if my_work[0] is None:
            # No work assigned - try to steal
            if await processor._steal_work():
                continue
        
        # Process assigned work
        start, end = my_work
        results = await process_work_segment(start, end)
        
        # Report progress
        await processor.report_progress(my_worker_id, 100.0)
        await processor.submit_results(results)
        
        # Check if done
        if await processor.is_complete():
            break
        
        await asyncio.sleep(0.1)
```

### 13.23 Cache and Database Consistency

#### Eventual Consistency Model

Honcho uses eventual consistency between cache and database:

```mermaid
sequenceDiagram
    participant Client
    participant Cache
    participant DB
    participant Invalidation
    
    Client->>Cache: GET key (hit)
    Cache-->>Client: Cached data
    
    Client->>DB: UPDATE related data
    DB-->>Client: Update successful
    
    Note over Client,DB: Cache now stale
    
    Client->>Invalidation: DELETE cache key
    Invalidation->>Cache: Remove stale entry
    
    Client->>Cache: GET key (now miss)
    Cache->>DB: Fetch fresh data
    DB-->>Cache: Fresh data
    Cache->>Cache: Cache fresh data
    Cache-->>Client: Fresh data
```

#### Strong Consistency for Critical Paths

For peer cards and active sessions, use locks:

```python
async def update_peer_card_and_cache(observer: str, observed: str, new_card: str):
    lock_key = f\"honcho:lock:peer-card:{observer}:{observed}\"
    
    async with cache.acquire_lock(lock_key, ttl=10):
        # Read current from DB
        current_card = await db.get_peer_card(observer, observed)
        
        # Update DB
        await db.update_peer_card(observer, observed, new_card)
        
        # Invalidate ALL related caches
        rep_keys = [
            f\"honcho:rep:{observer}:*:{observed}\",
            f\"honcho:context:*:{observed}\"
        ]
        
        # Bulk invalidation using pattern (Redis 2.8+)
        for pattern in rep_keys:
            keys = await cache.keys(pattern)
            if keys:
                await cache.delete(*keys)
        
        logger.info(\"Updated and invalidated caches for peer %s->%s\", observer, observed)
```

### 13.24 Cache in Offline/Edge Scenarios

For deployments with intermittent connectivity:

#### Local-First Caching

```python
class HybridCache:
    def __init__(self, redis_cache: Cache, local_cache: dict):
        self.redis = redis_cache
        self.local = local_cache  # In-memory fallback
        self.sync_queue = asyncio.Queue()  # Pending writes
    
    async def get(self, key: str):
        # Try local first (fastest)
        if key in self.local:
            return self.local[key]
        
        # Try Redis
        try:
            value = await self.redis.get(key)
            if value:
                self.local[key] = value  # Warm local cache
                return value
        except (redis_exc.ConnectionError, TimeoutError):
            logger.warning(\"Redis unavailable for %s, using local only\", key)
            return None
        
        return None
    
    async def set(self, key: str, value: Any, expire: int = 300):
        self.local[key] = value  # Always update local
        
        try:
            await self.redis.set(key, value, expire=expire)
            await self._sync_pending()  # Flush queue if connected
        except (redis_exc.ConnectionError, TimeoutError):
            # Queue for later sync
            self.sync_queue.put_nowait((key, value, expire))
            logger.info(\"Queued %s for later sync\", key)
    
    async def _sync_pending(self):
        \"\"\"Sync pending writes when reconnected.\"\"\"
        while not self.sync_queue.empty():
            try:
                key, value, expire = self.sync_queue.get_nowait()
                await self.redis.set(key, value, expire=expire)
                logger.debug(\"Synced pending %s\", key)
            except Exception as e:
                logger.warning(\"Failed to sync %s: %s\", key, e)
                break
```

#### Offline Mode Detection

```python
async def is_cache_available() -> bool:
    try:
        await asyncio.wait_for(cache.ping(), timeout=2.0)
        return True
    except (asyncio.TimeoutError, redis_exc.ConnectionError):
        return False

class OfflineAwareService:
    async def get_data(self, key: str):
        if await is_cache_available():
            # Use full caching logic
            return await self._full_cache_get(key)
        else:
            # Bypass cache, go direct to DB
            logger.info(\"Offline mode: bypassing cache for %s\", key)
            return await self._direct_db_get(key)
```

---

This completes the Redis caching system documentation. The cache provides significant performance improvements for read-heavy operations while maintaining reliability through fallbacks and careful invalidation strategies.