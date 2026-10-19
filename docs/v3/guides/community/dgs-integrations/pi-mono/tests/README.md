# pi-mono agent integration tests

Black-box tests for the pi-mono agent Honcho integration layer:

- `agent/extensions/honcho.ts` — the main extension (hooks, tools, commands, flush pipeline, import-time configuration)
- `agent/extensions/secret-scrub.ts` — the `/secret-scrub` extension (credential redaction for LLM context)
- `agent/extensions/honcho-apply-learnings-command.ts` — the `/honcho-apply-learnings` command
- `agent/extensions/voice-control.ts` — the voice control extension (settings store, TTS engine delegation)`
- `agent/honcho-apply-learnings/run.sh` — the standalone bash script
- `agent/honcho-upload-document/upload.ts` — the document upload CLI module
- `agent/voice-response/scripts/` — TTS scripts: `tts-toggle.py` (per-engine volume/engine/device), `tts-gain.py` (level→gain), `speak.sh` routing

## Running

```bash
cd docs/v3/guides/community/dgs-integrations/pi-mono
bun test tests/

# with coverage report
bun test --coverage tests/
```

Requires `bun` (tests were written against bun's `bun:test` runner: `mock.module`,
query-string cache-busted dynamic imports, `Response`, `spawnSync`).

## Layout

| Path | Purpose |
| --- | --- |
| `helpers/env.ts` | HONCHO_* env management + `importFresh()` (unique query string → fresh module instance) |
| `helpers/mock-fetch.ts` | Recording `globalThis.fetch` mock with latest-first routes |
| `helpers/mock-pi.ts` | Mock of pi's ExtensionAPI surface (`on`, `registerTool`, `registerCommand`, `ui.notify`) |
| `helpers/honcho-harness.ts` | One-call bootstrap: env + fetch routes + fresh honcho.ts import on a mock pi |
| `apply-learnings-command.test.ts` | `/honcho-apply-learnings` config, success, skip/failure and git-branch paths |
| `apply-learnings-skill.test.ts` | `run.sh` subprocess tests (fresh/update/role-filter/failure) |
| `honcho-config.test.ts` | import-time `honcho.json` + env resolution (cwd, `~/.pi`, `~/.config/pi`, `~/.honcho.json`, env precedence) |
| `honcho-extension-hooks.test.ts` | session lifecycle hooks, observation reflect/dedupe/prune, compaction, tree, workspace auto-detection |
| `secret-scrub.test.ts` | `/secret-scrub` + `/redact[-on|-off]` extensions: pattern engine, `context`/`tool_result` hooks, command toggles, session-start reset |
| `honcho-extension-tools.test.ts` | tools (`honcho_store/chat/list/search`), commands (`honcho-status/flush`), flush batching/chunking/retry |
| `upload-document.test.ts` | upload flow incl. real LiteParse subprocesses for PDF extraction |

## Test-isolation tricks

- **Fresh modules**: `importFresh(path)` appends `?fresh=<per-file prefix>-<n>` so Bun
  re-evaluates honcho.ts (which keeps queue/session state at module scope) per test.
  honcho.ts re-reads env/config at import, so env is set before importing.
- **hermetic config**: config tests `chdir` into sandboxes and point `HOME` at temp dirs
  (Bun's `os.homedir()` honors `HOME`); both restored in `afterEach/finally`.
- **`@sinclair/typebox` stub**: `../node_modules/@sinclair/typebox` is an inert stub — the
  extension only builds schema objects to pass to `registerTool` and never evaluates them.
- **apply-learnings**: `node:fs/promises` is mocked with a shim that virtualizes only the
  hardcoded roots the command touches (settings.json, agents dir, /tmp log, local synthesis
  docs) and falls through to the real fs elsewhere; `node:child_process.exec` is stubbed
  (callback style, controlled per-scenario). This keeps later test files (real temp files)
  unaffected even if bun carries module mocks across files.
- **secret-scrub**: needs no module mocks — the extension performs no import-time I/O besides
  fire-and-forget log appends, so `importFresh` + `createMockPi` suffice. The pure engines
  `scrubText`/`scrubJson` are exported from the module and tested directly.
- **PDF paths** use the real LiteParse CLI (`~/.pi/agent/skills/liteparse/parse.js`) — a
  minimal valid PDF (success) and a garbage PDF (both OCR passes fail) — no mocks.

## Known unreachable code (not covered, see also coverage output)

- `honcho.ts`: `loadConfigFile` (async variant) is defined but never called; the sync loader
  at import time is what actually runs. `estimateRawTailTokens` is defined but never called.
  `reflectObservations(..., mode="forced")` branches (`REFLECT_LIMITS_FORCED`) are not
  reachable: both call sites hardcode `mode: "threshold"`.
- `secret-scrub.ts`: the fire-and-forget log writer's `.catch(() => {})` callback (log-write
  failure path) is uncovered — logging never propagates failures by design.
- `run.sh`: the `if [ -z "$new_learnings" ]` early exit is unreachable — `new_learnings` is
  hardcoded via the simulated echo block.