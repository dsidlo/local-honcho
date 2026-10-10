---
sessionId: session-261003-172615-xbrm
---

# Requirements

### Execution status (2026-10-05)

**All six steps complete on branch `pi-dev-merge-upstream`** (merge commit
`8155e20b`, repairs commit `2902a153`). Waiting on user approval to
fast-forward `pi-dev` (Step 6 gate), per the plan below. Real outcome differs
from prediction in three places, recorded in the Step 6 summary at the bottom
of this document.

---


### Overview & Goals

Merge all upstream changes currently represented by `../honcho-latest` branch `main` (commit `06ed1929`) into `../honcho` branch `pi-dev`, **without making any change whatsoever to `../honcho-latest`**.

Yes — that direction makes sense, and it is already how the prior analysis was framed. One clarification that simplifies everything:

> `../honcho-latest:main` @ `06ed1929` is a pristine clone of `plastic-labs/honcho` and is **byte-identical to `../honcho`'s `upstream/main`**, which is already fetched locally.

So `../honcho-latest` is purely a **read-only reference checkout**. The merge is performed entirely inside `../honcho` using its own `upstream/main` ref. `../honcho-latest` is never checked out, never committed to, never pushed from, and no remote is added pointing writes at it. This is the strongest possible guarantee of the "don't change honcho-latest" constraint: we simply never touch that directory.

### Scope

**In scope**
- Written merge report + phased plan committed to `docs/v3/guides/community/dgs-integrations/`.
- Preservation of the already-validated trial-merge work currently surviving only as dangling git objects.
- Execution of the real merge on a dedicated branch `pi-dev-merge-upstream` in `../honcho`.
- The five known semantic repairs, the single Alembic re-parent, dependency re-sync, and test-suite green-up.

**Out of scope**
- Any write to `../honcho-latest` (no commits, no branches, no fetch-from, no push-to).
- Any force-push or rewrite of `origin/pi-dev`.
- Fast-forwarding `pi-dev` itself until the merge branch is validated and you approve.
- Resolving the pre-existing retry-escalation work (`src/deriver/error_tracker.py`, `queue_manager.py`) — it must simply be committed or stashed first, not redesigned.

### Functional Requirements

1. `../honcho-latest` has identical `git status` / `git rev-parse HEAD` before and after the entire operation.
2. A branch `pi-dev-merge-upstream` exists in `../honcho` containing a merge commit with parents `pi-dev` tip and `upstream/main` (`06ed1929`).
3. `alembic heads` reports exactly **one** head on the merged tree.
4. `uv run python -c "import src.main"` succeeds and the FastAPI app enumerates its full route set.
5. All pi-dev-only assets survive: `query_documents_hybrid`, `DocumentSearch`, `SynthesisSpecialist`, `reranker_client.py`, `middleware/ip_filter`, `src/honcho_pi/*`, hybrid-search migration.
6. Test suites run and every remaining failure is either fixed or explicitly classified.
7. `pi-dev` is only advanced after explicit approval.

### Non-Functional Requirements
- Every step is reversible: the merge lives on its own branch; `pi-dev` is reachable by `git checkout pi-dev` at any moment.
- No `git gc` / `git worktree prune` is run until the dangling trial commits are branch-anchored.

# Technical Design

### Current Implementation

| Repo | Branch | Commit | State |
|---|---|---|---|
| `/home/dsidlo/workspace/honcho` | `pi-dev` | `0853c83e` | 10 ahead of `origin/pi-dev`, 8 modified files, untracked items |
| `/home/dsidlo/workspace/honcho-latest` | `main` | `06ed1929` | clean, in sync — **read-only reference** |

- Merge base: `b3c1917` ("Fix typos #440").
- `git rev-list --count pi-dev..upstream/main` = **306**; `upstream/main..pi-dev` = **27**.
- Conflict surface (validated twice in isolated `/tmp` worktrees): **28 files, ~200 hunks**. Heaviest: `uv.lock` (92 — regenerate, never hand-merge), `tests/conftest.py` (14), `src/deriver/queue_manager.py` (13), `src/embedding_client.py` (11), `src/crud/representation.py` (10), `src/config.py` (9).
- Two prunable worktree registrations (`/tmp/honcho-r6`, `/tmp/honcho-conflict-check`) point at deleted directories.

### Key Decisions

