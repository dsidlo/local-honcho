# RRF vs vector-only conclusion search

Run on 2026-10-11 against the live Honcho at `localhost:8300`. The reranker was off (`RERANKER_ENABLED` is unset, so it stays at its default). These are applicability judgments of the returned notes, not a labeled nDCG run.

## What was compared

Both calls used one precomputed query embedding, the same `(observer, observed)` pair, and `top_k` 5.

| Search | Function | What it ranks |
|---|---|---|
| Vector-only | `query_documents` | Cosine distance on the conclusion embedding. Dialectic `search_memory` uses this unless the chat request sets `conclusion_search` to `rrf`. |
| RRF | `query_documents_hybrid(..., method="rrf")` | Vector, English full text (`ts_rank_cd`), and trigram similarity, fused as `1 / (60 + rank)`. `honcho_conclusions` calls this through `documents/search`. |

RRF pulls `top_k * 2` candidates from each list before fusing. The lexical ranker is PostgreSQL cover-density ranking. The code comment calls it BM25-like. It is not Okapi BM25.

The live `conclusions/query` route on this install also calls `query_documents_hybrid` with `method="rrf"`. It is not a vector-only control. The vector side of these tests called `query_documents` directly.

## Collection 1 — Grok, 2026-10-11

Workspace `local-honcho`, pair `(agent-grok, agent-grok)`. A few hundred conclusions from the Grok tailer session. After the first call, vector was about 10 ms and RRF about 30 ms. The first vector call was 74 ms.

| Prompt | Shared of 5 | Judgment |
|---|---|---|
| `jounral ofset for chat histroy tailer` | 5, same order | Both fit. All five notes are about the tailer, the journal, or the offset. The typos did not move the list. |
| `grok-honcho-tailer.service` | 4 | Both fit. Ranks 1 and 2 name the unit either way. RRF replaced a log-capture note with a note about copying `tailer.py`, which is nearby and less about the service. |
| `observe_others agent-grok` | 1 | RRF fits. Four of its five hits say `observe_others` stays off, so the agent does not observe `dsidlo`. Vector drifted to `observe_me` and to observer/observed pair talk. Its fifth hit is about Grok's own memory and does not answer the prompt. |
| `background job that copies the assistant private reasoning into long-term memory` | 5, same order | Neither fits. All five are about whether to turn Grok memory on. The tailer that reads `summary_text` is absent. |
| `what was decided about storing tool arguments` | 5, same order | Neither fits. The hits are about `find` flags and about the `honcho_ask` / `honcho_conclusions` defaults, not about leaving tool arguments out of capture. |

A second wording of the last two prompts still missed those decisions. "summary_text thoughts are read from the journal by the tailer" put the scratch-journal note first for both methods, then two `read_file` notes, because "read" pulled them in.

## Collection 2 — LightRAG, pi-mono, 2026-05-25 to 2026-05-26

Older pi-mono history, not the Grok session. Workspace `lightrag`, pair `(agent-pi-mono, agent-pi-mono)`, 2,817 conclusions. Sampled notes before choosing prompts. The notes that are about the work are the SAGE paper ingestion, the `llm_model_func` callback, and the `dgs-pi` branch.

FlexiTTS and the large OpenClaw collections are older too. Their conclusions wander off the project, so they were not used. Strata is a jcode collection from 2026-09-29 to 2026-10-05 and was left for the same reason: LightRAG is one subject with facts a reader can check.

Vector stayed near 25 ms after the first call. RRF was 60–79 ms on the short prompts and 246 ms and 389 ms on the two longer ones. The first vector call was 335 ms.

| Prompt | Shared of 5 | Judgment |
|---|---|---|
| `secnd papre failed mid-extracion` | 4 | Both fit. Rank 1 for both is the second paper failing mid-extraction. RRF's extra hit stays on that incident (extraction failed mid-process). Vector's extra hit only says both papers failed. |
| `llm_model_func` | 0 | RRF fits. All five hits name `llm_model_func`: the edit, the binding, the search, and the need to call a real model. Vector returned model names (`qwen`, `gemma`, `phi4`) and an `LLM_MODEL` setting. |
| `dgs-pi` | 4 | Both find the branch. RRF ranks "merged main into `dgs-pi`" second and adds "created branch `dgs-pi`." Vector spends ranks 2 and 3 on notes that only say "has -pi" and "has pi," and rank 5 is an unrelated bash call. RRF still keeps those fragment notes, lower. |
| `the ingestion pipeline died halfway through the second research paper` | 5, same order | Both fit. Rank 1 is the mid-extraction failure. The next three are the same SAGE ingestion. The fifth is chunk 9 of the first paper, the same job and not the failure. |
| `what was decided about making the language model callback async` | 5, same order | Both fit. The hits say there is no upstream check that `llm_model_func` is async, that it should be `async def`, and that an async-LLM fix was applied. |

## Conclusions

RRF is the better default for conclusion search. Vector-only is enough when a conclusion is already worded like the question, and it is the wrong tool when the question names a rare symbol.

On both collections the lists were identical, or one slot apart, whenever the stored note used the same words as the question. Vector search already had the right notes at the top, so full text and trigram did not change the answer. That covered the misspelled tailer prompt, the LightRAG paper failure, and the async-callback decision.

The two prompts that named a rare token went the other way. `observe_others` sent vector search to `observe_me`. `llm_model_func` sent it to a list of model names, and the two result lists shared nothing. Full text kept the literal. That is the difference that changes what a caller would answer.

Trigram did not rescue typos. The stored notes were spelled correctly, and the embedding of the misspelled query was already close enough to land in the right cluster. On `dgs-pi`, both methods also kept junk notes that only say "has pi." RRF ranked the real branch facts higher and did not drop the fragments.

A vague prompt fails in both. The Grok prompts about copying private reasoning, and about storing tool arguments, returned nearby notes about enabling memory and about tool defaults. The LightRAG paraphrase succeeded for both only because a conclusion already said the second paper failed mid-extraction.

`honcho_conclusions` already uses RRF. `honcho_ask` now sends `conclusion_search: rrf`, and this checkout's dialectic uses that for both the prefetch and the `search_memory` tool. Callers that omit the field stay on vector search. The API process currently serving port 8300 already fuses every dialectic `search_memory` with RRF and ignores the new field.
