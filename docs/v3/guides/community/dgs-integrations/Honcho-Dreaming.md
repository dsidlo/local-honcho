# Honcho Dreaming Process

## Overview

Dreaming is Honcho's memory consolidation and synthesis process. It runs autonomously in the background, analyzing observations about peers to create higher-level insights through deductive and inductive reasoning.

## Dream Cycle Triggers

Dreaming is triggered when a Collection reaches a document threshold (default: 50 new documents since the last dream). The process uses a delayed scheduler to batch work and avoid constant processing.

```
Document Created → Check Threshold (50 docs) 
    → Schedule Timer (30 min delay) 
    → Execute Dream Cycle
```

## Process Flow

### High-Level Dream Cycle

```mermaid
flowchart TD
    A[Document Added to Collection] --> B{Check Document Threshold}
    B -->|25+ docs since last dream| C[Schedule Dream Timer]
    B -->|Under threshold| Z[Continue Normal Operation]
    C --> D[Wait IDLE_TIMEOUT_MINUTES]
    D --> E{Check Min Hours Between Dreams}
    E -->|Time threshold met| F[Execute Dream Cycle]
    E -->|Too recent| Z
    F --> G[Phase 0: Surprisal Sampling]
    G --> H[Phase 1: Deduction Specialist]
    H --> I[Phase 2: Induction Specialist]
    I --> J[Phase 3: Synthesis Specialist]
    J --> K[Update Collection Metadata]
    K --> Z
```

### Detailed Dream Execution Flow

```mermaid
flowchart TD
    subgraph "Phase 0: Surprisal Sampling"
        P0A[Sample Observations] --> P0B{Geometric Surprisal > Threshold?}
        P0B -->|Yes| P0C[Generate Exploration Hints]
        P0B -->|No| P0D[No hints - free exploration]
        P0C --> P0E[Pass hints to specialists]
        P0D --> P0E
    end

    subgraph "Phase 1: Deduction Specialist"
        P1A[LLM Call] --> P1B[DISCOVERY: Search Memory]
        P1B --> P1C[DISCOVERY: Get Recent Observations]
        P1C --> P1D[DISCOVERY: Search Messages]
        P1D --> P1E[ANALYSIS: Find Logical Implications]
        P1E --> P1F[ACTION: Create Deductive Observations]
        P1F --> P1G[ACTION: Delete Outdated Observations]
        P1G --> P1H[ACTION: Update Peer Card]
    end

    subgraph "Phase 2: Induction Specialist"
        P2A[LLM Call] --> P2B[DISCOVERY: Search Memory]
        P2B --> P2C[DISCOVERY: Get Recent Observations]
        P2C --> P2D[DISCOVERY: Search Messages]
        P2D --> P2E[ANALYSIS: Identify Patterns]
        P2E --> P2F[ACTION: Create Inductive Observations]
        P2F --> P2G[ACTION: Update Peer Card]
    end

    subgraph "Phase 3: Synthesis Specialist"
        P3A[LLM Call] --> P3B[Analyze Deduction Results]
        P3B --> P3C[Analyze Induction Results]
        P3C --> P3D[Create Architectural Insights]
    end

    Start[Dream Cycle Start] --> P0A
    P0E --> P1A
    P1H --> P2A
    P2G --> P3A
    P3D --> End[Dream Cycle Complete]
```

## Object/Data Flows

### Input Data Sources

```mermaid
flowchart LR
    subgraph "Input Data"
        ID1[Documents/Observations<br/>from Vector Store]
        ID2[Messages<br/>from Session History]
        ID3[Peer Card<br/>Biographical Facts]
        ID4[Surprisal Scores<br/>High-information Observations]
    end

    subgraph "Processing"
        P1[Deduction Specialist<br/>LLM Agent]
        P2[Induction Specialist<br/>LLM Agent]
        P3[Synthesis Specialist<br/>LLM Agent]
    end

    subgraph "Output Data"
        OD1[Deductive Observations]
        OD2[Inductive Observations]
        OD3[Synthesis Insights]
        OD4[Updated Peer Card]
        OD5[Deleted Redundant Observations]
    end

    ID1 --> P1
    ID1 --> P2
    ID1 --> P3
    ID2 --> P1
    ID2 --> P2
    ID3 --> P1
    ID3 --> P2
    ID4 --> P1
    ID4 --> P2

    P1 --> OD1
    P1 --> OD4
    P1 --> OD5
    P2 --> OD2
    P2 --> OD4
    P3 --> OD3
```