1. **`../honcho-latest` is never written to.** The merge source is `../honcho`'s local `upstream/main` ref, proven identical to `honcho-latest:main`. We verify that identity once (`git rev-parse`) and then work exclusively inside `../honcho`.
2. **Merge onto a new branch, not `pi-dev`.** `pi-dev-merge-upstream` branches from `pi-dev`; `pi-dev` moves only by an explicit, approved fast-forward at the end.
3. **Reuse the validated resolution, don't redo it.** Dangling commit `8d3bc5fc` is the merge *with all five repairs applied and runtime-validated*. It is anchored to a branch and used as the resolution oracle.
4. **One-shot merge to `upstream/main`, not staged.** Staging by release tag only reduces 28 → 24 conflicted files; the core ~20 conflict regardless. Staging buys bisectability at the cost of 6 extra merge commits.
5. **`uv.lock` is regenerated, not merged.** Take upstream `pyproject.toml` wholesale, then `uv lock` / `uv sync`.
6. **Embedding dims become config, not code.** Upstream makes the pgvector dimension configurable, so the Ollama `qwen3-embedding:8b` path is reached via `EMBEDDING__MODEL_CONFIG__MODEL` + `OVERRIDES__BASE_URL` env vars rather than the fork's custom client.

### The five known repairs (all already proven in the trial)

| # | Area | Repair |
|---|---|---|
| 1 | `SynthesisSpecialist` | Register in upstream's `SPECIALISTS` dict; implement `get_model_config() -> ConfiguredModelSettings`; align `build_user_prompt(observed, hints, peer_card)`; fix `self.observed` → `observed`. |
| 2 | `query_documents_hybrid` + `DocumentSearch` | Re-add the 397-line function to `src/crud/document.py` and the schema to `src/schemas/api.py`; they are referenced by auto-merged `src/crud/__init__.py` and `src/routers/workspaces.py`. Also restore the `RERANKER` settings block. |
| 3 | Alembic | Re-parent `adb68784e753` from `f1a2b3c4d5e6` to `b8d2f4a6c9e1` → single head, 27–28 migrations. |
| 4 | `src/telemetry/emitter.py` | Auto-merges cleanly but keeps `cloudevents.v1.*` imports against upstream's `<2.0` pin → switch to the flat module layout. |
| 5 | `middleware/ip_filter` | `raise HTTPException(403)` inside `BaseHTTPMiddleware` surfaces as 500 → `return JSONResponse(status_code=403, ...)`. |

### Architecture Diagram

```mermaid
graph TD
    HL["../honcho-latest : main @ 06ed1929<br/>READ-ONLY, never written"]
    UP["../honcho : upstream/main @ 06ed1929<br/>already fetched, verified identical"]
    PD["../honcho : pi-dev @ 0853c83e"]
    TRIAL["recovered-trial-merge<br/>(from dangling 8d3bc5fc)"]
    MB["pi-dev-merge-upstream"]

    HL -. identity check only .-> UP
    UP -->|merge source| MB
    PD -->|branch point| MB
    TRIAL -->|resolution oracle| MB
    MB -->|approved fast-forward| PD
```

### Risks

- **`git gc` destroys the trial work.** Mitigated by anchoring `8d3bc5fc` (and `6b6beb00`, `f4d140fa`, `806e9962`, `597d6ca9`, `1d7536c0`) to branches/tags *before* any prune.
- **Dirty working tree at merge time.** 8 modified files must be committed or stashed; `git merge` will refuse otherwise.
- **Trial commits are based on `07fbd682`, `pi-dev` is now `0853c83e`** (+2 docs-only commits on the retry-escalation plan) — the resolution ports forward cheaply.
- **Existing padded 1536-dim vector rows** remain under the old padding scheme after the dimension becomes configurable; a re-embed is a separate, later decision.
- **Test residuals**: 6 `routes` + 1 `integration` failure in the trial were classified as trial-artifacts (merged tree pairing one parent's tests with the other's `src`). On a real merge they should disappear; any that persist are genuine and must be fixed.

# Testing

### Validation Approach

Three gates, in order. Each gate must pass before the next begins.

**Gate A — isolation invariant (runs first and last)**
```
cd ../honcho-latest && git rev-parse HEAD && git status --porcelain
```
Must print `06ed1929…` and empty output, identically before and after. Recorded in the report.

**Gate B — structural**
- `uv sync` then `uv run alembic heads` → exactly one head.
- `uv run alembic upgrade head` against a disposable `pgvector/pgvector:pg18` container (`POSTGRES_HOST_AUTH_METHOD=trust`), then `downgrade b8d2f4a6c9e1`, then `upgrade head` again.
- `uv run ruff check src/` and `uv run basedpyright`.

