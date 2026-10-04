# Honcho Upstream Merge: Report and Phased Plan

**Generated:** 2026-10-03
**Direction:** `honcho-latest:main` (`06ed1929`) → `honcho:pi-dev` (`0853c83e`)
**Hard constraint:** `../honcho-latest` is **read-only** and is never written to.

---

## Executive Summary

The `pi-dev` fork diverged from upstream `plastic-labs/honcho` at `b3c1917`
("Fix typos #440"). Since then upstream has landed **306 commits** across five
releases (v3.0.12 → v3.2.2) that `pi-dev` does not have, while `pi-dev` has
accumulated **27 commits** of its own.

A full merge was executed as a trial six separate times in throwaway worktrees,
including a runtime-validated pass against a dockerized `pgvector/pgvector:pg18`
instance. The conclusion is unambiguous:

**The merge is worth doing and is mechanically proven executable.** It costs
28 conflicted files / ~200 hunks, one Alembic re-parent, and five semantic
repairs. No pi-dev feature has to be abandoned.

---

## Direction and Isolation Constraint

`../honcho-latest` is a pristine clone of `plastic-labs/honcho` whose `main`
branch is at `06ed1929`. That commit is **byte-identical** to `../honcho`'s own
`upstream/main` ref, which is already fetched locally:

```
$ git -C ../honcho-latest rev-parse HEAD
06ed1929cf017c333a87c41d130bb6a0605d91c0
$ git -C ../honcho   rev-parse upstream/main
06ed1929cf017c333a87c41d130bb6a0605d91c0
```

Therefore `../honcho-latest` is purely a **read-only reference checkout**. The
entire merge is performed inside `../honcho` against its own `upstream/main`.
`../honcho-latest` is never checked out, never committed to, never fetched from,
never pushed to, and no remote is added that points writes at it. This is the
strongest available guarantee of the constraint: the directory is simply never
touched.

### Gate A — isolation invariant

Run before and after the entire operation; both runs must be identical.

```
cd ../honcho-latest && git rev-parse HEAD && git status --porcelain
```

| Run | HEAD | `status --porcelain` |
|---|---|---|
| Baseline (2026-10-03, pre-merge) | `06ed1929…` | *(empty)* |
| Post-merge | *(recorded at completion)* | *(recorded at completion)* |

---

## The Divergence

| | `pi-dev` | `upstream/main` |
|---|---|---|
| Merge base | `b3c1917` "Fix typos #440" (Mar 22, 2026) | same |
| Commits since base | **27** | **306** |
| Missing releases | — | v3.0.12, v3.1.x, v3.2.0, v3.2.1, v3.2.2 |
| Files touched since base | — | 714 |

190 of the 306 upstream commits touch `src/`.

### What upstream brings

**New components absent from `pi-dev`**

- `honcho-cli` (v0.2.0)
- `hermes-plugin-honcho`
- `harness-plugin-core` (Node)
- `sandbox/` — ephemeral mock-provider stack
- `skills/`
- deterministic `mock_provider` endpoint

**Core server improvements**

- **Dialectic** — `include_evidence` on pair and workspace chat (SDK/MCP/CLI
  support); conclusion attribution via `source_ids` and `times_derived` on the
  API and in filters.
- **Scopes hardening** — scoped-key gating on get-or-create routes; scope read
  options across MCP.
