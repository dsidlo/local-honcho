# Honcho Pi Installer Integration Test Design

## Overview

This document describes the design for a fully automated integration test that validates the end-to-end installation and operation of the `honcho-pi` PyInstaller binary. The test spins up a fresh Docker container, installs pi-mono, validates pi is operational, then installs honcho-pi via the self-contained installer, and finally validates that Honcho agentic memory is functioning end-to-end.

---

## Architecture

```mermaid
graph TB
    subgraph "Host Machine"
        A[pytest runner] --> B[test_orchestrator.py]
        B --> C[Docker Compose]
        B --> D["tmux driver<br/>(pexpect)"]
        B --> E[HTTP assertion client]
    end

    subgraph "Docker Container<br/>honcho-pi-test"
        C --> F[PostgreSQL 16 + pgvector]
        C --> G[Redis 7]
        C --> H[Ollama + bge-m3]
        C --> I["Test container<br/>(Ubuntu 22.04)"]
    end

    subgraph "Inside Test Container"
        I --> J["1. Install pi-mono"]
        J --> K["2. Validate pi"]
        K --> L["3. Run honcho-pi install<br/>(non-interactive)"]
        L --> M["4. Start honcho services"]
        M --> N["5. Validate honcho API"]
        N --> O["6. Validate memory via<br/>pi + honcho extension"]
    end

    D -.->|"tmux send_keys<br/>pexpect.spawn"| I
    E -.->|"HTTP GET/POST<br/>localhost:8000"| M
```

---

## Test Flow

```mermaid
flowchart TD
    START([Test Start]) --> DOCKER["docker compose up<br/>spin up containers"]
    DOCKER --> WAIT_DB{"Postgres<br/>healthy?"}
    WAIT_DB -->|No| WAIT_DB
    WAIT_DB -->|Yes| WAIT_OLLAMA{"Ollama +<br/>bge-m3 ready?"}
    WAIT_OLLAMA -->|No| WAIT_OLLAMA
    WAIT_OLLAMA -->|Yes| INSTALL_PI["Install pi-mono<br/>via curl/install script"]
    INSTALL_PI --> VALIDATE_PI{"pi --version<br/>returns OK?"}
    VALIDATE_PI -->|No| FAIL_PI["FAIL:<br/>pi install failed"]
    VALIDATE_PI -->|Yes| COPY_BIN["Copy honcho-pi binary<br/>into container"]
    COPY_BIN --> TMUX_SESSION["Start tmux session<br/>inside container"]
    TMUX_SESSION --> INSTALL_HONCHO["Run: honcho-pi install<br/>--non-interactive"]
    INSTALL_HONCHO --> CHECK_ENV{".env file<br/>created?"}
    CHECK_ENV -->|No| FAIL_CONFIG["FAIL:<br/>config not written"]
    CHECK_ENV -->|Yes| CHECK_SERVICES["Generate systemd services<br/>honcho-pi self configure"]
    CHECK_SERVICES --> START_API["Start honcho-api service"]
    START_API --> START_DERIVER["Start honcho-deriver service"]
    START_DERIVER --> HEALTH_CHECK{"GET /health<br/>returns 200?"}
    HEALTH_CHECK -->|No| FAIL_HEALTH["FAIL:<br/>API not healthy"]
    HEALTH_CHECK -->|Yes| CREATE_WORKSPACE["POST /v1/workspaces<br/>create test workspace"]
    CREATE_WORKSPACE --> CREATE_PEER["POST /v1/workspaces/{ws}/peers<br/>create test peer"]
    CREATE_PEER --> CREATE_SESSION["POST /v1/workspaces/{ws}/sessions<br/>create test session"]
    CREATE_SESSION --> SEND_MESSAGES["POST .../messages<br/>send conversation messages"]
    SEND_MESSAGES --> WAIT_DERIVE["Wait for deriver<br/>to process messages"]
    WAIT_DERIVE --> CHAT_QUERY["POST .../peers/{peer}/chat<br/>ask about messages"]
    CHAT_QUERY --> CHAT_RESPONSE{"Chat response<br/>contains context?"}
    CHAT_RESPONSE -->|No| FAIL_MEMORY["FAIL:<br/>memory not working"]
    CHAT_RESPONSE -->|Yes| CHECK_EXTENSION["Validate honcho.ts<br/>pi extension"]
    CHECK_EXTENSION --> EXT_OK{"Extension installed<br/>& enabled?"}
    EXT_OK -->|No| FAIL_EXT["FAIL:<br/>extension not integrated"]
    EXT_OK -->|Yes| TEARDOWN["docker compose down<br/>cleanup"]
    TEARDOWN --> PASS([PASS ✓])

    style FAIL_PI fill:#ff6b6b,color:#fff
    style FAIL_CONFIG fill:#ff6b6b,color:#fff
    style FAIL_HEALTH fill:#ff6b6b,color:#fff
    style FAIL_MEMORY fill:#ff6b6b,color:#fff
    style FAIL_EXT fill:#ff6b6b,color:#fff
    style PASS fill:#51cf66,color:#fff
```