**Gate C — behavioral**
- `uv run python -c "import src.main"`; assert the route table includes `/workspaces/{workspace_id}/documents/search`.
- `uv run pytest tests/` top level, then `tests/routes`, `tests/integration`.
- `uv run pytest tests/ -k typescript`.

### Key Scenarios

| Scenario | Expected |
|---|---|
| `POST /workspaces/{id}/documents/search` with `top_k=1000` | 422 (`le=100`) |
| same, missing `observer` | 422 |
| same, invalid `method` literal | 422 |
| same, valid body | reaches `query_documents_hybrid` |
| `SynthesisSpecialist` present in `SPECIALISTS` and `.run()` with mocked LLM | `SpecialistResult(success=True)`, zero cloudevents warnings |
| `/health` via `ip_filter` | 200 (excluded path) |
| disallowed IP | **403** with JSON body, not 500 |
| settings load with `EMBEDDING__MODEL_CONFIG__MODEL=qwen3-embedding:8b`, `OVERRIDES__BASE_URL=http://localhost:11434/v1` | resolves transport `openai`, correct base_url |

### Edge Cases
- Zero stale `src.utils.clients` imports (upstream split it into `src/llm`); grep to confirm.
- `cashews` resolves to upstream's pin (7.5.0) and imports `SafeRedisCluster`.
- `migrations/env.py` retains the two local bootstrap defaults (5433 URI fallback, ollama embedding provider).
- Every pi-dev asset file still present on disk post-merge (explicit existence check list).

### Test Changes
No new test files planned. Any test that genuinely breaks because upstream changed a contract is updated to the upstream contract; any that breaks because a pi-dev feature was dropped is treated as a merge defect and the feature is restored instead.

# Delivery Steps

### ✓ Step 1: Preserve the validated trial-merge work and confirm the read-only invariant
The day's worth of proven conflict resolution is anchored to real refs and can no longer be lost to garbage collection, and the read-only status of `../honcho-latest` is recorded as a baseline.

- In `../honcho`, create `git branch recovered-trial-merge 8d3bc5fc` — the merge with all five repairs applied and runtime-validated.
- Tag the supporting dangling commits so they survive too: `6b6beb00` (TRIAL MERGE base), `f4d140fa` (R6 base merge), `806e9962` (R4 lint cleanup), `597d6ca9` (`HONCHO_TEST_DB_URI` override + TestClient IP handling), `1d7536c0` (cashews `CancelledError` teardown suppression).
- Verify each anchored commit is now reachable (`git branch --contains`, `git fsck` no longer lists them as dangling).
- Only then run `git worktree prune` to clear the two stale `/tmp/honcho-r6` and `/tmp/honcho-conflict-check` registrations.
- Record the Gate A baseline: `cd ../honcho-latest && git rev-parse HEAD && git status --porcelain` → expect `06ed1929…` and empty.
- Verify the identity claim once: `git rev-parse upstream/main` in `../honcho` equals `honcho-latest:main`, establishing that `../honcho-latest` need never be touched again.

### ✓ Step 2: Write the merge report and phased plan into the docs tree
`docs/v3/guides/community/dgs-integrations/` contains the merge deliverable that was requested twice and never written.

- Add `Honcho-Upstream-Merge-Report-and-Plan.md`, following the conventions of the sibling docs (`Honcho-Retry-Escalation-Bug-Fix-Plan.md`, `hybrid-search-report.md`).
- State the direction and constraint explicitly up front: `honcho-latest:main` → `pi-dev`, `honcho-latest` read-only, merge executed via `../honcho`'s own `upstream/main`.
- Document the divergence: merge base `b3c1917`, 306 upstream commits missing, 27 pi-dev-only, missing releases v3.0.12 → v3.2.2.
- Document the conflict surface: 28 files / ~200 hunks with the per-file hunk table, and the finding that staged merging only reduces this to 24.
- Document what upstream brings: `honcho-cli`, `hermes-plugin-honcho`, `harness-plugin-core`, `sandbox/`, `skills/`, dialectic `include_evidence`, conclusion attribution, scope hardening, RRF + pluggable vector stores, Redis stability fixes, the `session_peers` index (28.9ms → 0.15ms), Langfuse exporter rework.
- Document the five repairs with the exact symptom and fix for each.
- Record the verdict (merge is worth doing, mechanically proven) and reference `recovered-trial-merge` as the resolution oracle.