- **Search** — oversized `search_query` truncation before embedding (#1255);
  query-budget reclaim in `get_working_representation`; `top_k=0` guard;
  RRF and pluggable vector stores (Qdrant / Turbopuffer / LanceDB).
- **Stability** — Redis idle-connection relabel and connect retries; redis-py
  8.x cluster leak fix; 503 on provider outages; evidence-after-commit crash
  fix; session delete no longer loads embedding content.
- **Deriver / dreamer** — batch message tagging with peer/target attribution;
  malformed observation-arg rejection; `DERIVER.SCHEDULER=api` mode.
- **Performance** — `session_peers (workspace, peer, session)` index migration
  (**28.9 ms → 0.15 ms**); smaller runtime image; CloudEvents instrumentation.
- **Telemetry** — Langfuse reworked to exporter-only with correct traces; inline
  mode removed.

**A bonus that resolves a previously-feared collision:** upstream makes the
pgvector column dimension configurable (`Vector(_VECTOR_DIM)` from
`EMBEDDING.VECTOR_DIMENSIONS`), whereas the fork hardcodes `Vector(1536)` and
pads Ollama vectors client-side. Post-merge, the Ollama `qwen3-embedding:8b`
path is reached **by environment variable alone**:

```
EMBEDDING__MODEL_CONFIG__MODEL=qwen3-embedding:8b
OVERRIDES__BASE_URL=http://localhost:11434/v1
```

This was validated at runtime: it resolves to `transport=openai` with the
correct `base_url`. The 4096-vs-1536 "collision" is therefore a configuration
fix, not a code fix.

---

## The Conflict Surface

Measured twice in isolated worktrees; the list is stable.

**28 conflicted files, ~200 hunks.** Of the 50 files touched by both sides, 28
conflict and 22 auto-merge cleanly but carry semantic risk.

| File | Hunks | Resolution |
|---|---:|---|
| `uv.lock` | 92 | **Regenerate** — never hand-merge |
| `tests/conftest.py` | 14 | Manual |
| `src/deriver/queue_manager.py` | 13 | Manual |
| `src/embedding_client.py` | 11 | Manual |
| `src/crud/representation.py` | 10 | Manual |
| `src/config.py` | 9 | Manual + re-splice `RERANKER` block |
| `pyproject.toml` | — | Take upstream wholesale |
| `src/utils/clients.py` | — | Accept upstream's **deletion** (split into `src/llm`) |
| `migrations/env.py` | — | Upstream + re-splice 2 local bootstrap defaults |
| `src/crud/document.py` | — | Manual; **must** retain `query_documents_hybrid` |
| `src/schemas/api.py` | — | Manual; **must** retain `DocumentSearch` |
| remaining ~17 | — | Routers, deriver, dreamer, SDK, docs |

### Staged merging does not help

Dry-run merges at each intermediate release tag:

| Merge target | Conflicted files |
|---|---:|
| v3.0.12 | 24 |
| v3.1.0 / v3.1.2 | 26 |
| v3.2.0 / v3.2.1 / v3.2.2 | 28 |
| full `upstream/main` | 28 |

Staging reduces the surface from 28 to 24 at best; the core ~20 files (deriver,
config, routers, crud) conflict regardless of path. Staging buys bisectability
at the cost of six extra merge commits — it does **not** buy conflict avoidance.
**Decision: one-shot merge to `upstream/main`.**

---

## The Five Semantic Repairs

All five were discovered by progressively deeper validation, and all five were
applied and proven in the trial merge. Three of them are invisible to the merge
tool — they only surface at runtime.

### 1. `SynthesisSpecialist` is orphaned by upstream's new registry

**Symptom.** The file survives the merge and the `synthesis` level survives in
`src/utils/types.py`, but upstream's new `SPECIALISTS: dict[str, BaseSpecialist]`
registry (deduction, induction, card-refresh) has no synthesis entry — the old
wiring lived in `orchestrator.py` / `specialists.py`, which upstream rewrote.
Upstream also changed the `BaseSpecialist` abstract contract.

**Fix.**
- Register the specialist in upstream's `SPECIALISTS` dict.
- Implement `get_model_config() -> ConfiguredModelSettings`, using upstream's own
  `_require_specialist_model_config(settings.DREAM.INDUCTION_MODEL_CONFIG, …)`
  idiom.
- Align `build_user_prompt(observed, hints, peer_card)` to the new signature.
- Fix the template reference `self.observed` → the now-passed `observed` argument.

**Proof.** `SynthesisSpecialist.run()` executed end-to-end against the real DB
with a mocked LLM: passed upstream's preflight, built prompts, called the LLM
once, returned `SpecialistResult(success=True)`.

### 2. `query_documents_hybrid` and `DocumentSearch` are silently dropped

**Symptom.** A naive "theirs" resolution of `src/crud/document.py` deletes the
397-line `query_documents_hybrid`, and a "theirs" resolution of
`src/schemas/api.py` drops the `DocumentSearch` schema. Both are still
*referenced* by `src/crud/__init__.py` and `src/routers/workspaces.py`, which
auto-merge cleanly — so the breakage is an import error at startup, not a
conflict marker.

**Fix.** Re-add `query_documents_hybrid` to `src/crud/document.py`; re-add
`DocumentSearch` plus its export to `src/schemas/api.py`; restore the `RERANKER`
settings block to upstream's config root.

**Proof.** The real 397-line implementation ran against a seeded live database
through the merged app's `AsyncSession`: returned 3 documents with the FTS match
ranked first, and re-ranked correctly per query, confirming live RRF fusion.
Route validation proven through FastAPI: `top_k=1000` → 422 (`le=100`),
missing `observer` → 422, invalid `method` literal → 422, valid body reaching
the hybrid function.

### 3. Alembic becomes multi-head — one re-parent fixes it

**Symptom.** `pi-dev` already carries two heads (`e4eba9cfaa6f` and the fork's
`adb68784e753`, both branching at `f1a2b3c4d5e6`). Upstream extends the chain to
head `b8d2f4a6c9e1` (`document_sources` + the `session_peers` index). Post-merge
there are 28 migrations with heads `adb68784e753` + `b8d2f4a6c9e1`.

**Fix.** Re-parent `adb68784e753` from `f1a2b3c4d5e6` to `b8d2f4a6c9e1`.

**Proof.** After the re-parent: single head `adb68784e753`, zero dangling
parents, linear `alembic history`. Against a disposable `pgvector/pgvector:pg18`
container, `upgrade head` ran all 27 migrations, then `downgrade b8d2f4a6c9e1`
and re-`upgrade head` both succeeded. Schema verified: 13 tables,
`idx_documents_content_tsv` and `idx_documents_content_trgm` GIN indexes, the
HNSW embedding index, and the generated `content_tsv` column.

### 4. `src/telemetry/emitter.py` — CloudEvents import drift

**Symptom.** The file auto-merges cleanly and invisibly keeps the fork's
`cloudevents.v1.[conversion|http]` imports (fork pins cloudevents 2.0.0), while
the merged lockfile resolves upstream's deliberate `cloudevents <2.0` pin
(1.12.1, flat module layout). At runtime every dreamer telemetry emission logs
`No module named 'cloudevents.v1'`.

**Fix.** Switch to upstream's flat imports.

**Proof.** The synthesis run then emitted zero warnings. A classic auto-merge
trap that only runtime testing catches.

### 5. `middleware/ip_filter` returns 500 instead of 403

**Symptom.** The deny path does `raise HTTPException(403)` inside
`dispatch`. Under Starlette's `BaseHTTPMiddleware` lifecycle the exception
escapes and surfaces as a **500**, and can interfere with the exception-handler
pipeline.

**Fix.** `return JSONResponse(status_code=403, …)`.

**Proof.** Behavioral matrix: excluded path `/health` → 200, disallowed IP →
**403 with a clean JSON body**, allowed IP → 200.

---

## Preserved Trial Work

The validated trial merge survived only as dangling git objects after its `/tmp`
worktrees were deleted. It has now been anchored to real refs and can no longer
be lost to garbage collection:

| Ref | Commit | Contents |
|---|---|---|
| `recovered-trial-merge` (branch) | `8d3bc5fc` | **The resolution oracle** — merge with all five repairs applied and runtime-validated |
| `trial/merge-base` | `6b6beb00` | TRIAL MERGE: upstream/main into pi-dev |
| `trial/r6-base-merge` | `f4d140fa` | R6 base merge |
| `trial/r4-lint-cleanup` | `806e9962` | R4 unused-import cleanup |
| `trial/test-db-uri-override` | `597d6ca9` | `HONCHO_TEST_DB_URI` override + TestClient IP handling |
| `trial/cashews-teardown` | `1d7536c0` | cashews `CancelledError` teardown suppression |

`recovered-trial-merge` is used as the **resolution oracle**: each conflicted
file in the real merge is diffed against its counterpart there. Note the trial
is based on `07fbd682` while `pi-dev` is now `0853c83e` (+2 docs-only commits on
the retry-escalation plan), so the resolution ports forward cheaply.

The two stale worktree registrations (`/tmp/honcho-r6`,
`/tmp/honcho-conflict-check`) were pruned only **after** the anchoring.

---

## Phased Plan

### Phase 1 — Preserve and baseline *(complete)*

Anchor the six dangling trial commits to a branch and five tags; verify
reachability via `git fsck`; prune the stale worktrees; record the Gate A
baseline for `../honcho-latest`; verify
`honcho-latest:main == ../honcho:upstream/main`.

### Phase 2 — Write this document *(complete)*

### Phase 3 — Create the merge branch and resolve

- Commit or stash the modified working-tree files so `git merge` will proceed.
- `git checkout -b pi-dev-merge-upstream pi-dev && git merge upstream/main`.
- Resolve all 28 files using the matrix above, diffing file-by-file against
  `recovered-trial-merge`.
- Delete `uv.lock` and regenerate with `uv lock`.
- Commit; confirm `git log -1 --format=%P` shows both parents.

### Phase 4 — Apply the five semantic repairs

Restore `query_documents_hybrid`, `DocumentSearch`, and the `RERANKER` settings
block; adapt and register `SynthesisSpecialist`; re-parent `adb68784e753`; fix
the CloudEvents imports; fix the ip-filter deny path. Then `uv sync`,
`uv run ruff check src/`, `uv run basedpyright`.

### Phase 5 — Validate

**Gate B (structural)** — single `alembic heads`; full
upgrade/downgrade/re-upgrade cycle against a disposable
`pgvector/pgvector:pg18` container with `POSTGRES_HOST_AUTH_METHOD=trust`;
lint and typecheck clean.

**Gate C (behavioral)** — `import src.main` with
`/workspaces/{workspace_id}/documents/search` present in the route table; the
scenario matrix (422s, 403 JSON, synthesis run); the Ollama config path;
`uv run pytest tests/`, `tests/routes`, `tests/integration`, and
`uv run pytest tests/ -k typescript`.

Then tear down the container and re-run **Gate A**.

### Phase 6 — Finalize and hand off

Update this document with the real outcome; present `pi-dev-merge-upstream` for
review. **Do not** fast-forward `pi-dev` or push to `origin/pi-dev` without
explicit approval.

---

## Trial Test Results (baseline expectation)

Measured on the trial-merged tree:

| Suite | Result |
|---|---|
| `tests/` (top level) | 396 passed |
| `tests/routes` | 6 failed / 482 passed |
| `tests/integration` | 1 failed / 75 passed |
| TypeScript SDK | 2 passed |

All 7 failures were classified as **trial artifacts**, not merge defects: the
trial tree paired one parent's test files with the other parent's `src`. For
example `tests/integration/test_token_metrics.py::test_ingestion_tracks_prompt_input_tokens`
calls `estimate_minimal_deriver_prompt_tokens(max_embedding_tokens=2048)` from
one parent while the merged `prompts.py` came from the other. On a real merge
these should not reappear. **Any that persist are genuine and must be fixed.**

---

## Risks

- **`git gc` destroying the trial work.** *Mitigated* — Phase 1 anchored every
  commit to a branch or tag before any prune.
- **Dirty working tree at merge time.** The modified files must be committed or
  stashed first; `git merge` refuses otherwise.
- **Trial base drift.** The trial is based on `07fbd682`; `pi-dev` is now
  `0853c83e`. The two intervening commits are docs-only.
- **Padded vectors.** Existing 1536-dim rows remain under the old client-side
  padding scheme after the dimension becomes configurable. Re-embedding is a
  separate, later decision.
- **Import-order landmine (not a merge defect).** The apparent `agent_tools`
  circular import (`agent_tools → crud → representation → dreamer → specialists
  → agent_tools`) reproduces identically on pristine upstream when `agent_tools`
  is imported first. The real entrypoint `src.main` imports fine on both trees.

---

## Deferred Follow-ups

- Re-embed the existing padded 1536-dim vector rows under the now-configurable
  dimension.
- `src/db.py` defines `engine = create_async_engine(...)` at module scope with
  `SessionLocal` bound to it, and `src/main.py` calls `await engine.dispose()`.
  That singleton is shared across concurrent requests — a cross-request failure
  mode worth revisiting independently of this merge.

---

## Verdict

**Merge.** The upstream delta is large and valuable — five releases of
stability, performance, and capability work, including a `session_peers` index
that takes a hot query from 28.9 ms to 0.15 ms, and a configurable vector
dimension that turns the fork's custom embedding client into two environment
variables. The cost is a day-scale, fully-mapped conflict resolution for which a
runtime-validated oracle already exists at `recovered-trial-merge`.