### Data Transformation Flow

```mermaid
flowchart TD
    subgraph "Raw Observations"
        RO1["Explicit Observation:<br/>User moved to Seattle"]
        RO2["Explicit Observation:<br/>User works at Microsoft"]
        RO3["Explicit Observation:<br/>User has 2 kids ages 5 and 8"]
    end

    subgraph "Deduction Phase"
        D1["Knowledge Updates:<br/>Meeting moved from Tue to Thu"]
        D2["Logical Implications:<br/>Has software engineering skills"]
        D3["Contradictions:<br/>Conflicting preferences noted"]
    end

    subgraph "Induction Phase"
        I1["Behavioral Patterns:<br/>Tends to reschedule when stressed"]
        I2["Preferences:<br/>Prefers morning meetings"]
        I3["Traits:<br/>Detail-oriented in planning"]
    end

    subgraph "Synthesis Phase"
        S1["Architectural Insights:<br/>Career trajectory shows consistent growth"]
    end

    RO1 & RO2 --> D2
    RO3 --> D2
    D2 --> I1
    D2 --> I2
    D2 --> I3
    I1 & I2 & I3 --> S1
```

## LLM Involvement

### Where LLMs Are Used

The dreaming process uses LLMs in three autonomous agent contexts:

| Phase | Specialist | LLM Purpose | Model Config |
|-------|-----------|-------------|--------------|
| Phase 1 | **Deduction Specialist** | Analyzes explicit observations to create deductive conclusions, identify contradictions, and update peer cards | `DREAM_DEDUCTION_MODEL` |
| Phase 2 | **Induction Specialist** | Identifies patterns across observations to create inductive generalizations | `DREAM_INDUCTION_MODEL` |
| Phase 3 | **Synthesis Specialist** | Creates high-level architectural insights from deduction and induction results | `DREAM_SYNTHESIS_MODEL` |

### LLM Interaction Pattern

```mermaid
sequenceDiagram
    participant Agent as Specialist Agent
    participant LLM as Language Model
    participant Tools as Tool Registry
    participant DB as Database/Vector Store

    Agent->>LLM: Initial Call (System Prompt + User Prompt)
    Note over Agent,LLM: System prompt defines role and tools<br/>User prompt provides context and hints

    loop Tool Calling Loop (max 10-15 iterations)
        LLM-->>Agent: Response with Tool Call
        Agent->>Tools: Execute Tool
        Tools->>DB: Query Observations/Messages
        DB-->>Tools: Return Results
        Tools-->>Agent: Tool Result
        Agent->>LLM: Send Tool Result
    end

    LLM-->>Agent: Final Response (No more tool calls)
    Agent->>DB: Save New Observations
    Agent->>DB: Update Peer Card
    Agent->>DB: Delete Redundant Observations
```

### Tool Arsenal

Each specialist has access to these tools during their exploration:

**Discovery Tools:**
- `get_recent_observations` - Retrieve recent observations from memory
- `search_memory` - Semantic search across observation space
- `search_messages` - Search raw message content

**Action Tools:**
- `create_observations` - Create new deductive/inductive observations
- `delete_observations` - Remove outdated/redundant observations
- `update_peer_card` - Update biographical profile (if enabled)

```mermaid
flowchart LR
    subgraph "LLM Agent"
        A[Specialist]
    end

    subgraph "Discovery Tools"
        T1[get_recent_observations]
        T2[search_memory]
        T3[search_messages]
    end

    subgraph "Action Tools"
        T4[create_observations]
        T5[delete_observations]
        T6[update_peer_card]
    end

    subgraph "Data Sources"
        D1[Observations Vector Store]
        D2[Messages Database]
        D3[Peer Card Store]
    end

    A --> T1
    A --> T2
    A --> T3
    A --> T4
    A --> T5
    A --> T6

    T1 --> D1
    T2 --> D1
    T3 --> D2
    T4 --> D1
    T5 --> D1
    T6 --> D3
```

