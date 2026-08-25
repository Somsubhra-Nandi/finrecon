# Stage 3 — findings

Recorded, not acted on. The Stage-1 generator, seeds, taxonomy, case
distribution, datasets and frozen hash were all frozen before Stage 3 began
and none of them was touched. Neither were the Stage-2 matchers.

Frozen FROZEN-EVAL SHA-256, verified before and after this stage:

```
f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b
```

---

## 1. DEV T2 needs no model at all

**Severity: material to the Stage-3/Stage-5 narrative. Not a bug.**

### What was observed

Under the validator's declared reference relations, an exhaustive
enumeration of every contiguous narration substring of length ≥ 4 — with no
model anywhere in the loop — reaches **200 of 200** DEV T2 cases, identifies
the correct settlement in every one, and misidentifies none.

| Strategy | DEV T2 uniquely + correctly identified | Wrong |
|---|---:|---:|
| One substring search of the whole narration | 33 / 200 | 0 |
| The mechanical test stand-in (6 fragments, bounded loop) | 171 / 200 | 0 |
| Exhaustive substring enumeration (unbounded, model-free) | **200 / 200** | 0 |

The first row is the `embedded_in_narration` degradation category: those
narrations contain the UTR literally, glued into a longer token, so a single
containment test finds them. The third row is the upper bound of what any
purely lexical strategy can extract.

Both rows are pinned as tests in `tests/test_stage3_dev_diagnostic.py` so
the finding cannot quietly stop being true.

#### The T3 half, measured later

The rows above say what the model-free arm *recovers*. They do not say what
it does when there is nothing to recover, and that is the half that decides
whether the arm is a usable baseline or merely a lucky one. Re-running both
strategies across all 240 DEV cases Stage 2 leaves unresolved, through the
unmodified validator and policy gate:

| Strategy | T2 (200) | T3 (40) | Unsafe auto-resolutions |
|---|---|---|---:|
| Separator-token split, length ≥ 4 | 171 correct, 29 escalate | 40 escalate | **0** |
| Exhaustive substring enumeration | 200 correct, 0 escalate | 40 escalate | **0** |

T3 escalates under brute force for a structural reason rather than a lucky
one: a T3 narration carries no fragment standing in a declared relation to
either candidate, so no fragment is discriminating and `no_reference_link`
fires. It is the *same* predicate that protects the model arm. Enumerating
harder cannot manufacture a link that is not there.

This makes finding 1 stronger, not weaker, and it cuts against the model
arm: on this benchmark the deterministic arm is not merely competitive, it
saturates T2 at zero risk. Any Arm-C-versus-Arm-D number reported from
benchmark v3 has to be read with that ceiling stated next to it.

Two things this is **not** licence to do. It is not licence to weaken the
validator so brute force fails — finding 1 already rules that out, and it
would trade correctly reconciled money for a better-looking ablation. And it
is not licence to make exhaustive enumeration the production Stage-3
behaviour: the model selects the fragment, and the controller fans that
selected fragment across the candidate set (finding 10). The distinction is
what keeps the later ablation meaningful, because an arm that enumerates
every substring is measuring the search space, not the investigation.

A T2 construction the search space cannot exhaust is a benchmark v4
question, not a Stage-3 one.

### Why it happens

The search space is tiny. A T2 narration is ~30–45 characters, giving a few
hundred substrings; there are exactly two candidates; each relation is a
string comparison. Brute force over that space costs microseconds. The
degraded reference is genuinely *necessary* — benchmark v2 fixed that, and
structured evidence alone still resolves nothing (Stage 2 leaves 200/200
unresolved) — but "necessary" turned out not to imply "hard to recover".

There is a second, sharper reason, and it is a methodological limitation
rather than an implementation detail. The validator's relation set
(`src/finrecon/evidence/reference.py`) and the generator's degradation
ladder (`benchmark/generator/utr_degradation.py`) are **two statements of
the same taxonomy, authored by the same person**. The relations were
re-derived from DESIGN.md §5.2's public vocabulary rather than imported —
that part is real, and the isolation tests enforce it — but re-deriving from
the same vocabulary is not independence. Every degradation the generator can
apply has a relation that inverts it, because both lists came from one head.
A real bank narration carries degradations outside that ladder, and the
relations would miss them; they would miss them *safely*, escalating rather
than guessing, but the coverage number would fall.