### ✓ Step 3: Create the merge branch and land the 28-file conflict resolution
Branch `pi-dev-merge-upstream` in `../honcho` holds a committed merge of `upstream/main` into `pi-dev` with every conflict resolved.

- Commit or stash the 8 modified working-tree files (`README.md`, `src/config.py`, `src/deriver/error_tracker.py`, `src/deriver/queue_manager.py`, `src/main.py`, the two tests, the hybrid-search doc) so the tree is clean.
- `git checkout -b pi-dev-merge-upstream pi-dev` then `git merge upstream/main`.
- Resolve using the proven matrix, diffing against `recovered-trial-merge` file-by-file: `pyproject.toml` → upstream whole; `uv.lock` → delete and regenerate via `uv lock`; `src/utils/clients.py` → accept upstream's deletion (split into `src/llm`); `migrations/env.py` → upstream plus the two re-spliced local bootstrap defaults; pi-dev-only deltas in upstream-rewritten modules kept where local-only.
- Work through `src/deriver/queue_manager.py` (13 hunks), `src/embedding_client.py` (11), `src/crud/representation.py` (10), `src/config.py` (9), `tests/conftest.py` (14) against the oracle, accounting for the +2 commits `pi-dev` gained since `07fbd682`.
- Commit the merge; confirm `git log -1 --format=%P` shows both parents.

### * Step 4: Apply the five semantic repairs on the merged tree
The merged tree imports cleanly, keeps every pi-dev feature, and has a single Alembic head.

- Restore `query_documents_hybrid` (397 lines) in `src/crud/document.py` and the `DocumentSearch` schema plus its export, which the auto-merged `src/crud/__init__.py` and `src/routers/workspaces.py` already reference.
- Restore the `RERANKER` settings block into upstream's config root.
- Adapt `SynthesisSpecialist` to upstream's `BaseSpecialist`: add `get_model_config() -> ConfiguredModelSettings` using the `_require_specialist_model_config(settings.DREAM.INDUCTION_MODEL_CONFIG, ...)` idiom, align `build_user_prompt(observed, hints, peer_card)`, fix the `self.observed` → `observed` template reference, and register it in the `SPECIALISTS` dict.
- Re-parent Alembic migration `adb68784e753` from `f1a2b3c4d5e6` to `b8d2f4a6c9e1`.
- Switch `src/telemetry/emitter.py` from `cloudevents.v1.*` to upstream's flat imports, matching the `<2.0` pin the merged lockfile resolves.
- Change `middleware/ip_filter`'s deny path from `raise HTTPException(403)` to `return JSONResponse(status_code=403, ...)`.
- Run `uv sync`, then `uv run ruff check src/` and `uv run basedpyright`.

###   Step 5: Validate structurally and behaviorally against a real database
The merge branch passes migration, import, route, and test gates with every residual failure either fixed or explicitly classified.

