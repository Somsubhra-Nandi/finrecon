# bounded-search-v1 — synthetic bounded-search challenge

**Status: READY FOR REVIEW, NOT FROZEN. No hosted-model result has been
observed.** Freeze the exact hash in this document before making a paid run.
This split is additive: it does not replace `v4-pilot` and does not modify the
frozen v3 benchmark.

## Purpose

Answer one narrow question:

> Under the same immutable `CaseSnapshot`, candidate set, evidence tools,
> deterministic tool outputs, validator, policy and tool-call budget, does a
> hosted investigator find decisive evidence more often than a strong fixed
> mechanical investigator?

The benchmark is synthetic and contains no customer data. It is not a claim
about production accuracy.

## System inspection that determined the design

Stage 2 first runs exact direct-key and exact derived reconciliation. For every
remaining case, `candidate_generator.v1` emits the complete plausible set and
`CaseSnapshot` freezes it with normalized source facts and a content hash.
Candidate order is deterministic and has no score or model ranking.

Stage 3 currently records `investigator.v4`, `tools.v3`, `agent-loop.v2`,
`trajectory-cache.v3`, `validator.v3` and `policy.v1`. The production loop's
default is eight model steps and at most eight admitted tool calls per model
turn. It has no separate total-call setting; the loose theoretical ceiling is
therefore 64 executed calls. This challenge does not change the production
semantics. It supplies the same explicit `LoopConfig(max_steps=4,
max_tool_calls_per_step=1)` to both arms.

The four read-only tools are:

| Tool | Selective action | Fan-out |
|---|---|---|
| `lookup_candidate_records(candidate_id)` | choose one candidate | returns that candidate's settlement/payment/refund records |
| `inspect_settlement_breakup(settlement_id)` | choose one settlement | returns its exact signed break-up lines and provenance |
| `compute_expected_net(candidate_id)` | choose one candidate | deterministically recomputes exact net and residual |
| `compare_reference_fragment(fragment)` | choose one literal narration fragment | compares that one fragment against every immutable candidate in one call |

`validator.v3` mechanically verifies exact/prefix/suffix/contains, masking,
separator and character-multiset reference relations; exact-paise candidate
financials; sound break-up references; source-backed refund-line amounts; and
complete conjunctive reference/structural closure. `policy.v1` then applies the
hard resolution and escalation rules. Model prose and confidence are never
decision inputs.

This explains why `mechanical-investigator-v1` does so well on v3 T2. A v3 T2
case normally has two candidates and one degraded reference in a short
narration. The investigator can enumerate narration fragments and repeatedly
invoke the comparison tool; each invocation checks all candidates, while the
validator then enumerates the complete reference closure. Amount/date/break-up
facts are already uniform across the Stage-2 candidate set. There is little
left to select strategically.

The operations that actually require selection are which candidate to inspect,
which candidate net to recompute, which settlement break-up to inspect, and—
most importantly—which literal narration fragment to spend a comparison on.
The comparison itself is not candidate-selective. A fair challenge therefore
has to make useful fragment/candidate selection scarce without withholding any
evidence from either arm.

The prior hosted Opus evidence is not a solution to this question. A historical
50-case DEV T2 cohort requested `claude-opus-5-thinking`, was reported as
`claude-opus-5`, and resolved 50/50 in 56 steps, but tools, prompt and model all
changed relative to its Ox Alpha comparator. The committed notes explicitly
withhold a causal claim, and the raw hosted transcripts are not committed as
fixtures. This new split starts from a separately hashed cohort.

## Cohort

Seed: `730241`. Total: 50 cases, 599 visible records.

| Family | Cases | Intended outcome | Construction emphasis |
|---|---:|---|---|
| `reference_prioritization` | 7 | resolve | a decisive degraded reference among plausible remittance fields |
| `noisy_reference_selection` | 7 | resolve | paired/wide reference clues among many reference-shaped decoys |
| `multi_evidence_composition` | 7 | resolve | two or three independently valid reference reaches whose intersection is unique |
| `refund_linked_reasoning` | 6 | resolve | reference reach composed with a source-backed refund break-up amount |
| `conflicting_evidence` | 6 | resolve | tempting shared clues separated only by the complete evidence set |
| `decoy_heavy_candidate_search` | 7 | resolve | three to five candidates and a dense choice of plausible actions |
| `ambiguity_controls` | 10 | escalate | no discriminator, or an incomplete conjunction that leaves multiple survivors |

Candidate counts: 32 cases with three, nine with four, nine with five.
Every candidate in a case has the same Stage-2 total and settlement date facts;
there is no unique-total or unique-date shortcut. The visible narration plus
candidate-specific lookups present 30–42 plausible evidence actions per case
(mean 34.52).

## Budget and fairness

One tool call is one successfully executed registered tool invocation. A
`compare_reference_fragment` call counts once even though it compares that
fragment with every candidate; this fan-out is documented because it makes
the challenge easier, and it is available identically to both arms.

The budget is exactly four executed calls per case, implemented as four model
steps with one call admitted per step. A fourth call can end in automatic
resolution after validation; otherwise the controller terminates with
`step_budget_exhausted`. There are at least 30 plausible actions, so exhaustive
enumeration cannot fit. Both arms receive the same snapshot briefing, tool
schemas, outputs and controller. Hidden truth is opened only by the evaluator
after all production decisions are final.