### What this implies

Ablation arm B (a good-faith lexical/regex baseline, DESIGN.md §5.5) will
score very high on T2, and the **C-vs-D delta — "the most interesting number
in the project" — again has little room to move**. This is finding 1 of
`STAGE2-FINDINGS.md` recurring in a new form: there, structured evidence made
T2 trivially resolvable; here, the reference-recovery step itself is
brute-forceable.

Options, none of which are Stage-3 decisions:

- **Report it.** DESIGN.md §5.5 already commits to this posture: *"If D beats
  C by two points at 2.5× the cost, report exactly that"*, including the
  conclusion that the simpler architecture is the one worth shipping. A
  measured result that deterministic-first architecture reaches this
  benchmark's degraded references is a real, defensible finding about
  reconciliation systems — arguably a more useful one than a narrow agent win.
- Widen the T2 construct in a future generator version so the search space is
  not exhaustible (longer narrations, many same-amount candidates, multiple
  reference-shaped decoys in the text). That is a Stage-1 change requiring a
  manifest bump, a CHANGELOG entry and a new hash, and it is out of scope here.
- Evaluate the agent on the sub-population where the lexical strategy
  abstains, and say plainly that frozen T2 does not isolate model-exclusive
  recovery.

**Recommendation: report it.** Do not weaken the validator's relation set to
leave the model something to do. That would trade correctly reconciled money
for a better-looking ablation, which DESIGN.md §1 rules out in its first
paragraph.

---

## 2. Refusal holds under maximal adversarial pressure

**Severity: none. A positive result, recorded because it is the claim the
architecture exists to support.**

The same exhaustive enumeration that solves every T2 case was pointed at the
40 DEV T3 cases, testing every narration substring against every candidate
and both reference kinds:

```
DEV T3 under exhaustive adversarial enumeration:  40 refused, 0 identified
```

No fragment anywhere in any T3 narration stands in a declared relation to
exactly one candidate. An agent with an unbounded step budget and a
deliberate intent to manufacture a link cannot produce one, because the
evidence is not there and the relations do not invent.

This is a stronger statement than "the test stand-in refused 40/40", which
could merely mean it did not try hard enough. Pinned as a test in
`tests/test_stage3_dev_diagnostic.py::TestAdversarialRefusal`.

---

## 3. A single malformed tool call ends the case

**Severity: minor. A deliberate design choice with a stated coverage cost.**

DESIGN.md §4.3 lists "schema validation failure" as a hard blocker, and §11's
risk register says the mitigation is a "Pydantic validation → escalate path,
not retry-forever". Implemented literally: the first refused tool call —
unknown tool, unparsable arguments, a field that fails the schema, an
identifier outside the snapshot — stops the loop immediately and escalates
the case, even if the evidence gathered before it was conclusive.

The cost is real. A model that types one malformed argument on step 6 loses a
case it had already solved on step 5. The alternative — let it retry, keep
the evidence — is worse for a reason worth stating: a retry loop against a
schema is a model being *coached* toward a call that executes, and the
difference between "eventually produced a valid call" and "understood the
case" is exactly what a bounded, auditable investigation is supposed to keep
visible. It is also not free: retries spend the budget that bounds the run.

Observed cost on DEV with the deterministic stand-in: **zero cases**, because
it cannot emit a malformed call. Under a real model this will be non-zero,
and the trajectory records every instance, so the rate is measurable rather
than assumed.

Updated by finding 10: the execution rule is unchanged, but the *record* of
it was incomplete. Calls that passed preflight and lost their batch to a
sibling failure used to leave no trajectory entry at all; they now carry an
explicit `skipped_due_to_batch_rejection` status. What the rule costs is
therefore now visible in the audit trail rather than only inferable from the
gap between requested and recorded calls.

---

## 4. A fragment that reaches every candidate must not veto one that separates them

**Severity: none. A design decision found during implementation, recorded
because the naive rule is wrong in a way that is not obvious.**

The first formulation of the uniqueness predicate was: *the union of
candidates reachable from any admissible fragment must be exactly one*.