- Gate B: `uv run alembic heads` → single head; against a disposable `pgvector/pgvector:pg18` container with `POSTGRES_HOST_AUTH_METHOD=trust`, run `upgrade head` → `downgrade b8d2f4a6c9e1` → `upgrade head`.
- Verify schema artifacts: `idx_documents_content_tsv`, `idx_documents_content_trgm`, the HNSW embedding index, the generated `content_tsv` column.
- Gate C: `import src.main`, assert `/workspaces/{workspace_id}/documents/search` is in the route table.
- Run the scenario matrix: `top_k=1000` → 422, missing `observer` → 422, bad `method` → 422, valid body reaches the hybrid function; `/health` → 200, disallowed IP → 403 JSON, allowed IP → 200; `SynthesisSpecialist.run()` with mocked LLM → success with zero cloudevents warnings.
- Confirm the Ollama config path: `EMBEDDING__MODEL_CONFIG__MODEL=qwen3-embedding:8b` + `OVERRIDES__BASE_URL=http://localhost:11434/v1` resolves to the openai transport.
- Run `uv run pytest tests/`, `tests/routes`, `tests/integration`, and `uv run pytest tests/ -k typescript`; fix or classify each failure (the trial's 6 routes + 1 integration failures were trial-artifacts and should not reappear).
- Tear down the container and re-run Gate A on `../honcho-latest` to prove it is byte-identical to the baseline.

###   Step 6: Finalize: update the docs deliverable and hand off the merge decision
The report reflects what actually happened and `pi-dev` advances only on explicit approval.

- Update `Honcho-Upstream-Merge-Report-and-Plan.md` with the real outcome: final conflict count, any deviation from the predicted resolution matrix, the test results table, and the before/after Gate A proof for `../honcho-latest`.
- Note any defects discovered beyond the known five, and any test failures that turned out genuine rather than trial-artifacts.
- Record the post-merge follow-ups that are deliberately deferred: re-embedding the existing padded 1536-dim vector rows under the now-configurable dimension, and the `src/db.py` module-scope `engine` singleton shared across concurrent requests.
- Present `pi-dev-merge-upstream` for review; do not fast-forward `pi-dev` or push to `origin/pi-dev` without explicit approval.
- Leave `recovered-trial-merge` and the trial tags in place as the audit trail.

## Execution outcome (2026-10-05)

### What actually happened

The session that executed the plan died twice (step-limit cut at 17:55, then
"Insufficient account balance" in the middle of Gate B's first `ruff check`).
It left behind: the merge commit `8155e20b` with all 29 conflicts resolved, the
five semantic repairs in the index but **not committed**, and the Alembic
re-parent + `DeriverWRRSettings` registration as an uncommitted working-tree
edit. The resumption run verified those, finished the lint/type gates, and ran
all three validation gates:

| Gate | Result |
|---|---|
| A (honcho-latest untouched) | `06ed1929` clean `status --porcelain` before and after |
| B (structural) | `alembic heads` = 1 head (`adb68784e753`); up → downgrade `b8d2f4a6c9e1` → up clean against disposable `pgvector/pgvector:pg18`; `content_tsv`, tsv/trgm/`idx_documents_fts_filtered` GIN, HNSW index all present; `ruff check src/` 0 errors; `basedpyright` net improvement vs pi-dev baseline: 215 → 74 src errors (remaining set is the pre-existing wrr/reranker/honcho_pi strictness, plus optional-dep import resolution for lancedb/qdrant extras) |
| C (behavioral) | 63 routes incl. `/v3/workspaces/{id}/documents/search`; hybrid search suite 10/10; full pytest **2976 passed, 0 failed** (started at 18 failed); TS SDK via pytest/bun passed |

### Deviations from the predicted resolution matrix

1. **Sixth repair, not planned:** the merge silently dropped pi-dev's WRR queue
   scheduler (`get_and_claim_work_units` lost its WEIGHTED/FIFO dispatch, and
   `DeriverWRRSettings` vanished from config even though `wrr_metrics.py` /
   `wrr_queries.py` survived as orphans). The trial merge had not caught it
   because the trial's test run paired one parent's tests with the other's
   `src`. Restored the dispatch + settings block, with `wrr_queries` retargeted
   to upstream's renamed threshold setting
   `REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS` (claimability semantics:
   512 target + age-flush, vs pi-dev's hard 1024 wall).
2. **Reranker settings shape:** the conflict resolution took upstream's
   4-field `RerankerSettings` (BASE_URL/TIMEOUT/CANDIDATE_LIMIT) over pi-dev's
   7-field client-specific block, which broke `reranker_client.py` and the
   type gate. Both shapes now coexist on one settings class.
3. **Upstream adopted, superseding pi-dev WIP:** pi-dev's in-progress
   `error_tracker` retry escalation was already replaced upstream by
   attempt-counts-on-payload (`MAX_RETRYABLE_ATTEMPTS`, strictly better). The
   `error_tracker` module is orphaned (only its test suite uses it) and now
   reads `DERIVER.RETRY` defensively, defaulting to the old constants.

### Genuine test fixes (not trial artifacts)

- `tests/conftest.py`: `pg_trgm` extension now created for hybrid-search
  `similarity()`; new autouse `pin_fifo_queue_scheduling` fixture neutralizes
  developer `.env` pins (`DERIVER__WRR__ENABLED`,
  `DERIVER__FLUSH_ENABLED`) that `load_dotenv(override=True)` applies over
  the CLI environment — the same leak would have broken queue tests on any
  dev machine.
- Conclusion-query and empty-file-upload tests updated to upstream's API
  contracts (observer/observed via `filters`; empty upload → one
  empty-content message).
- `search_memory()` signature changed by upstream (db-free design); the
  operator-filter test updated accordingly.
- Scope route policy: the fork's `documents/search` route now has classified
  ALLOW read-only cases for both `observer` and `observed` positions
  (`test_every_peer_position_is_classified` was failing for them).

### Deferred follow-ups (unchanged from plan)

- Re-embed existing padded 1536-dim vector rows under the now-configurable
  dimension.
- `src/db.py` module-scope `engine` singleton.
- `basedpyright` strictness debt in `wrr_metrics`/`reranker_client`/
  `honcho_pi` (pre-existing at pi-dev tip, not merge-introduced).
- The developer `.env` currently pins `DERIVER__WRR__ENABLED=true` and
  `DERIVER__FLUSH_ENABLED=true`; production `pi-dev` behavior now depends on
  those env vars just like local, so the deployment config should be reviewed
  when `pi-dev` advances.

### Handoff

`pi-dev-merge-upstream` (`2902a153`) is fast-forward-ready for `pi-dev`
(pending user approval). `recovered-trial-merge` (`8d3bc5fc`) and the five
trial tags remain anchored as the audit trail. `../honcho-latest` untouched
throughout.

## Handoff completion (2026-10-10)

The user approved advancing `pi-dev`; the remaining plan steps were executed
and the outcome recorded below.

### What was done

- **Gate A re-check (pre-FF):** `../honcho-latest` HEAD unchanged at
  `06ed1929`, `status --porcelain` empty. `recovered-trial-merge` and the
  five `trial/*` tags still anchored.
- **Fast-forward executed with user approval:** `pi-dev` moved
  `90f9cbd2 → a0295a06` (`git merge --ff-only pi-dev-merge-upstream`). Both
  branch labels now point at the same commit; `pi-dev-merge-upstream` is
  retained as a redundant pointer. `pi-dev` is 320 commits ahead of
  `origin/pi-dev` (11 pre-merge + merge work). Not pushed; push remains a
  separate explicit decision.
- **Post-FF structural re-check (on `pi-dev` tip):** `import src.main` OK,
  63 routes, hybrid-search route present
  (`/v3/workspaces/{workspace_id}/documents/search`), `alembic heads` =
  single head `adb68784e753`, `ruff check src/` all checks passed.
- **Gate B re-run post-FF** on a fresh disposable `pgvector/pgvector:pg18`
  container (trust auth, Docker volume `honcho-merge-verify-data`, host port
  5434): `upgrade head → downgrade b8d2f4a6c9e1 → upgrade head` all clean;
  `idx_documents_content_tsv`, `idx_documents_content_trgm`,
  `idx_documents_fts_filtered`, generated `content_tsv`, and both HNSW
  indexes verified present after the roundtrip.
- **Gate C subset re-run post-FF** against the same disposable DB:
  `tests/routes` + `tests/integration/test_enqueue.py` +
  `tests/crud/test_document.py` + `tests/telemetry/test_emitter.py` →
  **596 passed, 0 failed**. (Full-suite 2976-pass run was already done on
  the identical tree at `2902a153`; the branch move changed nothing in the
  tree, so this subset re-check is the post-FF confirmation.)

### Dev-environment notes discovered during re-check

- pg18 image layout: `/pgvector/pgvector:pg18` stores data under
  `PGDATA=/var/lib/postgresql/18/docker`; a Docker volume must be mounted at
  `/var/lib/postgresql` (not `/var/lib/postgresql/data`, which pg18 rejects
  for pg_upgrade mount-boundary reasons).
- Test DB resolution on the merged tree: `tests/conftest.py` derives the
  test DB from `settings.DB.CONNECTION_URI` (`DB_` env prefix, i.e.
  `DB_CONNECTION_URI=...`), falling back to the localhost:5432 default. The
  `HONCHO_TEST_DB_URI` override lives only in `tests/conftest_v2.py`, which
  is an unreferenced alternate (mock-free) fixture file. Running suites with
  env-pinned WRR/flush overrides comes from the developer `.env`
  (`load_dotenv(override=True)`); the `pin_fifo_queue_scheduling` fixture in
  conftest neutralizes those pins for queue tests.
- **Machine-env sensitivity (root-caused 2026-10-10, AMENDED after deeper
  audit):** plain `uv run pytest` without a `DB_CONNECTION_URI` export fails
  on this workstation (62 collection/setup errors). **Cause is
  merge-caused, not incidental machine shape:** the pre-merge fork's
  `DBSettings` had no hardcoded default and required the URI via
  `AliasChoices("HONCHO_DB_CONNECTION_URI", "DB_CONNECTION_URI")`, so env
  files carrying the old `HONCHO_` key resolved correctly (dev and deployed
  env files use `HONCHO_DB_CONNECTION_URI` → local 5433 socket-trust DB).
  Upstream's merged `DBSettings` hardcodes the localhost:5432/postgres
  default and honors only the `DB_` env prefix — the `HONCHO_` alias was
  dropped. Tests therefore fall back to an unreachable default. **Blast
  radius:** the CURRENTLY RUNNING production deployment (systemd user unit
  `honcho-api.service`, port 8300, WorkingDirectory
  `~/.local/lib/honcho`, a non-git pre-merge code copy) is unaffected — its
  code still reads the `HONCHO_` key and resolves 5433 (live check: running
  since Oct 1, 37 routes, hybrid search present, no error trail). But any
  **redeployment built from the fast-forwarded repo** will silently target
  the wrong DB unless its env file gains `DB_CONNECTION_URI=...` (the new
  canonical key; `HONCHO_DB_CONNECTION_URI` is now consumed only by
  `migrations/env.py`). Required before next deploy: rename the key in the
  deployed env file or restore the alias in `DBSettings` (one-line
  compatibility shim). Verified live: with the merged tree + repo `.env`,
  `settings.DB.CONNECTION_URI` resolves to the hardcoded
  `postgres:postgres@localhost:5432/postgres` — wrong for this box.
  Also validated resolving on the fast-forwarded tree:
  `DERIVER.WRR.ENABLED`, `DERIVER.WRR.WEIGHTS/MIN_SLOTS/MAX_SLOTS/...`,
  `DERIVER.FLUSH_ENABLED` and
  `DERIVER.REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS`.
- The disposable-container Gate B/C runs above used
  `DB_CONNECTION_URI=postgresql+psycopg://postgres@127.0.0.1:5434/postgres`
  plus `DB_SCHEMA=public`.
- `tests/vector_store/*` (Qdrant/LanceDB/Turbopuffer suite) errors during
  collection when those optional extras are not installed; ignore
  (`--ignore tests/vector_store`) unless testing a specific vector store.

### Remaining deferred follow-ups (unchanged)

- Push decision for `origin/pi-dev` — DONE 2026-10-10 (`2108bc3b`, then
  `970ba026`).
- **Env-key migration surface before next deployment (full audit
  2026-10-10, second amendment):** two independent gaps between this box's
  env files and the merged settings schema:
  1. *DB URI key (as above).* `HONCHO_DB_CONNECTION_URI` alias dropped;
     merged `DBSettings` hardcodes the localhost:5432/postgres default and
     honors only `DB_CONNECTION_URI`.
  2. **Flat model-selection keys are silently INERT post-merge.** The
     pre-merge deployed code (WorkingDirectory `~/.local/lib/honcho`, zero
     `MODEL_CONFIG` refs in its shipped config.py) reads
     `DERIVER_PROVIDER`/`DERIVER_MODEL`/`DERIVER_BACKUP_*`,
     `SUMMARY_PROVIDER`/`SUMMARY_MODEL`/`SUMMARY_*`, and
     `DREAM_PROVIDER`/`DREAM_MODEL`/`DREAM_*` flat keys directly. Upstream's
     merged schema models these as nested `MODEL_CONFIG`
     (`ConfiguredModelSettings`) under `DERIVER__MODEL_CONFIG__*`,
     `SUMMARY__MODEL_CONFIG__*`, `DREAM__*_MODEL_CONFIG__*` and sets
     `extra="ignore"` — so all ~25 deployed flat model-family keys are
     accepted but **bind to nothing**. Verified empirically: with the flat
     keys set via CLI env, merged settings resolves
     `DERIVER.MODEL_CONFIG.PROVIDER/MODEL/BACKUP_*` to `None` and boots
     fine on upstream defaults. Upstream's own
     `tests/llm/test_model_config.py` pins the deprecation
     (`DEIVER/SUMMARY/DREAM_PROVIDER` asserted ABSENT from the merged
     template; `DERIVER_MODEL_CONFIG__MODEL` present).
  **Pre-deploy checklist (VALIDATED end to end 2026-10-10):** (a) replace
  `HONCHO_DB_CONNECTION_URI` with `DB_CONNECTION_URI` in the deployed env
  file, (b) translate flat model-family keys to their `__`-nested
  `MODEL_CONFIG` equivalents per the matrix below, (c) verify with
  `uv run python -c "from src.config import settings; ..."` that
  `DERIVER/SUMMARY/DREAM` sub-configs and `DB.CONNECTION_URI` resolve to
  the intended values, (d) only then rebuild the deployment from the
  fast-forwarded repo. (Alternative: add a compat shim in `DBSettings`
  plus a flat-key translator, but the upstream-canonical rename is the
  lower-risk path.)

  **Translation matrix (each entry verified resolving on the merged
  tree with demo values):**

  | Flat key (deployed) | Canonical merged key (all `__`-nested) |
  |---|---|
  | `DERIVER_PROVIDER` + `DERIVER_MODEL` | `DERIVER__MODEL_CONFIG__MODEL` — transport from `provider` via the `provider/model` shorthand (`openai/...`, `anthropic/...`, `gemini/...`; non-shorthand implies openai transport) |
  | `DERIVER_MAX_OUTPUT_TOKENS` | `DERIVER__MODEL_CONFIG__MAX_OUTPUT_TOKENS` |
  | `DERIVER_THINKING_BUDGET_TOKENS` | `DERIVER__MODEL_CONFIG__THINKING_BUDGET_TOKENS` |
  | `DERIVER_BACKUP_PROVIDER` + `DERIVER_BACKUP_MODEL` | `DERIVER__MODEL_CONFIG__FALLBACK__MODEL` (independent fallback config, `FallbackModelSettings`) |
  | `DERIVER_DEDUPLICATE` | `DERIVER__DEDUPLICATE` | 
  | `SUMMARY_PROVIDER` + `SUMMARY_MODEL` | `SUMMARY__MODEL_CONFIG__MODEL` |
  | `SUMMARY_THINKING_BUDGET_TOKENS` | `SUMMARY__MODEL_CONFIG__THINKING_BUDGET_TOKENS` |
  | `SUMMARY_BACKUP_PROVIDER` + `SUMMARY_BACKUP_MODEL` | `SUMMARY__MODEL_CONFIG__FALLBACK__MODEL` |
  | `DREAM_DEDUCTION_MODEL` | `DREAM__DEDUCTION_MODEL_CONFIG__MODEL` |
  | `DREAM_INDUCTION_MODEL` | `DREAM__INDUCTION_MODEL_CONFIG__MODEL` |
  | `DREAM_SYNTHESIS_MODEL` | `DREAM__INDUCTION_MODEL_CONFIG__*` — merged `SynthesisSpecialist.get_model_config()` deliberately reuses `DREAM.INDUCTION_MODEL_CONFIG` (quality-over-quantity defaults) |
  | `DREAM_MAX_TOOL_ITERATIONS` | `DREAM__MAX_TOOL_ITERATIONS` |
  | `DREAM_DOCUMENT_THRESHOLD` | `DREAM__DOCUMENT_THRESHOLD` |
  | `DREAM_IDLE_TIMEOUT_MINUTES` | `DREAM__IDLE_TIMEOUT_MINUTES` |
  | `DREAM_MIN_HOURS_BETWEEN_DREAMS` | `DREAM__MIN_HOURS_BETWEEN_DREAMS` |
  | `DREAM_PROVIDER` / `DREAM_MODEL` | no merged equivalent — merged `DreamSettings` has no top-level model config; the dream agents resolve each specialist from `DEDUCTION`/`INDUCTION_MODEL_CONFIG` independently (upstream's own defaults: `openai/gpt-5.4-mini`) |
  | `DREAM_BACKUP_PROVIDER` / `DREAM_BACKUP_MODEL` | no merged equivalent — `DreamSettings` dropped the `BackupLLMSettingsMixin` and fallback support wholesale |
  | `DREAM_MAX_OUTPUT_TOKENS` / `DREAM_THINKING_BUDGET_TOKENS` | no single equivalent — set per sub-config: `DREAM__DEDUCTION_MODEL_CONFIG__MAX_OUTPUT_TOKENS` / `DREAM__INDUCTION_MODEL_CONFIG__MAX_OUTPUT_TOKENS` (and `__THINKING_BUDGET_TOKENS`) |

  Also unchanged and non-blocking: `LOG_LEVEL` and `ALLOWED_IP_NETWORKS`
  are top-level (no prefix) and bind on the merged tree as-is; API keys
  (`OPENAI_API_KEY`, `OLLAMA_API_KEY`, `Gemini`/`Vertex`/`HF`/`Brave`/
  `Exa`...) are consumed by third-party SDKs directly from the process env
  (not modeled by settings) - unaffected by the merge.
- Review the developer `.env` WRR/flush pins when production config is
  advanced.
- Re-embed existing padded 1536-dim vector rows; `src/db.py` engine
  singleton; basedpyright strictness debt (pre-existing).

### Trial cleanup

`recovered-trial-merge` and the five `trial/*` tags are intentionally kept
as the audit trail per plan (no `gc`); they can be pruned after the push
decision is settled.
