# Grok Build × Honcho — phased integration plan

Status: **phases 1 and 2 installed** on 2026-10-11. Phase 3's review point was applied: the default observed peer is now `agent-grok`. Phase 4's promoter is in `src/honcho/promote.py` and has not written a topic, because `[memory]` is not enabled. Phase 5 stays with Grok's own summary.

Installed:

- `~/.grok/honcho/common.py`, `tailer.py`, `mcp_server.py`
- `~/.grok/skills/honcho-search`, `honcho-conclusions`, `honcho-ask`
- `[mcp_servers.honcho]` in `~/.grok/config.toml` with `enabled = true`
- user unit `grok-honcho-tailer.service`, enabled and running

A scratch journal was posted before the unit was enabled. `local-honcho` / `grok-dryrun01` holds the prompt, the `summary_text` thought, the final reply, `tool_call read_file`, and `tool_result read_file: ok`. The secret in that prompt was stored as `[REDACTED]`. `encrypted_content` was not stored. Pointing the tailer at a closed port logged a retryable ensure failure and left the offset on the unsent line; the next poll posted that line once. The tailer log has no prompt text.

The live unit then backfilled both existing journals: `local-honcho` / `grok-01a12cb0-ce21-7a60-81db-2e2653ab2101` and `home-dsidlo-workspace` / `grok-01a1276a-d9f9-7783-af90-0ce735d3b84a`. `grok inspect` lists the three skills and the `honcho` MCP server. This already-open Grok session loaded the skills and did not load the MCP server. A new session is required before `honcho_search` is a tool.

Phase 3 check, after the deriver had written conclusions: `(agent-grok, dsidlo)` is 0 in both workspaces, while `(agent-grok, agent-grok)` is filled (413 in `local-honcho` and 302 in `home-dsidlo-workspace` on the later recount). Empty peer config leaves `observe_others` off, so each peer is observed only by itself. The default `observed` in `honcho_conclusions`, and the default `target` in `honcho_ask`, now follow that pair: omitted `target` asks `agent-grok` about itself. Pass `observed=dsidlo` or `target=dsidlo` for the still-empty cross-peer pair. `honcho_search` finds the captured messages.

Phase 4's writer is `src/honcho/promote.py`. It records the conclusion id and the tag `from-honcho`, skips an id already present, and returns without writing when memory is off. No topic was written. `[memory]` is not enabled. `~/.grok/rules/SOUL.md` was not modified.

Phase 5 is not installed. The journals contain `system`, `user`, `reasoning`, `assistant`, and `tool_result` only, so there is no pre-compact transcript to store. Compaction stays with Grok's own summary.

This directory is the working package for Grok Build extensions. It sits beside the pi-mono package at `docs/v3/guides/community/dgs-integrations/pi-mono/` and is not listed in the Mintlify nav (`docs/docs.json`).

## What the 2026-10-11 session decided

The design below applies the Honcho analysis from Grok session `01a1276a-d9f9-7783-af90-0ce735d3b84a`, launched in `/home/dsidlo/workspace` on 2026-10-10 and finished on 2026-10-11. That session read the live pi-mono extension (`~/.pi/agent/extensions/honcho.ts`) through the jcode integration that already follows pi's rules (`~/workspace/tools/jcode-honcho`), then said what this harness should copy.

Three decisions from that session are binding for every phase:

1. **Capture is a tail of `chat_history.jsonl`.** Grok hooks can see a prompt (`UserPromptSubmit`) and the final reply (`Stop.lastAssistantMessage`). They cannot see the thought. The thought is already on disk as `reasoning.summary[].text` where `type` is `summary_text`. A tailer outside the session can read that. The session does not wait on Honcho.
2. **Recall is a pull.** Search and conclusions are there when a turn needs them. Dialectic (`honcho_ask`) is for "what do we know" and "where did this leave off", and it takes 5–30 seconds. No Honcho brief is injected into the startup prompt, into `UserPromptSubmit`, or into `~/.grok/rules/SOUL.md`.
3. **Workspace name.** Inside a git repo, use pi-mono's origin rule so Pi, jcode, and Grok share one workspace per project. Outside a git repo, flatten the session directory: `/home/dsidlo/workspace` becomes `home-dsidlo-workspace`. Never use `default`. A session whose cwd is `~/workspace` stays in `home-dsidlo-workspace` even when a tool later edits a nested repo. A session started inside that repo keeps the origin name.