That is wrong, and canonical settlement IDs are what expose it. They share a
prefix — `setl_dev_000023`, `setl_dev_000024` — so a lazy fragment like
`"SETL"` stands in a declared prefix relation to **every** candidate at once,
pinning four characters. Under the union rule, one such probe would poison a
case where another fragment had cleanly separated the candidates, and a model
would be punished for looking around.

The corrected rule evaluates each fragment independently and counts only the
ones that *discriminate* — reach exactly one candidate. A fragment reaching
zero or several is recorded and set aside: it carries no information about
which candidate is right, so it is neither evidence for nor against.

What is **not** set aside is disagreement. Two discriminating fragments
pointing at different candidates is a contradiction, and the case escalates.
There is no majority vote, no tie-break, and no preference for the relation
that pins more characters.

---

## 5. Rerunning Stage 3 was not idempotent until claims were tracked per case

**Severity: bug, found by a test, fixed.**

DESIGN.md §4.3 makes "counterparty already resolved in this run" a hard
blocker, and the first implementation read the ledger's flat set of claimed
settlements. On a second run over the same batch, every case that had
resolved on the first run found its own settlement already claimed — by
itself — and escalated. The batch changed its answers on a rerun, which is
precisely what the Stage-2 idempotency invariant forbids.

Fixed by tracking claims *per case*
(`LedgerStore.settlement_claims`): a settlement is contended only when a
**different** case claims it. Contention among Stage-3 resolutions in the
same pass is still settled by retracting both, matching
`derived_reconciliation.withdraw_contended` and keeping the pass
order-independent. Covered by
`tests/test_stage3_pipeline.py::TestIdempotency`.

---

## 6. A widened Stage-2 candidate can never auto-resolve in Stage 3

**Severity: none. A deliberate consequence, stated so it is not mistaken for
an oversight.**

When no settlement group totals the credit exactly, Stage 2's candidate
generator falls back to `date_window_only` blocking and emits every in-window
settlement so the case is not left with an empty file. The Stage-3 policy
requires `exact_total_in_window` provenance, so such a candidate can never
auto-resolve, regardless of how strong its reference evidence is.

That is correct: recovering a reference does not account for money nobody has
accounted for, and DESIGN.md §4.3 admits no unexplained paise. It is a
declared, configurable policy
(`EvidencePolicy.require_exact_total_blocking_rule`), not a hardcode, and a
test asserts both behaviours. On DEV the situation does not arise — every
unresolved case carries exact-total candidates — so this costs nothing here
and could cost coverage on data that behaves differently.

---

## 7. No live model run has been performed

**Severity: material to what Stage 3 can claim. Not a defect.**

No provider credential (`OPENROUTER_API_KEY`, `GROQ_API_KEY`,
`GEMINI_API_KEY`) was present in the build environment, in any scope. Stage 3
is therefore verified end to end against deterministic fake providers, and:

- the committed trajectory corpus (`fixtures/trajectories/`) is **empty**;
- no number anywhere in this repository describes model behaviour;
- the provider adapters' wire translation is tested against recorded request
  and response bodies, not against a live endpoint. A live run may still
  surface an endpoint or model-ID mismatch, and the default model IDs in
  particular should be checked against each provider's current catalogue
  before the first live call — provider catalogues change faster than this
  repository does.

What *is* established without a model: the loop's bounds and terminations,
the tools' read-only behaviour and access control, the trajectory record, the
cache key and zero-call replay, the validator's predicates, the policy gate's
blockers and value ladder, ledger integration and idempotency, and the
fallback classification for every provider failure mode.

The live gap is the one thing standing between this stage and a complete
day-8 gate, and it is a credential away, not a rewrite away.

---

## 8. BASELINE — later live smoke tests and five-case diagnostic

**Status: later observation. Section 7 remains the historical state when the
original Stage-3 findings were recorded; it is no longer the current live-run
state.**

Subsequent live calls used OpenRouter model `stealth/ox-alpha`, with Groq and
Gemini configured only as infrastructure fallbacks. Neither successful smoke
test needed a fallback.

- A genuinely ambiguous DEV-style case was investigated without a fabricated
  reference and safely escalated.
- A degraded-reference case with narration fragment `PF*******VQ` produced a
  mask-consistent relation to exactly one candidate, and the unchanged
  validator/policy safely resolved it.
- Strict duplicate-key decoding was exercised live: an ambiguous tool argument
  object with two `candidate_id` keys was rejected before execution and safely
  escalated without fallback.

