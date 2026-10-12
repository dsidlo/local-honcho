# Honcho Integration for jcode

Source and tests for the jcode ↔ Honcho integration, mirroring the layout of
the other integrations in this directory (`grok-build/`, `pi-mono/`). The
production copy of this integration lives in
`~/workspace/tools/jcode-honcho` and is installed onto the machine from there
(hooks wired in `~/.jcode/config.toml`, skills copied to `~/.jcode/skills`,
binaries symlinked to `~/bin`); this tree is the canonical, tested snapshot.

## Layout

```
jcode/
├── src/                     integration code (importable module layout)
│   ├── honcho_mcp.py        stdio MCP server: honcho_store / honcho_search /
│   │                        honcho_ask / honcho_conclusions
│   ├── jcode_honcho_tailer.py  session-journal tailer (systemd service),
│   │                           byte-offset resumable, fail-open spooling
│   ├── jcode_secret_gate.sh    pre_tool hook blocking credential exposure
│   └── jcode_honcho_capture.sh session/turn lifecycle hook (observer)
├── tests/                   pytest suites (see below)
├── pytest.local.ini         tree-scoped pytest config
├── honcho-conventions.md    binding capture-scrubbing policy + memory contract
└── run-coverage.sh          runs everything with a 100% coverage gate
```

Not mirrored here: `skills/` (honcho-ask, honcho-brief, honcho-promote),
`bin/honcho-*` CLI helpers (ask, brief, model-check, failover-watch), and
systemd units. They live in the production repo and `bin/install.sh` syncs
them to `~/.jcode/skills` and `~/bin`; this tree mirrors exactly the parts
that execute inside jcode (MCP server, tailer, hooks).

## Tests

| Suite | Scope | Needs |
|---|---|---|
| `tests/test_honcho_mcp_unit.py` | scrubbing, chunking, workspace detection, tool handlers vs fake API, stdio protocol loop | offline |
| `tests/test_tailer_unit.py` | journal parsing, mapper, spool/retry, state persistence, main loop | offline |
| `tests/test_secret_gate.py` | secret-gate hook allow/block cases (incl. all credential-assignment regexes) | offline |
| `tests/test_capture_hook.py` | capture hook markers vs an in-process fake Honcho (session/turn lifecycle, fire-and-forget) | offline |
| `tests/test_honcho_mcp.py` | MCP server end-to-end vs live Honcho, isolated workspace | live Honcho |
| `tests/test_tailer.py` | tailer end-to-end + restart-no-duplicates | live Honcho |

Isolation invariant: live suites write only to throwaway
`jcode-selftest-*` workspaces and delete them at the end. Real records in
`jcode`, `flexitts`, and pi-mono workspaces are never touched. Suites tagged
`@pytest.mark.live` skip cleanly when no Honcho is reachable at
`:8300` (health probe `/health`).

The capture-hook suite is fully hermetic: it runs the hook against an
in-process `ThreadingHTTPServer` fake, so no port conflicts and no real
records anywhere.

### Run everything

```bash
# from the monorepo root; passes only with 100% statement+branch coverage
bash docs/v3/guides/community/dgs-integrations/jcode/run-coverage.sh
```

### Run one suite

```bash
uv run pytest docs/v3/guides/community/dgs-integrations/jcode/tests/test_secret_gate.py \
    -c docs/v3/guides/community/dgs-integrations/jcode/pytest.local.ini -q
```

## Coverage notes

The 100% gate covers `src/` (the integration code) in both modes; without a
live Honcho the `@live`-marked e2e suites skip and full-tree coverage reads
~95% (test-file lines), which the runner prints informationally. Coverage of
the subprocess children is real: the MCP server and tailer run as actual
processes in the e2e suites. Local Honcho ships an `a1_coverage.pth` that
starts `coverage.process_startup()` in any child interpreter when
`COVERAGE_PROCESS_START` is set, so `run-coverage.sh` sets it and combines
the parallel data files afterward (`coverage combine`).
The two source-side exclusions are documented `# pragma: no branch` markers on
mathematically unreachable chunker arcs; two test-helper paths unreachable
while Honcho is up carry `# pragma: no cover` with an explanation.

Two behavioral fixes were made in `src/` while writing the tests:
- `honcho_mcp.tool_store`: chunk metadata (`is_chunk`, `chunk_index`,
  `total_chunks`) was computed but never attached to messages; fixed and
  the per-chunk recomputation removed.
- `jcode_honcho_tailer.spool`: `os.makedirs` ran outside the fail-open try
  block, so an unwritable spool path could crash the tailer; now fully
  fail-open per the capture contract.
- `jcode_honcho_capture.sh`: made testable without behavior change —
  `HONCHO_BASE_URL` env override honored (was hardcoded to localhost), and
  the redundant empty message POST after session creation was dropped.

Bash sources are covered behaviorally through their pytest drivers (the
secret-gate and capture suites execute the real scripts over subprocess
and record their HTTP effects); `coverage.py` measures the Python
modules, not bash, so the 100% gate below applies to `.py` files.