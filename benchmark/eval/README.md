# Stage 4 — Offline benchmark evaluation

The only layer in this repository that reads hidden ground truth and reports
accuracy. It is not part of the shipped system, it cannot make a provider
call, and it cannot influence a reconciliation decision.

## The boundary, stated once

| Layer | Reads ground truth? | Reports accuracy? | Can call a model? | Decides? |
|---|---|---|---|---|
| Stage 2 — deterministic core | no (structurally) | **no** | no | yes |
| Stage 3 — investigation agent | no (structurally) | **no** | yes | yes |
| **Stage 4 — this package** | **yes** | **yes** | **no** | **no** |

Three properties make that table true rather than aspirational:

1. **Stage 3 itself has no ground truth.** `tests/test_benchmark_isolation.py`
   walks the AST of every module on the reconciliation path — `normalize`,
   `matchers`, `candidates`, `ledger`, `agent`, `decide`, `evidence`,
   `pipeline.py`, `loader.py`, `stage3.py`, `investigate_cli.py`,
   `reconcile_cli.py` — and fails if any of them so much as names
   `ground_truth` in an import, a string literal or a path expression.

2. **The production controller reports no accuracy.** `reconcile_cli` and
   `investigate_cli` print resolution counts, blockers, steps and models.
   They print no correctness number, because a production controller has no
   answer key. That is a design property, not an omission.

3. **Accuracy exists only here, and only after the fact.** This package is
   outside `src/`, so `pyproject.toml`'s `packages.find where = ["src"]`
   never installs it. The dependency arrow points one way: the evaluator
   imports `finrecon`; nothing under `src/finrecon` imports the evaluator, and
   `tests/test_stage4_evaluator.py` asserts both directions.

**Evaluation can never influence reconciliation.** The evaluator runs after
the fact, over recorded artifacts, and returns numbers to a human. It has no
write path into the ledger's decision columns, no provider, and no way to
feed a score back into the gate that decides whether money moves.

## Offline by construction

The evaluator never constructs OpenRouter, GoRouter, Groq, Gemini or any other
provider. It replays recorded trajectories through the **real** Stage-3
validator and policy with `replay_only=True` and `chain=None`, so the decision
it scores is the decision the controller would have taken — not a
reimplementation that could drift from it.

If a trajectory is missing, the run **fails closed**. It does not fall back to
a live run; there is nothing in this package that could.

Three independent guarantees, each tested:

- **structural** — no module here imports `finrecon.agent.providers.*`
  (asserted by walking the AST of every file in the package);
- **runtime** — `provider_calls_made()` is false and `cache_hits` equals the
  cohort size after every evaluation;
- **belt and braces** — replay driven by a provider that raises on contact is
  never contacted.

Version drift is also fail-closed. A trajectory's cache key covers the prompt,
tool schema, loop, cache format, validator and policy declaration; if a
recorded key no longer matches what the current tree computes, the evaluator
refuses rather than attributing an old contract's behaviour to today's code.

## Usage

```bash
# Score a warmed trajectory cache against DEV ground truth
python -m benchmark.eval evaluate --split dev \
  --trajectories fixtures/trajectories \
  --provider gorouter --model claude-opus-5-thinking

# Pin the exact cohort to a historical run, from several sources
python -m benchmark.eval evaluate --split dev \
  --run-dump run-batch.txt --run-dump run-followup.txt \
  --cohort-from-dump baseline.txt \
  --expected-tier T2 \
  --json-out report.json

# Compare two evaluations over one identical cohort
python -m benchmark.eval compare report-a.json report-b.json
```

`make eval` and `make eval-compare` wrap the common cases.

### Cohort pinning

`--limit` ordering is not a cohort. Two runs that both say "50 cases" can
cover different fifties, and comparing them measures nothing. Pass `--cohort`
(a JSON array or newline-delimited file of case IDs) or `--cohort-from-dump`
(derive the exact set from a baseline run's own transcript).

Before scoring, the evaluator reconciles requested vs found and reports
duplicates, missing, extra, tier counts and contamination. An incomplete exact
cohort aborts the run — scoring the subset that happened to be present would
report a better number than the run earned. `--allow-partial-cohort` and
`--allow-tier-contamination` downgrade those to warnings, deliberately
verbosely.

### `--no-replay`

For a baseline recorded under a **superseded** contract, which today's
validator cannot parse and today's cache key cannot address. It reports
termination reasons, tool-validation rejections and telemetry — all readable
facts of the recording — and **no correctness at all**, because correctness
requires a decision and a decision requires the gate. Those fields are `null`
with a stated reason, never `0`: a zero would read as "no wrong resolutions",
which this mode has no evidence for.

Note that `deterministic_policy_resolved` is a termination reason, not a
resolution count. The gate also runs after a loop that ended
`investigation_complete`, so the termination count is a lower bound on
resolutions and must not be reported as one.

## Metrics

Defined once in DESIGN.md §5.3 and implemented once in `scoring.py`:

```
overall_match_rate        = correct automatic reconciliations
                            ────────────────────────────────────
                            cases with a uniquely resolvable
                            ground truth

auto_resolution_accuracy  = correct auto-resolutions / all auto-resolutions
auto_resolution_coverage  = auto-resolved / total cases
escalation_recall         = correctly escalated / all truly-ambiguous cases
unsafe_auto_match_rate    = incorrect auto-resolutions / total cases
value_at_risk_paise       = value of incorrect auto-resolutions
```

T3 is excluded from the match-rate denominator by construction — it has no
uniquely resolvable answer. The numerator counts **automatic** reconciliations
only: a resolvable case the system escalates lowers match rate, and a human
resolving it later does not repair the number. Rates on an empty denominator
are `null`, never `0.0`.

### Correctness is defined exactly once

`scoring.verdict_for` encodes the predicate already asserted by
`tests/test_stage3_dev_diagnostic.py::test_no_dev_case_is_auto_resolved_incorrectly`,
and that test is left untouched. `tests/test_stage4_evaluator.py` re-derives
the test's predicate inline and asserts the two agree case-for-case across a
full DEV Stage-3 run, so a second, drifting definition cannot appear silently.

### Soundness

Correctness says a resolution was right; soundness says it was reached the
declared way. Every auto-resolution is re-checked for exact-paise
reconciliation, a fragment genuinely present in the bank narration, only
policy-declared relation IDs, a candidate from the deterministic set, and
intact snapshot integrity. Violations are reported individually and in
aggregate.

## Comparison and attribution

`compare` verifies that two evaluations cover identical case IDs and identical
tier composition before emitting any delta, and refuses otherwise.

It then counts how many configuration dimensions differ — provider/model,
prompt, tool schema, loop, validator, policy — and **withholds causal
attribution whenever more than one moved**. The rule is mechanical so it
cannot be argued into a conclusion by whoever writes the summary. An A/B whose
treatment changed three things at once has no identified effect; reporting one
anyway is how a tooling change gets credited with a model's capability.

## DEV vs FROZEN-EVAL

DEV truth is readable without ceremony. FROZEN-EVAL truth is gated behind an
explicit `--allow-frozen-truth`, because the freeze protocol's value
(DESIGN.md §5.1 step 7 — build against DEV, report against FROZEN) rests on
held-out outcomes not being consulted while iterating.

The gate is a speed bump, not security: it makes reading held-out answers a
deliberate act someone can be asked about. It cannot leak frozen truth into a
live investigation, because this package has no provider and never decides
anything.
