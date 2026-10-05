# Upstream patch proposal: SynthesisSpecialist AttributeError

**Found:** 2026-09-29 · **Affects:** self-hosted Honcho v3.0.3 (`~/.local/lib/honcho`)
**Evidence:** 3 dream tasks crashed Sep 28 (runs 7b64ab9d, a6ebf6a8, 1e660827) with
`AttributeError: 'SynthesisSpecialist' object has no attribute 'observed'`.
The 21:24 scheduled dream for `flexitts/dsidlo/dsidlo` produced zero conclusions because of it.

## Root cause

`src/dreamer/synthesis_specialist.py`, `build_user_prompt` (line ~149):

- The **with-hints** branch interpolates only `hints_str` (safe).
- The **no-hints fallback** branch (line ~171) references `{self.observed}`.
- `BaseSpecialist.run` (src/dreamer/specialists.py:117) receives `observed` as a **function argument**
  and passes it to `build_system_prompt` as an argument — it is never stored on `self`.
- The fallback is reachable whenever the orchestrator passes `hints=None`, which happens exactly when
  the deduction and induction phases both return empty final content
  (observed in all 3 failed runs; their phase-completion logs are literal `...`).

Live reproduction (no DB needed):

```python
from src.dreamer.synthesis_specialist import SynthesisSpecialist
s = SynthesisSpecialist()
s.build_user_prompt(None)   # -> AttributeError: 'SynthesisSpecialist' object has no attribute 'observed'
```

`DeductionSpecialist` and `InductionSpecialist` have no such reference in their fallback prompts —
the bug is synthesis-only.

## Minimal fix (Option A, smallest diff)

Pass `observed` through an instance attribute in `BaseSpecialist.run` before building prompts:

```diff
--- a/src/dreamer/specialists.py
+++ b/src/dreamer/specialists.py
@@ class BaseSpecialist
         run_id = parent_run_id or str(uuid.uuid4())[:8]
         task_name = f"dreamer_{self.name}_{run_id}"
         start_time = time.perf_counter()
+        self.observed = observed  # make available to build_user_prompt fallbacks (synthesis)
```

## Alternative fix (Option B, strictly local to synthesis)

Thread `observed` into `build_user_prompt`'s signature:

```diff
--- a/src/dreamer/specialists.py
+++ b/src/dreamer/specialists.py
@@         messages: list[dict[str, str]] = [
             {
                 "role": "user",
-                "content": self.build_user_prompt(hints, current_peer_card),
+                "content": self.build_user_prompt(observed, hints, current_peer_card),
             },
--- a/src/dreamer/synthesis_specialist.py
+++ b/src/dreamer/synthesis_specialist.py
@@     def build_user_prompt(
         self,
+        observed: str,
         hints: list[str] | None,
         peer_card: list[str] | None = None,
     ) -> str:
...
-        return f"""{peer_card_context}
-Explore {self.observed}'s learned patterns and identify synthesis opportunities.
+        return f"""{peer_card_context}
+Explore {observed}'s learned patterns and identify synthesis opportunities.
```

Option B changes the abstract signature in `specialists.py` (BaseSpecialist.build_user_prompt is
abstract there) and the corresponding call site; it is the cleaner long-term shape but touches
3 call/definition sites. Option A is one line and cannot break other specialists.

## Also worth fixing upstream

- `orchestrator.py` treats empty deduction/induction content silently: empty content → `hints=[]` →
  fallback path. A defensive `getattr(self, "observed", observed)` or signature thread-through
  prevents silent crashes on the scheduled-dream path (the one that bit at 21:24).
- The deriver logs phase results as `...` making empties indistinguishable from truncation;
  log `len(content)` alongside.

## Impact when unfixed

- Scheduled dreams (`POST /v3/workspaces/{ws}/schedule_dream`) and threshold-triggered dreams will
  fail silently for any peer-pair whose run has empty phase content — the queue claims the work unit
  and drops it, with only a single orchestrator ERROR line.