## What pi-mono does, and what this package does with it

pi-mono's extension (`~/.pi/agent/extensions/honcho.ts`, mirrored in this repo under `docs/v3/guides/community/dgs-integrations/pi-mono/`) is an in-process event subscriber. It queues the ReAct cycle, chunks at 8000 characters, flushes batches of 5, and on `session_before_compact` / `session_before_tree` writes an observational summary and **returns that summary to Pi** so the compacted context changes.

| pi-mono mechanism | Grok surface | This plan |
| --- | --- | --- |
| `session_start`: detect workspace from `ctx.cwd`, `POST /v3/workspaces` | Session cwd is `summary.json` `info.cwd`, else the decoded sessions directory name | Phase 1, in the tailer |
| Workspace = `remote.origin` repo name, lowercase, `[^a-z0-9_-]` → `-` | Same function | Phase 1. This checkout's origin is `git@github.com:dsidlo/local-honcho.git`, so the slug is `local-honcho`, not `honcho` |
| No `.git` → basename, with a short denylist (`src`, `test`, …) | No `.git` → full flattened path. Basename `workspace` is already a workspace on this server | Phase 1, per the session |
| Peers `dsidlo` + `agent-pi-mono`, both on the session | Peers `dsidlo` + `agent-grok` | Phase 1 |
| Session id `pi-{timestamp}` | Session id `grok-{grokSessionId}` | Phase 1. Stable across restarts, so a retry cannot open a second session |
| Queue prompt, thought, tool call **with arguments**, tool result **with body**, final reply | Prompt, plaintext thought, final reply. Tool call is the name. Tool result is name + status | Phase 1. The session refused argument and body capture |
| Chunk on `\n\n`, then sentences, then a hard cut. Metadata `is_chunk`, `chunk_index`, `total_chunks` | Same algorithm, **4000** characters (the jcode/session number; pi uses 8000) | Phase 1 |
| Batch 5, 50 ms between batches, requeue on failure | One post per source line (API cap 100 messages), 50 ms between lines. The offset moves past a line only after that post succeeds, so a retry does not duplicate it | Phase 1 |
| In-process `setTimeout(flush)` so a slow Honcho can still hitch a turn | Separate process. Fail-open. Grok never calls it | Phase 1 |
| `session_before_compact` returns a `CompactionResult` Pi installs as the summary | `PreCompact` / `PostCompact` are passive. A hook cannot replace Grok's summary | Not ported. See Phase 5 |
| `session_before_tree` returns a branch summary and caches `observationOverlay` | Grok has no tree event | Not ported. Branch name is metadata on each message when `git branch --show-current` succeeds |
| `/honcho-apply-learnings` writes synthesis back into the agent | Grok memory topics, tag `from-honcho`. Never `SOUL.md` | Phase 4, after capture has something to promote |
| Local overlay when the API 404s, used as the compact summary | No overlay. A down server leaves the offset unmoved and the next poll retries | Phase 1 |
| Hybrid document search (vector + FTS + trigram, method `rrf`) | `POST /v3/workspaces/{id}/documents/search` on this fork. Fallback: `conclusions/query` with `filters.observer` and `filters.observed` | Phase 2 |
| Dialectic `POST /peers/{id}/chat` | Same route. Default observer `agent-grok`, no target (self). `target=dsidlo` is explicit | Phase 3 |