---

## Component Interaction

```mermaid
sequenceDiagram
    participant T as Test Runner
    participant D as Docker Compose
    participant C as Test Container
    participant DB as PostgreSQL
    participant OLL as Ollama
    participant HP as honcho-pi CLI
    participant API as honcho-api
    participant DER as honcho-deriver
    participant PI as pi-mono

    T->>D: docker compose up -d
    D->>DB: start postgres+pgvector
    D->>OLL: start ollama, pull bge-m3
    D->>C: start ubuntu container

    T->>C: docker exec ... install pi-mono
    C->>PI: curl | bash (install pi)
    T->>C: docker exec ... pi --version
    C-->>T: pi v0.x.x OK

    T->>C: docker cp honcho-pi binary
    T->>C: docker exec ... chmod +x honcho-pi

    T->>HP: honcho-pi install --non-interactive
    HP->>DB: test connection
    HP->>OLL: test embedding endpoint
    HP->>C: write ~/.config/honcho-pi/.env
    HP->>C: generate systemd services
    HP-->>T: install complete

    T->>C: systemctl --user start honcho-api
    C->>API: uvicorn starts on :8000
    T->>C: systemctl --user start honcho-deriver
    C->>DER: deriver worker starts

    T->>API: GET /health
    API-->>T: 200 OK

    T->>API: POST /v1/workspaces {id: "test-ws"}
    T->>API: POST /v1/workspaces/test-ws/peers {id: "test-user"}
    T->>API: POST /v1/workspaces/test-ws/sessions {peers: ...}
    T->>API: POST .../messages [{content: "I love Python", peer_id: "test-user"}]

    Note over DER: deriver processes messages<br/>creates observations

    T->>API: POST /v1/workspaces/test-ws/peers/test-user/chat {query: "What language do I like?"}
    API->>DER: retrieve observations
    DER-->>API: relevant context
    API-->>T: "You prefer Python" ✓

    T->>C: ls ~/.pi/agent/extensions/honcho.ts
    C-->>T: honcho.ts exists ✓
    T->>C: cat ~/.pi/agent/settings.json
    C-->>T: honcho.enabled: true ✓

    T->>D: docker compose down -v
```

---

## Detailed Phase Design

### Phase 1: Environment Setup

```mermaid
flowchart LR
    subgraph "docker-compose.yml"
        A[postgres:16-pgvector] --- B[redis:7-alpine]
        B --- C[ollama/ollama]
        C --- D[ubuntu:22.04<br/>test target]
    end

    subgraph "Volumes"
        E[pgdata] -.-> A
        F[redis-data] -.-> B
        G[ollama-models] -.-> C
    end

    subgraph "Network"
        H[honcho-test-net] -.-> A
        H -.-> B
        H -.-> C
        H -.-> D
    end
```

#### Docker Compose Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres` | `ankane/pgvector:v0.7.4-pg16` | 5432→5432 | Primary database with pgvector |
| `redis` | `redis:7-alpine` | 6379→6379 | Queue backend for deriver |
| `ollama` | `ollama/ollama:latest` | 11434→11434 | Local embedding model host |
| `test-target` | `ubuntu:22.04` (custom) | 8000→8000 | Installation target |