The baseline five-case DEV T2 diagnostic used:

```
case:bnk_dev_000003
case:bnk_dev_000005
case:bnk_dev_000011
case:bnk_dev_000018
case:bnk_dev_000026
```

Aggregate baseline result:

| Measure | Baseline |
|---|---:|
| investigated | 5 |
| resolved | 1 |
| escalated | 4 |
| `investigation_complete` | 1 |
| `step_budget_exhausted` | 3 |
| `tool_validation_failed` | 1 |
| fallbacks | 0 |
| total tokens | approximately 91,153 |
| mean tokens / case | approximately 18,231 |
| mean model steps | 6 |
| maximum model steps | 8 |
| unsafe automatic matches observed | 0 |

The three budget failures were orchestration waste rather than an absence of
decisive evidence: the model had already found a fragment that mechanically
separated the complete candidate set, then continued requesting arithmetic,
record lookup, break-up, and additional fragment calls until the eight-step
budget expired. The loop also executed only the first call from multi-call
assistant turns, forcing later model turns to request discarded calls again.

Per-case baseline step/token/latency rows were not retained in this repository;
only the aggregate diagnostic above and the case IDs were available when the
optimization branch began.

---

## 9. OPTIMIZED EXPERIMENT — bounded tool batches and deterministic early stop

**Branch: `exp/stage3-orchestration-opt`. Experimental; not a replacement for
the baseline.**

The optimization changes orchestration only:

1. One assistant turn may request up to eight independent read-only calls.
   Every call is first decoded with duplicate-key rejection, Pydantic-validated,
   and authorized against the immutable snapshot. If any call fails, the whole
   batch executes no handler and terminates with the existing safe semantic
   failure path. A valid batch executes serially in response order.
2. After a complete successful tool batch, the loop passes accumulated raw
   outputs and the complete immutable snapshot through the existing validator
   and policy. If that unchanged layer already resolves, the loop terminates as
   `deterministic_policy_resolved` without another model turn. Model prose and
   confidence are not inputs.
3. The prompt now identifies the displayed Stage-2 totals, deltas, and blocking
   rules as trusted facts already held by the validator, discouraging routine
   re-proof of exact totals while keeping all candidates and all tools visible.
4. Trajectories now retain the per-turn tool bound, validator/policy identities
   and declaration, end-to-end latency, aggregate token helpers, provider-call
   count, requested-call count, executed-call count, and explicit early-stop
   status. Cache keys cover every new control-flow input.

Safety consequences are intentionally one-way:

- no validator relation, financial predicate, value threshold, or blocker was
  weakened;
- no candidate can be added, removed, reordered, ranked, or scored;
- malformed mixed batches execute nothing rather than partially succeeding;
- semantic failures still do not trigger provider fallback;
- the model still cannot resolve money, and its prose still cannot trigger the
  early stop.

Local verification on 2026-08-23:

| Check | Result |
|---|---:|
| focused Stage-3 suite | 342 passed |
| complete suite | 1,151 passed |
| frozen benchmark | SHA-256 matches v3 manifest |
| frozen SHA-256 | `f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b` |

The fresh optimized five-case live diagnostic was subsequently run outside the
Desktop workspace, where the provider credentials were available. It used the
same five DEV T2 IDs as the baseline, a fresh trajectory directory, and no
cache replays. OpenRouter model `stealth/ox-alpha` served all five calls; no
fallback provider was used.

Aggregate baseline-to-optimized comparison:

| Measure | Baseline | Optimized | Change |
|---|---:|---:|---:|
| investigated | 5 | 5 | 0 |
| resolved | 1 | 3 | +2 |
| escalated | 4 | 2 | -2 |
| `investigation_complete` | 1 | 0 | -1 |
| `step_budget_exhausted` | 3 | 0 | -3 |
| `deterministic_policy_resolved` | 0 | 3 | +3 |
| `tool_validation_failed` | 1 | 2 | +1 |
| mean model steps | 6.00 | 1.00 | -83.3% |
| maximum model steps | 8 | 1 | -87.5% |
| total tokens | 91,153 | 10,444 | -80,709 (-88.5%) |
| mean tokens / case | 18,231 | 2,089 | -16,142 (-88.5%) |
| total investigation latency | not retained | 93,589 ms | not comparable |
| fallbacks | 0 | 0 | 0 |
| unsafe automatic matches observed | 0 | 0 | 0 |