jcode already proved the out-of-process shape: `~/workspace/tools/jcode-honcho` tails `~/.jcode/sessions/*.journal.jsonl`, scrubs, chunks at 4000, and exposes `honcho_search`, `honcho_ask`, `honcho_conclusions`, and `honcho_store` over stdio MCP. Its global non-repo workspace is the dedicated name `jcode`. The session replaced that fallback for Grok with the flattened path. Its conclusions call puts `observer` at the top level; this server requires them inside `filters` (and the hybrid route takes them as body fields). The MCP server here uses the fields this server actually accepts.

## Identity

| | Value |
| --- | --- |
| API | `http://localhost:8300` (`HONCHO_BASE_URL`) |
| User peer | `dsidlo` (`HONCHO_USER`) |
| Agent peer | `agent-grok` (`HONCHO_AGENT_PEER`) |
| Session id | `grok-` + the Grok session id |
| Manual notes | session `grok-manual` in the detected workspace |
| Resource pattern | `^[a-zA-Z0-9_-]+$`, length ≤ 512 (`RESOURCE_NAME_PATTERN`) |
| State | `~/.cache/grok-honcho/state.json` (byte offsets + tool-id → name) |
| Log | `~/.cache/grok-honcho/tailer.log` (counts and ids, never message text) |

Both peers are added to every `grok-*` session with empty peer config, so observation stays at the server default. The agent peer is modeled. That matches the project rule that an AI assistant may leave `observe_me` on.

A Grok session launched in `~/workspace` writes to workspace `home-dsidlo-workspace`. A Grok session launched in this repo writes to `local-honcho`, which is the same slug pi-mono would compute from `remote.origin` on this checkout. `upstream` (`plastic-labs/honcho`) is not the origin, so it does not win.

## Capture contract

Source file: `~/.grok/sessions/<url-encoded-cwd>/<session-id>/chat_history.jsonl`.

| Journal record | Honcho message | Peer |
| --- | --- | --- |
| `user` text | `type: prompt`. If the row contains `<user_query>`, only that inner text is stored. A row that is only `<system-reminder>`, `<user_info>`, or `<rules>` is skipped | `dsidlo` |
| `reasoning.summary[]` where `type` is `summary_text` | `type: thought` | `agent-grok` |
| `reasoning` with no plaintext summary | skipped. `encrypted_content` is never read | |
| `assistant.content` when non-empty | `type: final` | `agent-grok` |
| `assistant.tool_calls[]` | `tool_call <name>`. Arguments are not copied | `agent-grok` |
| `tool_result` | `tool_result <name>: ok\|error`. The body is read only far enough to classify `exit:` / `error`, then discarded | `agent-grok` |
| `system`, `backend_tool_call`, anything else | skipped | |

Secret-shaped substrings (API keys, bearer tokens, connection strings, `PGPASSWORD=`) are replaced with `[REDACTED]` before chunking and before any post. The log and the state file do not contain message text.

Subagent journals are separate session directories. Each becomes its own `grok-{id}` session in the workspace of that directory's cwd.

## Package layout

Paths under `src/` are the `~/.grok/` tree. Installing Phase 1 and Phase 2 is a copy of those paths, plus one merge into `config.toml`.

```
docs/v3/guides/community/dgs-integrations/grok-build/
  docs/grok-build-integration-plan.md          this file
  src/honcho/common.py                         ~/.grok/honcho/common.py
  src/honcho/tailer.py                         ~/.grok/honcho/tailer.py
  src/honcho/mcp_server.py                     ~/.grok/honcho/mcp_server.py
  src/honcho/mcp-fragment.toml                 merged into ~/.grok/config.toml
  src/honcho/promote.py                        phase 4; writes a topic only if memory is enabled
  src/skills/honcho-search/SKILL.md            ~/.grok/skills/honcho-search/SKILL.md
  src/skills/honcho-conclusions/SKILL.md       ~/.grok/skills/honcho-conclusions/SKILL.md
  src/skills/honcho-ask/SKILL.md               ~/.grok/skills/honcho-ask/SKILL.md
  systemd/user/grok-honcho-tailer.service      ~/.config/systemd/user/  (not under ~/.grok)
  tests/test_mapping.py                        stays in the repo
```