#### Test Target Container Dockerfile

The test target is a minimal Ubuntu 22.04 container with:
- `systemd` (user session support)
- `curl`, `git`, `tmux`, `pexpect` (Python)
- `uv` package manager
- No pre-installed honcho or pi (clean slate)

---

### Phase 2: Pi Installation & Validation

```mermaid
flowchart TD
    A["Enter test-target container"] --> B["Install pi-mono<br/>via official script"]
    B --> C{"pi --version<br/>exits 0?"}
    C -->|Yes| D["Verify pi agent dir<br/>~/.pi/agent exists"]
    C -->|No| E["Pi install failed<br/>dump install log"]
    D --> F{"pi agent<br/>directory valid?"}
    F -->|Yes| G["Phase 2 PASSED ✓"]
    F -->|No| H["pi directory missing<br/>or corrupted"]

    style G fill:#51cf66,color:#fff
    style E fill:#ff6b6b,color:#fff
    style H fill:#ff6b6b,color:#fff
```

The pi-mono installation uses the official curl-based installer. We validate:
1. `pi --version` returns a version string
2. `~/.pi/agent/` directory exists
3. `~/.pi/agent/settings.json` is valid JSON

---

### Phase 3: Honcho-Pi Installation

```mermaid
flowchart TD
    A["Copy honcho-pi binary<br/>into container"] --> B["chmod +x honcho-pi"]
    B --> C["Create tmux session<br/>'honcho-test'"]
    C --> D["tmux send-keys:<br/>honcho-pi install --non-interactive"]
    D --> E{"Exit code 0?"}
    E -->|No| F["FAIL: install failed<br/>capture tmux output"]
    E -->|Yes| G{"~/.config/honcho-pi/.env<br/>exists?"}
    G -->|No| H["FAIL: config not written"]
    G -->|Yes| I{"Validate .env<br/>contains required keys"}
    I -->|Invalid| J["FAIL: config incomplete"]
    I -->|Valid| K["Check systemd services<br/>generated"]
    K --> L{"~/.config/systemd/user/<br/>honcho-api.service exists?"}
    L -->|No| M["FAIL: services not generated"]
    L -->|Yes| N["Phase 3 PASSED ✓"]

    style N fill:#51cf66,color:#fff
    style F fill:#ff6b6b,color:#fff
    style H fill:#ff6b6b,color:#fff
    style J fill:#ff6b6b,color:#fff
    style M fill:#ff6b6b,color:#fff
```

#### Required .env Keys to Validate

| Key | Validation |
|-----|-----------|
| `DATABASE_URL` | Contains `postgresql+psycopg://` |
| `LLM_PROVIDER` | One of: anthropic, openai, groq, gemini, vllm |
| `LLM_EMBEDDING_PROVIDER` | One of: ollama, openai |
| `API_PORT` | Numeric, default 8000 |
| `HONCHO_BASE_URL` | Starts with `http://` |

---

### Phase 4: Service Startup & Health

```mermaid
flowchart TD
    A["systemctl --user daemon-reload"] --> B["systemctl --user start honcho-api"]
    B --> C["systemctl --user start honcho-deriver"]
    C --> D["Retry loop:<br/>GET /health every 2s<br/>max 30 attempts"]
    D --> E{"/health returns 200?"}
    E -->|Yes| F["Check API version<br/>GET /v1/"]
    E -->|No, retries left| D
    E -->|No, exhausted| G["FAIL: API not responding<br/>dump journalctl logs"]
    F --> H["Phase 4 PASSED ✓"]

    style H fill:#51cf66,color:#fff
    style G fill:#ff6b6b,color:#fff
```

---

### Phase 5: Honcho Memory Validation