## Surprisal Sampling

Surprisal sampling is a technique Honcho uses to identify high-information observations during the dreaming process. It pre-filters observations to find the most "surprising" or unexpected ones worth investigating, allowing specialists to focus their attention on the most significant patterns.

### The Concept

In information theory, **surprisal** measures how unexpected an event is relative to what is already known. An observation with high surprisal contains novel information that deviates from the existing pattern.

**High Surprisal = High Information Value**
- Expected: "User attended a meeting" (low surprisal - routine event)
- Unexpected: "User unexpectedly quit their job" (high surprisal - significant deviation)

### Algorithm

```mermaid
flowchart LR
    A[Fetch Observations] --> B[Extract Embeddings]
    B --> C[Build Tree Structure]
    C --> D[Compute Surprisal]
    D --> E[Normalize Scores]
    E --> F[Filter Top N%]
    F --> G[Generate Hints]

    subgraph "Tree-Based Index"
        C
        D
    end
```

**Steps:**
1. **Fetch** observations from the collection (recent/random/all)
2. **Extract** their vector embeddings (1536-dim)
3. **Build** a tree-based spatial index (BallTree or KDTree)
4. **Compute** surprisal score for each observation
5. **Normalize** scores to [0, 1] range using min-max normalization
6. **Select** top N% (default: top 75th percentile)

### Surprisal Computation

The surprisal score is calculated using the tree structure:

```python
# For each observation's embedding
surprisal = tree.surprisal(embedding)

# Based on distance to k-nearest neighbors in the tree
# Higher distance = more isolated = more surprising
```

**Intuition:** An observation that is far from its neighbors in embedding space is "surprising" because it represents information that doesn't fit the existing patterns.

### Configuration

```bash
# Enable/Disable
DREAM_SURPRISAL__ENABLED=true

# Sampling Strategy
DREAM_SURPRISAL__SAMPLING_STRATEGY=recent  # recent | random | all
DREAM_SURPRISAL__SAMPLE_SIZE=200           # Max observations to sample

# Threshold
DREAM_SURPRISAL__TOP_PERCENT_SURPRISAL=0.25  # Take top 25%

# Tree Settings
DREAM_SURPRISAL__TREE_TYPE=balltree  # balltree | kdtree
DREAM_SURPRISAL__TREE_K=5            # k-nearest neighbors
```

### Example Output

```
🎯 Surprisal computation complete. Taking top 25%
Selected: 12/48 observations (top 25%)
📊 Filtered statistics: min=0.750, max=0.987, mean=0.834

Top 5 observations by surprisal:
  #1 [surprisal=0.987] User unexpectedly quit their job
  #2 [surprisal=0.954] User adopted a new dietary restriction
  #3 [surprisal=0.921] User moved to a different time zone
  #4 [surprisal=0.889] User changed their primary work schedule
  #5 [surprisal=0.875] User mentioned a new hobby interest
```

These high-surprisal observations are converted to exploration "hints" sent to the deduction and induction specialists, guiding them toward the most significant patterns.

### Purpose in Dreaming

| Without Surprisal | With Surprisal |
|-------------------|----------------|
| Specialists explore all observations | Specialists focus on high-value observations |
| May miss important patterns in noise | Prioritizes information-dense content |
| Wastes compute on routine events | Efficient use of LLM context window |
| May surface stale/redundant insights | Targets novel, unexpected patterns |

## Input Data Specification

### Surprisal Sampling Input

**Purpose:** Pre-filter observations to find high-information content worth investigating.

**Input Data:**
- All observations in the Collection based on sampling strategy
- Geometric surprisal scores (how unexpected is this observation given context)

**Output:** Up to 10 exploration hints passed to specialists.

### Deduction Specialist Input

**System Prompt Context:**
- Target peer name (`observed`)
- Peer card (biographical facts, if enabled)
- Tool definitions

**User Prompt Context:**
- Optional exploration hints (from surprisal)
- Current peer card state

