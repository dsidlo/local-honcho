# Honcho/jcode Integration Conventions

*Owner: ~/workspace/tools/jcode-honcho. Established 2026-09-28. These are binding for every capture path (journal tailer, hooks, MCP store, backfill).*

## Workspace scoping
- **Git repo session** → workspace derived from git origin URL (pi-mono's rule): e.g. FlexiTTS → `flexitts`. Shared with pi-mono so both agents' histories live together per project.
- **No repo / global scope** → the dedicated `jcode` workspace (user-level preferences, cross-project patterns, non-repo sessions).
- **Never** use the `default` workspace.
- Peer ids: user = `dsidlo` (same peer as pi), agent = `agent-jcode` (distinct from `agent-pi-mono`).
- Session ids: `jcode-{jcode-session-id}` in every workspace.

## Peer attribution (user-mandated)
- User prompts → peer `dsidlo`, metadata `type: prompt`.
- Agent thoughts (reasoning), actions (`tool_use`), observations (`tool_result`), final answers → peer `agent-jcode`, metadata `type:` in {`thought`, `tool_call`, `observation`, `final`}.
- Both peers joined to every `jcode-*` session.

## Capture-scrubbing policy (binding, highest priority)
Sensitive content must never reach Honcho, regardless of capture path:
1. **Path denylist** (matches tool inputs/results by substring): `auth.json`, `.env`, `*.env`, `*.key`, `*credential*`, `*.pem`, `id_rsa`, `id_ed25519`, `*.p12`, `secrets/`
2. **Tool-result default is metadata-only:** tool name, status, byte count, duration. Full `tool_result` bodies are captured ONLY if `capture_tool_bodies=opt-in` is explicitly set in config, and NEVER for denylisted paths.
3. **Regex second layer:** content matching secret patterns (sk-[A-Za-z0-9]{20,}, ghp_, gho_, AKIA[0-9A-Z]{16}, xox[baprs]-, -----BEGIN.*PRIVATE KEY-----, hf_[A-Za-z0-9]{30,}, xai-v[0-9a-zA-Z]{20,}, bearer tokens) is scrubbed/replaced with `[REDACTED]` before posting.
4. **Purge procedure (corrected 2026-09-28):** Honcho v3.0.3 has **no message DELETE endpoint** (verified against src/routers/messages.py: only POST/GET/PUT). Instead mark the message: `PUT /v3/workspaces/{ws}/sessions/{sid}/messages/{id}` with metadata `test_note: true` / `scrubbed: true`. Content cannot be removed via API; if truly sensitive content lands in Honcho, stop the tailer, use SQL to purge, then restart (pgvector embeddings re-derive only from live messages).
5. Nothing sensitive is ever written to local logs or spool files verbatim; spool files store the redaction-safe metadata only.

## Capture toggles (tunables, this file is the registry)
| Toggle | Default | Meaning |
|---|---|---|
| `capture_prompts` | always | user prompts (peer dsidlo) |
| `capture_reasoning` | on | assistant reasoning blocks |
| `capture_tool_calls` | on | tool_use: name + inputs (inputs truncated 2 KB) |
| `capture_tool_results` | metadata | tool_result: name/status/bytes only; bodies opt-in |
| `capture_finals` | on | assistant final answers |

## Chunking and batching (mirror pi-mono's honcho.ts)
- Content >4000 chars: split on paragraph boundaries (`\n\n`), then sentences, then fixed length; metadata `is_chunk`, `chunk_index`, `total_chunks`.
- Max 5 messages per POST, 50ms between batches; failed messages requeued (spool).

## Failure handling
- Honcho unreachable → spool to `~/.cache/jcode-honcho-spool/` and retry on next opportunity; jcode unaffected.
- Unknown journal record shapes → spool and log (never drop), include jcode version in the log line.
- Only `*.journal.jsonl` files processed; `*.bak`, `*.corrupt.jsonl`, and plain `*.json` session files ignored.

## jcode native memory split (memory contract)
- jcode memory graph: curated durable facts, preferences, corrections. Explicit writes only.
- Honcho: episodic history + synthesized conclusions. Automatic (tailer) or explicit (honcho_store).
- Promotion path (Phase 4): conclusions that prove durable → jcode `memory remember` with tag `from-honcho`; mark promoted in Honcho metadata.