Optimized per-case trajectories:

| Case | Outcome / termination | Model steps | Calls requested / executed / refused | Tokens | Total latency | Decisive evidence |
|---|---|---:|---:|---:|---:|---|
| `case:bnk_dev_000003` | resolved / `deterministic_policy_resolved` | 1 | 4 / 4 / 0 | 2,043 | 17,220 ms | `PF*******VQ` was mask-consistent only with `setl_dev_000005` (4 pinned characters) |
| `case:bnk_dev_000005` | resolved / `deterministic_policy_resolved` | 1 | 4 / 4 / 0 | 1,979 | 15,447 ms | `SK************1R` was mask-consistent only with `setl_dev_000007` (4 pinned characters) |
| `case:bnk_dev_000011` | resolved / `deterministic_policy_resolved` | 1 | 6 / 6 / 0 | 2,516 | 32,994 ms | `8MR7YNFHN` was a prefix only of `setl_dev_000015`'s UTR (9 pinned characters) |
| `case:bnk_dev_000018` | escalated / `tool_validation_failed` | 1 | 4 / 0 / 2 invalid | 2,154 | 18,340 ms | none executed: two calls contained duplicate object keys, so the four-call batch failed atomically |
| `case:bnk_dev_000026` | escalated / `tool_validation_failed` | 1 | 1 / 0 / 1 invalid | 1,752 | 9,588 ms | none executed: the call contained duplicate `candidate_id` and `fragment` keys |

The refused-call count above is the number of invalid calls recorded in the
trajectory. In `case:bnk_dev_000018`, the two otherwise valid batch members
were also deliberately not executed because preflight is atomic. The DEV
ground truth was consulted only after the live run for diagnostic scoring: all
three optimized resolutions matched the expected settlements, so the observed
unsafe-match count remained zero. Ground truth was not available to the
provider, prompt, tools, validator, or policy.

This run demonstrates the intended efficiency effect and the intended
fail-closed behaviour, but five T2 cases from one model are not enough to
promote the experiment. The duplicate-key termination count also rose from one
to two: the model sometimes tried to encode multiple logical calls inside one
argument object even though batching was available. That is a model-behaviour
signal for broader measurement, not grounds for weakening strict decoding or
changing code from this sample alone. Recommendation: **KEEP OPTIMIZED BRANCH
FOR FURTHER TESTING**, including a broader fresh DEV T2/T3 diagnostic and
provider-diverse sampling before any promotion decision.

---

## 10. The comparison tool was asking the model to enumerate candidates for nothing

**Severity: material. Fixed on `exp/stage3-orchestration-opt`; not yet
measured live.**

### What was observed

In the 50-case optimized DEV T2 diagnostic (finding 9), 17 of 50 cases
terminated `tool_validation_failed`. Every one of the 18 malformed calls
behind them was the same shape: two logical operations fused into a single
arguments object with a duplicated key.

```json
{"candidate_id": "A", "fragment": "X", "candidate_id": "B", "fragment": "X"}
```

All 18 duplicated `candidate_id`; 10 also duplicated `fragment`. The prose in
those turns was correct — the model had identified the right fragment and
said so — and the batch shape made the mechanism visible: **16 of 16
odd-sized batches contained a fused call, against 1 of 38 even-sized ones.**
An even plan (k fragments × 2 candidates) emerging as an odd batch is the
arithmetic shadow of exactly one fusion event. The per-operation fusion rate
was ~7.9%, and the per-case failure probability followed the operation count.

### Why it happened

`compare_reference_fragment(candidate_id, fragment)` required a cross-product
the decision layer then discarded.

`decide/validator.py` harvests only the top-level `fragment` from a
comparison output (`_fragments_from`) and re-evaluates it against **every**
candidate in the immutable snapshot itself. The `candidate_id` a call named
never reached a predicate. Measured across the DEV residual, the validator's
result is byte-identical whichever candidate a comparison was aimed at.

So the model was made to spell out an axis with no consumer, at a cost of one
Bernoulli trial per redundant operation. In the 50-case run, 65 of 140
comparison calls (46%) merely repeated an already-tested fragment against the
other candidate, and a further 70 calls were record reads the validator never
consumes at all.