`mcp-fragment.toml` is shipped disabled (`enabled = false`). Merging it must not replace the rest of `~/.grok/config.toml`. The fragment's `args` path is the installed copy.

There is no `~/.grok/hooks/honcho.json` in this package. A `Stop` hook already runs TTS (`~/.grok/hooks/tts.json`). A second Stop hook would see only `lastAssistantMessage`, would miss the thought, and would race the tailer. `PreCompact` cannot return a replacement summary, so a hook there cannot do what pi's `session_before_compact` does.

## Phases

Each phase is independently installable. Later phases assume the earlier ones are running. Do not start a phase until you accept its exit check.

### Phase 0 — this review

The files exist in the repo. They are not installed.

Exit: you accept or mark up the three binding decisions, the capture table, and the workspace example for this checkout (`local-honcho` vs `home-dsidlo-workspace`).

### Phase 1 — capture

Copy `src/honcho/common.py` and `src/honcho/tailer.py` to `~/.grok/honcho/`. Install the user unit from `systemd/user/grok-honcho-tailer.service`. Enable it only after the dry run.

Dry run, from a scratch sessions directory, before pointing it at the real one:

```bash
GROK_SESSIONS_DIR=/tmp/grok-honcho-dry \
GROK_HONCHO_STATE=/tmp/grok-honcho-dry-state.json \
GROK_HONCHO_LOG=/tmp/grok-honcho-dry.log \
  python3 docs/v3/guides/community/dgs-integrations/grok-build/src/honcho/tailer.py
```

Point `GROK_SESSIONS_DIR` at a copy of one small session, not at `~/.grok/sessions`, until a single journal lands in the expected workspace.

Exit:

- `honcho` (or curl) shows workspace `home-dsidlo-workspace` or `local-honcho` according to the session cwd.
- Session id is `grok-` plus the directory name under `~/.grok/sessions/.../`.
- A prompt, a `summary_text` thought, and a final reply are present. A tool result's stored content is `tool_result <name>: ok` or `: error`.
- Stopping Honcho does not affect a running `grok` session. The tailer log shows a retryable post failure and the offset does not pass the unsent line.
- `~/.cache/grok-honcho/tailer.log` contains no prompt text.

Backfill is the same process: a missing offset starts at byte 0, so the first poll of an old journal sends its history. Let the first real run drain before judging duplicate rate. Re-running against a saved offset must not create a second copy.

### Phase 2 — pull recall

Merge `src/honcho/mcp-fragment.toml` into `~/.grok/config.toml` and set `enabled = true`. Copy the three skills to `~/.grok/skills/`. Start a new Grok session so the MCP server is picked up.

The tools:

| Tool | Call | When |
| --- | --- | --- |
| `honcho_search` | `POST /v3/workspaces/{ws}/search` | Fast lookup across both peers |
| `honcho_conclusions` | `POST /v3/workspaces/{ws}/documents/search` with `method: rrf`, observer `agent-grok`, observed `agent-grok` | Distilled memory. 404 falls back to `conclusions/query` |
| `honcho_ask` | `POST /v3/workspaces/{ws}/peers/agent-grok/chat` with no `target` | One slow question, on request. `target=dsidlo` is explicit |
| `honcho_store` | batch `POST .../messages` into session `grok-manual` | Explicit note only. The tailer remains the automatic path |

Exit:

- In a session whose cwd is this repo, `honcho_search` resolves workspace `local-honcho` with no `workspace` argument.
- In a session whose cwd is `~/workspace`, the same call resolves `home-dsidlo-workspace`.
- A skill does not fire on an ordinary coding turn. `/honcho-ask` with a question returns dialectic `content` or a visible error. It does not add text to the system prompt.
- `grok inspect` lists the three skills.

### Phase 3 — confirm the dreamer is feeding the pull

