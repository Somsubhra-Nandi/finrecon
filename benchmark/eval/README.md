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

### Sliced views

The same verdicts are re-reported five ways, in `metrics_by_tier`,
`metrics_by_archetype`, `metrics_by_family`, `metrics_by_required_composition`
and `metrics_by_candidate_count`. A slice carries counts, the two rates whose
denominators it actually owns (match rate and auto-resolution accuracy) and
value at risk — deliberately narrower than the headline block, because quoting
a six-case slice's escalation recall next to a cohort's invites reading one
denominator as the other.

Two shapes worth knowing before reading one:

- **Families overlap; compositions partition.** A benchmark v4 case carries
  several family tags and exactly one required composition, so the family
  counts do not sum to the cohort size and the composition counts do.
- **A benchmark v1–v3 cohort reports `{}` for families and compositions, not
  zeros.** Those generations have no families. An absent key would read as "not
  measured"; a zero would read as "measured, none found". Empty is the only
  honest rendering, and `tests/test_v4_stage4_integration.py` pins it.

`metrics_by_candidate_count` reports an `unknown` bucket for splits whose
ground truth records no candidate count, which is every split before v4.

### Conjunctive provenance

`validator.v2` resolves a case when one candidate is the only one consistent
with every informative claim in the narration's deterministic closure, so a
resolution can rest on several clues none of which is conclusive alone. The
report describes that shape rather than leaving a reader to infer it, in a
`conjunction` block: how many resolutions needed composition and how many did
not, the distribution of informative claims and disjoint narration spans per
case, the final intersection size, and the reference-evidence state of every
case (identified / ambiguous / contradictory / no informative evidence / no
admissible agent evidence / closure incomplete).

`agent_atom_coverage` is the one to read for the C-vs-D question. Since v2 the
decision does not depend on which claims the agent surfaced, so how many it
surfaced is free to measure — and it is the honest form of "did the
investigation earn its tokens?", now that it cannot be confused with "did the
investigation decide?".

`accepted_relations_for` reports one row per clue a resolution rested on, each
carrying the fragment, its narration offsets and span, the atom identity, the
declared relation, the reference it matched and how many candidates that clue
reached. Reading the agent's own findings instead would make a conjunctive
resolution look unevidenced — none of its clues discriminates alone, which is
the point of it.

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

`v4-pilot` sits beside `dev` on the ungated side, for the same reason `dev` is
there: it is a development artifact with no held-out status to protect. A
frozen v4, if one is ever cut, would be a different split name on the gated
side.

## The deterministic baselines are a different package

`benchmark/baselines/` answers a different question — *how much of a benchmark
needs no model at all?* — and it has to decide things to answer it, which this
package must never do. So it lives separately, and the separation is enforced
the same way this one is: nothing there imports a provider, `arms.py` and
`features.py` cannot name ground truth, and truth is loaded only in
`report.py`, strictly after every arm has returned. See
`benchmark/V4-PILOT.md` §5 for what the arms are and what they measured.