An earlier attempt to fix this with wording — telling the model explicitly
that one invocation is one operation, that 1×2 is two calls and 2×2 is four —
was run against the same 50 cases and **failed**: 17 → 18 failures, resolved
32 → 31, mean tokens 2,619 → 3,304. Instruction does not reach the sampler.
That wording has been reverted.

### What changed

The candidate axis is gone from the interface:

```
compare_reference_fragment(candidate_id, fragment)   ->   compare_reference_fragment(fragment)
```

The tool now walks `snapshot.candidates` itself and returns one entry per
candidate, in snapshot order, with no filter, no ranking and no early exit. A
candidate whose settlements carry nothing comparable still appears with an
empty comparison tuple, so "evaluated and nothing held" cannot be confused
with "omitted".

This moves **no judgement** into the model and none out of it. The fan-out was
already happening deterministically one layer down; it now happens once
instead of twice, and the model no longer has to encode it. Decision
invariance is asserted directly, over the whole 240-case DEV residual, by
feeding the validator the old per-candidate evidence and the new
snapshot-wide evidence for the same fragment set and requiring identical
`validate_case` and `decide` results
(`tests/test_validator.py::TestDecisionInvarianceAcrossTheContractChange`).

The other three tools keep their scalar `candidate_id` / `settlement_id`.
They are targeted record reads — choosing which record to open is real
investigation — and they are where hallucinated-identifier access control
still has something to check.

### What did not change

Atomic reject-all, batch preflight, duplicate-key rejection, the validator's
predicates, the policy gate's blockers and thresholds, and the step budget.
The offline replay in the architecture review showed that 10 of the 17
rejected batches already held sufficient and correct evidence, and that
executing the valid subset would have resolved them correctly. **That was
deliberately not implemented.** Whether a malformed sibling should forfeit a
batch is a separate question about `BLOCKER_TOOL_VALIDATION`, and it is a
policy relaxation, so it does not ride along with an interface correction.

What was fixed instead is that those calls are no longer invisible. Across
the 17 failures, 40 otherwise-valid calls had produced no trajectory record
at all, so a rejected turn read in the audit trail as though the model had
asked for nothing but the malformed call. Every requested call now carries an
explicit status — `succeeded`, `validation_failed`, or
`skipped_due_to_batch_rejection` — with its raw and validated arguments and
no output. No output is what keeps a skipped call out of `raw_tool_evidence`,
so the audit gains a record and the decision gains nothing.

### What is not yet known

Nothing here has been measured live. The predicted effect is arithmetic, not
observed: mean comparison operations per case fall from 4.56 to 1.50 on the
50-case trajectories, which at the measured 7.9% per-operation fusion rate
predicts a per-case failure probability of ~12% against ~31%. `strict: true`
is now also sent on OpenAI-dialect tool declarations, where a provider that
honours it makes a duplicate key ungrammatical rather than merely refused —
but no provider is assumed to honour it, and the local decoder is unchanged.

**The same 50 DEV T2 cases must be re-run live, with fresh trajectory
storage, before any reliability claim is made.** Batches 2–4 stay paused
until that run lands.

---

## 11. VERIFIED EXACT-COHORT RESULT — the same 50 DEV T2 cases, offline-scored

§10 ended by requiring that the same 50 DEV T2 cases be re-run before any
reliability claim. That run has landed, and both sides have now been scored
offline against DEV ground truth by the Stage-4 evaluator
(`benchmark/eval/`), not by reading a run summary.

### The cohort

The comparison cohort is the **exact 50 case IDs** of the historical Ox Alpha
batch — which is precisely the first 50 sorted DEV T2 cases. Verified:

- **50/50 are T2.** No T0, T1 or T3 contamination.
- The new run's transcript covered the first 50 of *all 240* Stage-2
  unresolved cases, so it held 38 of the cohort plus 12 T3 cases. The 12 T3
  cases escalated, correctly, and are **excluded** from the comparison; the
  12 cohort cases it did not cover were run separately and supplied from that
  second transcript. 38 + 12 = 50, set-equal to the Ox cohort, no overlap.