```mermaid
flowchart TD
    A["Phase 5: Memory Test"] --> B["Create workspace:<br/>POST /v1/workspaces"]
    B --> C["Create peer:<br/>POST /v1/workspaces/{ws}/peers"]
    C --> D["Create session:<br/>POST /v1/workspaces/{ws}/sessions"]
    D --> E["Send messages:<br/>POST .../sessions/{s}/messages"]
    E --> F["Wait for deriver:<br/>poll until observations exist"]
    F --> G["Chat query:<br/>POST .../peers/{p}/chat<br/>query='What language do I like?'"]
    G --> H{"Response mentions<br/>Python?"}
    H -->|Yes| I["Memory working ✓"]
    H -->|No| J["Chat returned<br/>empty or irrelevant"]
    J --> K["FAIL: memory not<br/>forming or recalling"]

    style I fill:#51cf66,color:#fff
    style K fill:#ff6b6b,color:#fff
```

#### Memory Test Messages

We send a sequence of messages that create unambiguous facts, then verify the Dialectic can recall them:

```json
[
  {"content": "I absolutely love programming in Python. It's my favorite language.", "peer_id": "test-user"},
  {"content": "I live in San Francisco and work as a software engineer.", "peer_id": "test-user"},
  {"content": "My cat is named Whiskers and she likes to sit on my keyboard.", "peer_id": "test-user"}
]
```

**Validation query**: `"What programming language do I prefer?"`
**Expected**: Response mentions "Python"

---

### Phase 6: Pi Extension Validation

```mermaid
flowchart TD
    A["Phase 6: Extension Test"] --> B{"honcho.ts exists<br/>in extensions dir?"}
    B -->|No| C["FAIL: Extension file<br/>not installed"]
    B -->|Yes| D["Read settings.json"]
    D --> E{"honcho.enabled<br/>== true?"}
    E -->|No| F["FAIL: Extension not<br/>enabled in settings"]
    E -->|Yes| G["Verify honcho.ts<br/>contains HONCHO_BASE_URL"]
    G --> H{" Contains<br/>localhost:8000<br/>or env var?"}
    H -->|Yes| I["Extension valid ✓"]
    H -->|No| J["FAIL: Extension<br/>misconfigured"]

    style I fill:#51cf66,color:#fff
    style C fill:#ff6b6b,color:#fff
    style F fill:#ff6b6b,color:#fff
    style J fill:#ff6b6b,color:#fff
```

---

## Tmux / Pexpect Interaction Design

For scenarios requiring interactive terminal interaction (the `honcho-pi install` wizard when run interactively), we use `pexpect` driving a `tmux` session inside the Docker container.

### Why tmux + pexpect?

- `pexpect` needs a TTY; Docker `exec` doesn't provide one by default
- `tmux` creates a persistent pseudo-terminal inside the container
- Allows sending keystrokes and reading output without blocking
- Supports both interactive and non-interactive flows

### Session Management

```mermaid
sequenceDiagram
    participant T as Test Runner (pexpect)
    participant DOCKER as Docker exec
    participant TMUX as tmux session
    participant HP as honcho-pi process

    T->>DOCKER: docker exec -d test-target tmux new-session -d -s honcho-test
    DOCKER->>TMUX: session created

    T->>DOCKER: docker exec test-target tmux send-keys -t honcho-test './honcho-pi install' Enter
    DOCKER->>TMUX: send keystrokes
    TMUX->>HP: receives stdin
    HP-->>TMUX: outputs prompts

    T->>DOCKER: docker exec test-target tmux capture-pane -t honcho-test -p
    DOCKER-->>T: captured output

    alt Interactive flow
        T->>DOCKER: docker exec test-target tmux send-keys -t honcho-test 'Y' Enter
        DOCKER->>TMUX: send response
        TMUX->>HP: receives input
    end

    T->>DOCKER: docker exec test-target tmux send-keys -t honcho-test 'exit' Enter
    DOCKER->>TMUX: session ends
```

### Pexpect Helper Class