No new Grok code. The local deriver and dreamer already turn captured messages into conclusions. This phase checks that Phase 1's messages are actually becoming readable conclusions for the pair `(agent-grok, dsidlo)`.

Exit: after a captured session has had time to derive, `honcho_conclusions` with a query about that session returns at least one conclusion whose observer is `agent-grok` and whose observed peer is `dsidlo`. If the pair stays empty while `(agent-grok, agent-grok)` fills up, the default `observed` in the MCP tool should change. That is a review point, not a silent switch.

### Phase 4 — promote durable conclusions into Grok memory

pi's `/honcho-apply-learnings` writes synthesis back into the agent. jcode writes the same kind of conclusion into its memory graph with the tag `from-honcho`.

Here the curated store is Grok memory (`~/.grok/memory-v2/**/topics/*.md`), and only when `[memory] enabled = true`. A promotion writes a topic (or an observation the next `/dream` will fold) and records the conclusion id in the topic so the same conclusion is not promoted twice.

Do not write promoted text into `~/.grok/rules/SOUL.md`. That file is a standing instruction, loaded every session, and the 2026-10-11 session kept it separate from Honcho.

`src/honcho/promote.py` is that writer. It waits on `[memory] enabled = true` before it creates a topic.

Exit: one conclusion, chosen by you, appears in a memory topic with the conclusion id, and `SOUL.md` is unchanged.

### Phase 5 — compaction bookmark, if it is still wanted

pi stores the text about to be compacted and returns a shorter summary that **becomes** the session context. Grok's `PreCompact` hook is observe-only. It cannot hand a summary back.

If a later journal record or hook payload exposes the pre-compact transcript, the tailer can store it as `type: obs_extraction`. That gives the dreamer the text Grok is about to forget. It does not change what Grok keeps in the live context. Building a second observational-memory extension that rewrites Grok's summary would be a different project, and it would be the push path this plan is not taking.

Exit: either a stored `obs_extraction` message for one real compaction, or a decision to leave compaction to Grok's own summary.

## Verification already run

`tests/test_mapping.py` covers the rules that do not need a server:

- `/home/dsidlo/workspace` flattens to `home-dsidlo-workspace`.
- An origin URL `git@github.com:dsidlo/local-honcho.git` yields `local-honcho`, and a nested directory inside that repo resolves to the same slug.
- A worktree `.git` file follows `commondir` to the origin.
- A non-repo directory does not collapse to its basename.
- `<user_query>` is kept; a bare `<system-reminder>` is dropped.
- A reasoning record contributes `summary_text` only.
- A tool call keeps the name and drops arguments, including a secret in those arguments.
- A tool result becomes `name: error` without the body.
- A `sk-…` token in a prompt is replaced with `[REDACTED]`.
- A trailing partial JSON line is not consumed.
- Over-length text is split with `chunk_index` / `total_chunks`, each piece ≤ 4000 characters.

Not verified yet, because it needs your approval and the live server: an end-to-end post, MCP tool calls from a Grok session, and a conclusion showing up after the deriver runs.

## Review points

1. **`local-honcho` vs `honcho`.** This checkout's origin slug is `local-honcho`. Pi on the same checkout computes the same name. Confirm that is the workspace you want shared. Switching to the `upstream` remote would fork Grok off the workspace Pi writes.
2. **Tool arguments stay out.** jcode stores a 2 KB scrubbed input. pi stores the full input and the full observation. This package stores the tool name and a status word. Say if you want the 2 KB input back.
3. **One workspace per session cwd.** A Grok session started in `~/workspace` that edits `honcho/` does not also write to `local-honcho`. Splitting one journal by the cwd inside each tool call was the earlier idea; the path-as-name decision replaced it.
4. **Dialectic target.** Applied. `honcho_ask` defaults to agent-grok looking at itself, because `(agent-grok, dsidlo)` stayed empty. Pass `target=dsidlo` to ask about the user.
5. **Install.** Phase 1 and Phase 2 are copies plus a config merge plus a user systemd unit. Nothing runs until you accept a phase.