The mechanical arm is a credible fixed strategy, not a deliberately weak one.
It tokenizes visible narration, scores ordinary bank contexts (`UTR`, `REF`,
`TRACE`, `RRN`, clearing/origin/beneficiary labels), favors reference-shaped
alpha-numeric fragments, spends one comparison per turn, and stops after a
valid reference reaches the validator's already-complete closure. It has no
family vocabulary, canonical references or ground truth.

## Construction oracle

The evaluation-only oracle may enumerate the complete admissible reference
closure and seed the real validator/policy outside the production budget. Its
decisions are complete before truth is loaded.

| Result | Count |
|---|---:|
| Intended resolvable accepted correctly | 40 / 40 |
| Intended ambiguous escalated | 10 / 10 |
| Wrong automatic resolutions | 0 |
| Reference fragments audited | 1,028,123 |
| Seed comparisons required | 45 |
| Provider/network calls | 0 |

The seed count is 45 rather than 50 because five no-discriminator ambiguity
controls have no admissible reference atom to submit.

## Mechanical baseline

| Metric | Result |
|---|---:|
| Correct automatic resolutions | 20 / 40 resolvable |
| Wrong automatic resolutions | 0 |
| Escalations | 30 |
| Correctly escalated ambiguity controls | 10 / 10 |
| Precision | 1.000 |
| Resolution rate (all cases) | 0.400 |
| Executed tool calls | 151 / 200 maximum |
| Mean / median calls | 3.02 / 4 |
| Budget-exhausted cases | 30 (60%) |
| Value at risk | 0 paise |

| Family | Cases | Correct | Wrong | Escalated | Calls | Exhausted |
|---|---:|---:|---:|---:|---:|---:|
| ambiguity controls | 10 | 0 | 0 | 10 | 40 | 10 |
| conflicting evidence | 6 | 3 | 0 | 3 | 21 | 3 |
| decoy-heavy candidate search | 7 | 1 | 0 | 6 | 25 | 6 |
| multi-evidence composition | 7 | 7 | 0 | 0 | 10 | 0 |
| noisy-reference selection | 7 | 1 | 0 | 6 | 26 | 6 |
| reference prioritization | 7 | 7 | 0 | 0 | 7 | 0 |
| refund-linked reasoning | 6 | 1 | 0 | 5 | 22 | 5 |

This is materially below 40 and inside the predeclared 15–25 design target,
with no unsafe automatic resolution. It passes the “worth freezing before a
hosted run” construction gate; it does not predict that Opus will win.

## Leakage and reproducibility

- No `VALDT`, `TRUTH=`, `WINNER=`, `EXPECTED=`, outcome, family or composition
  label appears in visible records.
- Truth occurs in at least four positions of the production-sorted candidate
  tuple; the most common truth position covers 16/40 resolvable cases.
- Family order is seeded and shuffled; longest family run in case-ID order is
  two. Outcome order is rejected and redrawn if a run exceeds six.
- Every source file is independently seed-shuffled and is not ID-sorted.
- Irrelevant evidence placement is seed-shuffled inside each narration.
- All candidate totals and date tuples are identical within a case.
- Independent regeneration is byte-identical across all seven hashed files.
- Frozen v3 remains `f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b`.

Pre-freeze benchmark fingerprint:

```text
e2142a61275a681971cc6d14a02d9c3a8439cb797972a32e072518a09ebb9958
```

Do not mutate the seven hashed files after approval. Record the freeze before
observing any hosted trajectory.

## Later hosted run (do not run before freeze approval)

PowerShell, all 50 cases:

```powershell
$env:FINRECON_PROVIDER_ORDER='gorouter'
$env:GOROUTER_MODEL='claude-opus-5-thinking'
uv run python -m finrecon.investigate_cli --split bounded-search-v1 --fixtures fixtures/trajectories/bounded-search-v1-opus --max-steps 4 --max-tool-calls-per-step 1
uv run python -m benchmark.eval evaluate --split bounded-search-v1 --trajectories fixtures/trajectories/bounded-search-v1-opus --cohort benchmark/cohorts/bounded-search-v1.json --expected-tier SEARCH --provider gorouter --model claude-opus-5-thinking --max-steps 4 --max-tool-calls-per-step 1 --label bounded-search-v1-opus --json-out benchmark/reports/bounded-search-v1-opus.json
uv run python -m benchmark.eval compare benchmark/reports/bounded-search-v1-mechanical.json benchmark/reports/bounded-search-v1-opus.json --label-a mechanical --label-b opus --json-out benchmark/reports/bounded-search-v1-comparison.json
```

This is 50 case investigations and at most 200 hosted completions (one per
model step). A planning estimate is 125–175 completions; the hard ceiling is
200. Deterministic tools, validator and policy make no provider calls.

## Known weaknesses

1. `compare_reference_fragment` fans one fragment across every candidate, so
   the benchmark cannot test candidate-by-candidate comparison efficiency.
2. Once any admissible reference seed is found, `validator.v3` computes the
   complete deterministic closure. Consequently the composition families
   test selecting a useful entry point, not whether the model itself composes
   every fact.
3. Field labels such as `ORIGIN-REF` are realistic hints but may make some
   families straightforward for both a model and a bank-context heuristic;
   the baseline's 20 resolutions quantify that fact.
4. The source relationships are operationally plausible but synthetic. The
   result must not be generalized to arbitrary production narrations.
5. Fifty cases give useful paired evidence but wide family-level confidence
   intervals. Report counts and exact paired outcomes, not only percentages.