```python
class TmuxDriver:
    """Drive interactive commands inside a tmux session within a Docker container."""

    def __init__(self, container_name: str, session_name: str = "honcho-test"):
        self.container = container_name
        self.session = session_name

    def exec(self, cmd: str) -> str:
        """Run a non-interactive command in the container."""
        ...

    def start_tmux(self) -> None:
        """Create a tmux session inside the container."""
        ...

    def send_keys(self, keys: str, enter: bool = True) -> None:
        """Send keystrokes to the tmux session."""
        ...

    def capture_pane(self) -> str:
        """Capture current tmux pane output."""
        ...

    def wait_for(self, pattern: str, timeout: int = 30) -> bool:
        """Wait for pattern to appear in tmux output."""
        ...

    def send_line(self, line: str) -> None:
        """Send a line of input (keystrokes + Enter)."""
        ...
```

---

## File Structure

```
pyinstaller/
├── docs/
│   └── installer-test-design.md      # This document
├── tests/
│   ├── docker/
│   │   ├── Dockerfile                 # Multi-stage: build + test target
│   │   ├── docker-compose.yml         # Full test stack
│   │   ├── .env.test                  # Test environment config
│   │   └── entrypoint.sh              # Container startup script
│   ├── conftest.py                    # Shared fixtures (docker, http client, tmux driver)
│   ├── test_installer_e2e.py          # Main E2E test orchestrator
│   ├── test_phase1_environment.py     # Phase 1: Container environment validation
│   ├── test_phase2_pi_install.py       # Phase 2: Pi installation & validation
│   ├── test_phase3_honcho_install.py   # Phase 3: Honcho-pi installation
│   ├── test_phase4_services.py         # Phase 4: Service startup & health
│   ├── test_phase5_memory.py           # Phase 5: Memory E2E validation
│   ├── test_phase6_extension.py        # Phase 6: Pi extension validation
│   └── test_interactive_install.py    # Interactive (tmux-driven) install test
└── src/
    └── honcho_pi/
        └── ...
```

---

## Docker Configuration

### Dockerfile (tests/docker/Dockerfile)

The Dockerfile uses a multi-stage build:

1. **Builder stage**: Installs build dependencies, copies the honcho source, and produces the PyInstaller binary
2. **Test target stage**: Minimal Ubuntu 22.04 with systemd, tmux, and runtime deps — no honcho pre-installed

```mermaid
flowchart LR
    subgraph "Stage 1: Builder"
        A[ubuntu:22.04] --> B[install python, uv, build deps]
        B --> C[copy honcho source]
        C --> D[build honcho-pi binary<br/>via pyinstaller]
    end

    subgraph "Stage 2: Test Target"
        E[ubuntu:22.04] --> F[install systemd, tmux,<br/>curl, git, ollama client]
        F --> G[copy honcho-pi binary<br/>from builder]
        G --> H["ENTRYPOINT<br/>entrypoint.sh"]
    end

    D -.->|"COPY --from=builder"| G
```

### docker-compose.yml (tests/docker/docker-compose.yml)

Key configuration:

```yaml
services:
  postgres:
    image: ankane/pgvector:v0.7.4-pg16
    environment:
      POSTGRES_DB: honcho
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: honcho_test_password
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 3s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    # Pre-pull bge-m3 on first start

  test-target:
    build:
      context: ../..
      dockerfile: tests/docker/Dockerfile
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
      ollama:
        condition: service_started
    ports: ["8000:8000"]
    volumes:
      - ./..:/workspace:ro    # source mount for debugging
    environment:
      - DATABASE_URL=postgresql+psycopg://postgres:honcho_test_password@postgres:5432/honcho
      - HONCHO_BASE_URL=http://localhost:8000
    privileged: true           # needed for systemd user session
```

### Entrypoint Script (tests/docker/entrypoint.sh)

```bash
#!/bin/bash
set -e

# Start systemd user session
export XDG_RUNTIME_DIR=/run/user/$(id -u)
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# Wait for dependent services
echo "Waiting for PostgreSQL..."
until pg_isready -h postgres -U postgres; do sleep 2; done

echo "Waiting for Ollama..."
until curl -sf http://ollama:11434/api/tags; do sleep 5; done

# Pull embedding model
curl -sf http://ollama:11434/api/pull -d '{"name":"bge-m3"}'

echo "All services ready."
exec "$@"
```

---

## Test Implementation Details

### Fixtures (tests/conftest.py)