**During Execution:**
- Recent observations (`get_recent_observations`)
- Search results (`search_memory`, `search_messages`)

### Induction Specialist Input

**System Prompt Context:**
- Target peer name (`observed`)
- Peer card (biographical facts, if enabled)
- Tool definitions

**User Prompt Context:**
- Optional exploration hints (from surprisal)
- Current peer card state

**During Execution:**
- Both explicit AND deductive observations
- Pattern analysis across multiple sources

### Synthesis Specialist Input

**Input Data:**
- Results from deduction phase
- Results from induction phase
- Exploration hints (if any)

**Output:**
- High-level architectural insights about the peer

## Configuration Variables

```bash
# Enable/Disable Dreaming
DREAM_ENABLED=true

# Threshold Settings
DREAM_DOCUMENT_THRESHOLD=25      # Documents added before scheduling
DREAM_IDLE_TIMEOUT_MINUTES=30    # Delay before execution
DREAM_MIN_HOURS_BETWEEN_DREAMS=4  # Minimum time between dreams

# LLM Model Selection
DREAM_PROVIDER=vllm
DREAM_MODEL=qwen3.5:397b-cloud
DREAM_DEDUCTION_MODEL=qwen3.5:397b-cloud
DREAM_INDUCTION_MODEL=qwen3.5:397b-cloud
DREAM_SYNTHESIS_MODEL=qwen3.5:397b-cloud

# LLM Behavior
DREAM_MAX_OUTPUT_TOKENS=8192
DREAM_THINKING_BUDGET_TOKENS=4096
DREAM_MAX_TOOL_ITERATIONS=10

# Surprisal Settings
DREAM_SURPRISAL__ENABLED=true

# History Context
DREAM_HISTORY_TOKEN_LIMIT=16384
```

## Key Concepts

### Observation Levels

```mermaid
flowchart TB
    subgraph "Level 1: Explicit"
        E1["Direct facts from messages<br/>'User said they moved to Seattle'"]
    end

    subgraph "Level 2: Deductive"
        D1["Logical implications<br/>'Has software engineering skills'"]
        D2["Contradictions<br/>'Conflicting preferences noted'"]
        D3["Knowledge updates<br/>'Meeting moved to Thursday'"]
    end

    subgraph "Level 3: Inductive"
        I1["Behavioral patterns<br/>'Tends to reschedule when stressed'"]
        I2["Preferences<br/>'Prefers morning meetings'"]
        I3["Personality traits<br/>'Detail-oriented in planning'"]
    end

    subgraph "Level 4: Synthesis"
        S1["Architectural insights<br/>'Career shows consistent growth trajectory'"]
    end

    E1 --> D1
    E1 --> D2
    E1 --> D3
    D1 --> I1
    D1 --> I2
    D1 --> I3
    I1 --> S1
    I2 --> S1
    I3 --> S1
```

### Peer Card

A special document containing stable biographical facts about a peer:
- Name, age, location, occupation
- Family relationships
- Standing instructions (`INSTRUCTION: call me X`)
- Core preferences (`PREFERENCE: prefers detailed explanations`)
- Personality traits (`TRAIT: analytical thinker`)

Updated during deduction and induction phases when new durable facts are discovered.

## Telemetry and Monitoring

Dream cycles emit telemetry events:
- `DreamRunEvent` - Overall dream cycle completion
- `DreamSpecialistEvent` - Individual specialist runs
- Token usage metrics per specialist
- Duration and iteration counts

```mermaid
flowchart LR
    subgraph "Events Emitted"
        E1[DreamRunEvent]
        E2[DreamSpecialistEvent<br/>Deduction]
        E3[DreamSpecialistEvent<br/>Induction]
        E4[DreamSpecialistEvent<br/>Synthesis]
    end

    subgraph "Metrics Tracked"
        M1[Input Tokens]
        M2[Output Tokens]
        M3[Tool Calls]
        M4[Iterations]
        M5[Duration]
    end

    E1 --> M1
    E1 --> M2
    E1 --> M5
    E2 --> M1
    E2 --> M2
    E2 --> M3
    E2 --> M4
    E3 --> M1
    E3 --> M2
    E3 --> M3
    E3 --> M4
```