That exclusion is worth stating precisely, because "the excluded cases are
exactly the failures" is the shape of a success-correlated filter. It is not
one: the excluded cases were excluded for being T3, and a T3 case escalating
is the correct outcome, not a failure.

### Results

| | Ox Alpha · investigator.v2 · tools.v1 | Claude Opus 5 · investigator.v4 · tools.v3 |
|---|---:|---:|
| investigated | 50 | 50 |
| resolved | 32 | **50** |
| escalated | 18 | **0** |
| correct auto-resolutions | — (see below) | **50** |
| **wrong auto-resolutions** | **0** | **0** |
| tool-validation-failed cases | 17 | **0** |
| `duplicate_argument_key` rejections | 18 | **0** |
| `unknown_candidate` | 0 | **0** |
| provider failures in cohort | 0 | 0 |
| mean tokens per case | ~2,619 | ~11,371 |
| mean steps per case | ~1.10 | ~1.12 |

The Opus side was scored by replaying its recorded trajectories through the
real validator and policy: 50 cache hits, zero provider calls, zero soundness
violations (exact-paise reconciliation, fragment genuinely present in the
narration, only policy-declared relations, candidate from the deterministic
set).

The Ox side is reported in **recorded-only** mode. Its trajectories were
produced under `tools.v1` / `trajectory-cache.v2`, which the current validator
cannot parse and the current cache key cannot address, so the evaluator
refuses to replay them rather than attribute an old contract's behaviour to
today's code. Its termination and tool-validation counts above are read
directly from the recording; its 32/18 resolved/escalated split comes from
that run's own CLI summary. **Its correctness was never scored**, which is why
the correct-auto-resolutions cell is blank rather than zero. Its `0 wrong` is
the figure that run reported, on the same deterministic gate.

The model requested was `claude-opus-5-thinking`; the gateway reported
`claude-opus-5` on all 56 steps. Requested and reported are recorded
separately and never merged — a run whose numbers came from a substituted
model means something else.

### MANDATORY INTERPRETATION CAVEAT

**Do not claim tools.v3 caused the 32 → 50 improvement.** Three things
changed together between the two runs:

1. Ox Alpha → Claude Opus 5
2. `tools.v1` → `tools.v3`
3. `investigator.v2` → `investigator.v4`

An experiment whose treatment moved three axes at once has no identified
effect on any one of them. The evaluator enforces this mechanically: its
comparison mode counts differing configuration dimensions and emits
`causal_claim: null` whenever more than one moved.

The defensible conclusions are exactly these four:

- **A.** The corrected `tools.v3` contract operated successfully with a strong
  reasoning model across the entire historical 50-case T2 cohort.
- **B.** The old `duplicate_argument_key` failure mode disappeared in the new
  run — 18 rejections across 17 cases became 0.
- **C.** The deterministic validator and policy maintained **0 wrong automatic
  resolutions**, which is the metric DESIGN.md §1 says matters most.
- **D.** **Architecture-only causality remains unproven** without a same-model
  A/B. Isolating the tools.v3 effect requires Opus on `tools.v1`, or Ox Alpha
  on `tools.v3`.

A fourth confound belongs on the record next to the caveat: on the identical
cohort, mean token spend rose roughly 4.3× (~2,619 → ~11,371 per case). Some
of the gain is bought with compute, not with architecture.

### Reproducing it

Both sides are reproducible offline, with no API key:

```bash
# A — historical baseline, recorded-only (its contract is superseded)
python -m benchmark.eval evaluate --split dev --no-replay \
  --run-dump <ox-batch-transcript> \
  --cohort-from-dump <ox-batch-transcript> \
  --expected-tier T2 --provider openrouter --model stealth/ox-alpha \
  --json-out report-oldox.json

# B — new run, replayed through the real validator and policy
python -m benchmark.eval evaluate --split dev \
  --run-dump <opus-batch-transcript> --run-dump <opus-followup-transcript> \
  --cohort-from-dump <ox-batch-transcript> \
  --expected-tier T2 --provider gorouter --model claude-opus-5-thinking \
  --json-out report-opus.json

python -m benchmark.eval compare report-oldox.json report-opus.json
```

`--cohort-from-dump` pointing at the Ox transcript in *both* invocations is
what pins the comparison to one exact cohort. `--limit` ordering would not
have: the two runs' first-50 windows cover different cases.