```mermaid
classDiagram
    class DockerComposeManager {
        +project_name: str
        +compose_file: Path
        +up() None
        +down() None
        +logs(service: str) str
        +exec(service: str, cmd: str) CompletedProcess
        +wait_healthy(service: str, timeout: int) bool
        +copy_into(service: str, src: Path, dst: str) None
    }

    class HttpClient {
        +base_url: str
        +get(path: str) Response
        +post(path: str, json: dict) Response
        +wait_for_health(timeout: int) bool
        +create_workspace(id: str) dict
        +create_peer(ws: str, id: str) dict
        +create_session(ws: str, peers: list) dict
        +send_messages(ws: str, session: str, msgs: list) dict
        +chat(ws: str, peer: str, query: str) dict
    }

    class TmuxDriver {
        +container: str
        +session: str
        +start_tmux() None
        +send_keys(keys: str) None
        +capture_pane() str
        +wait_for(pattern: str, timeout: int) bool
        +send_line(line: str) None
    }

    class HonchoTestContext {
        +docker: DockerComposeManager
        +http: HttpClient
        +tmux: TmuxDriver
        +workspace_id: str
        +peer_id: str
        +session_id: str
    }

    HonchoTestContext --> DockerComposeManager
    HonchoTestContext --> HttpClient
    HonchoTestContext --> TmuxDriver
```

#### Key Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `docker_compose` | session | Starts/stops Docker Compose stack |
| `test_container` | session | Provides TmuxDriver for the test-target container |
| `http_client` | session | HttpClient pointed at the API (after services start) |
| `honcho_context` | session | Full test context with workspace/peer/session IDs |

### Test Phases as Markers

```python
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker,
    pytest.mark.slow,
]

# Phase markers for selective running
phase1 = pytest.mark.phase1   # Environment setup
phase2 = pytest.mark.phase2   # Pi installation
phase3 = pytest.mark.phase3   # Honcho installation
phase4 = pytest.mark.phase4   # Service startup
phase5 = pytest.mark.phase5   # Memory validation
phase6 = pytest.mark.phase6   # Extension validation
```

### Timeout & Retry Strategy

```mermaid
flowchart TD
    A["Start operation"] --> B["Poll with exponential backoff"]
    B --> C{"Success?"}
    C -->|Yes| D["Return result"]
    C -->|No| E{"Retries left?"}
    E -->|Yes| F["Wait: min(2^attempt, 30) seconds"]
    F --> B
    E -->|No| G["Collect diagnostics:<br/>docker logs, journalctl,<br/>.env contents, systemctl status"]
    G --> H["FAIL with full context"]
```

| Operation | Initial Delay | Max Retries | Backoff |
|-----------|--------------|-------------|---------|
| DB healthy | 2s | 30 | 2s fixed |
| Ollama ready | 5s | 12 | 5s fixed |
| API /health | 2s | 30 | exponential 2→30s |
| Deriver processing | 2s | 20 | 2s fixed |
| Memory query | 3s | 5 | 3s fixed |

---

## Interactive Install Test Design

The interactive install test uses `pexpect` driving `tmux` to simulate a human walking through the `honcho-pi install` wizard:

```mermaid
sequenceDiagram
    participant T as Test (pexpect)
    participant TMUX as tmux session
    participant HP as honcho-pi install

    T->>TMUX: tmux new-session -s test
    T->>TMUX: tmux send-keys './honcho-pi install' Enter

    HP->>TMUX: "Use Docker Postgres with pgvector? [Y/n]:"
    T->>TMUX: tmux send-keys 'Y' Enter

    HP->>TMUX: "Postgres password (default=honcho_default):"
    T->>TMUX: tmux send-keys 'honcho_test_password' Enter

    HP->>TMUX: "LLM Provider [anthropic/openai/groq/gemini/vllm]:"
    T->>TMUX: tmux send-keys 'vllm' Enter

    HP->>TMUX: "Use local Ollama for embeddings? [Y/n]:"
    T->>TMUX: tmux send-keys 'Y' Enter

    HP->>TMUX: "Ollama embedding model [nomic-embed-text]:"
    T->>TMUX: tmux send-keys 'bge-m3' Enter

    HP->>TMUX: "Enable Dreamer? [Y/n]:"
    T->>TMUX: tmux send-keys 'Y' Enter

    HP->>TMUX: "Install Pi extension? [Y/n]:"
    T->>TMUX: tmux send-keys 'Y' Enter

    HP->>TMUX: "✓ Configuration saved to ~/.config/honcho-pi/.env"
    HP->>TMUX: "✓ Systemd services generated"
    HP->>TMUX: "✓ Pi extension installed"

    T->>TMUX: tmux capture-pane
    T-->>T: Assert all "✓" present
```

---

## Diagnostic Collection on Failure

When any phase fails, the test automatically collects:

```mermaid
flowchart TD
    A["Test Phase FAILED"] --> B["Collect diagnostics"]
    B --> C["docker compose logs<br/>all services"]
    B --> D["journalctl --user<br/>honcho-api/deriver"]
    B --> E["cat ~/.config/honcho-pi/.env<br/>(redact secrets)"]
    B --> F["systemctl --user status<br/>all honcho services"]
    B --> G["ls -la ~/.pi/agent/<br/>extensions + settings"]
    B --> H["curl -v http://localhost:8000/health<br/>full response headers"]
    B --> I["tmux capture-pane<br/>last 100 lines"]
    B --> J["Write to<br/>tests/test-results/&lt;timestamp&gt;/"]

    style A fill:#ff6b6b,color:#fff
```

All diagnostics are written under `tests/test-results/<timestamp>/` with separate files for each artifact, making post-mortem analysis straightforward.

---

## Running the Tests

```bash
# Full E2E suite (all phases, ~5-10 minutes)
cd pyinstaller
pytest tests/ -m integration --verbose

# Single phase
pytest tests/ -m phase3 --verbose

# Skip Docker setup (use already-running stack)
SKIP_DOCKER_UP=1 pytest tests/ -m phase4 --verbose

# Interactive install test only
pytest tests/test_interactive_install.py --verbose

# Quick smoke test (phases 1-4 only, no memory validation)
pytest tests/ -m "integration and not slow" --verbose
```

### CI Integration

```yaml
# .github/workflows/installer-test.yml
- name: Run Installer E2E Tests
  run: |
    cd pyinstaller
    pip install -e ".[dev]"
    pip install testcontainers pexpect
    pytest tests/ -m integration --timeout=600 --junitxml=test-results.xml
  artifacts:
    - pyinstaller/tests/test-results/
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Docker Compose over Dockerfile-only** | Need isolated network for postgres/redis/ollama; Compose provides health checks and dependency ordering |
| **pexpect + tmux over expect** | Python-native, better error handling, integrates with pytest, works inside Docker |
| **Non-interactive as primary, interactive as separate test** | `--non-interactive` mode is deterministic; interactive mode tests the wizard UX separately |
| **Ollama + bge-m3 for embeddings** | Avoids external API key dependency; bge-m3 is the default in the .env.template |
| **vllm provider for LLM (mocked)** | In CI, we mock the LLM provider or use a small local model to avoid API costs; deriver still processes messages |
| **pgvector image (ankane/pgvector)** | Matches production; initial schema migration creates `VECTOR` columns |
| **systemd user sessions in container** | honcho-pi manages services via `systemctl --user`; requires `privileged` or `--security-opt seccomp=unconfined` |
| **Privileged container** | Required for systemd user session support; alternative is sysbox runtime |

---

## Future Enhancements

1. **LLM Mocking**: Add a mock LLM server that returns deterministic responses, eliminating external API dependency
2. **Upgrade Testing**: Test upgrading from a previous honcho-pi version
3. **Multi-distro**: Test on Ubuntu 22.04, 24.04, Fedora 40, and macOS (Darwin)
4. **Performance Baselines**: Assert service startup < 10s, memory query < 5s
5. **Chaos Testing**: Kill deriver mid-processing, restart, verify recovery
6. **Network Isolation**: Test API reachability from pi-agent perspective inside the container