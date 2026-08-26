# validator.v2 — safe conjunctive reference evidence

Recorded, not acted on beyond the change itself. Benchmark v3's generator,
seeds, taxonomy, datasets and frozen hash were untouched; so were the Stage-3
tools, the prompt, the agent loop, the candidate generator, the policy gate and
every integer-paise rule.

Frozen FROZEN-EVAL SHA-256, verified before and after:

```
f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b
```

**No provider was called at any point.** Every number below comes from
deterministic code or from the non-linguistic `MechanicalInvestigator` fake.

---

## 1. The threat model, and why the obvious rule is unsafe

**Severity: this is the finding that determined the design.**

`benchmark/V4-PILOT.md` §3 established that the shipped decision layer could
not express a conjunction at all. The obvious fix — intersect the reach sets of
the fragments the agent tested — is unsafe, and not marginally.

Three references arranged the way same-bank same-day references actually are:

```
A = AXISCN1137863727      head AXISCN11 ......... tail 863727
B = AXISCN115842Q7K4      head AXISCN11 ......... tail Q7K4
C = Q7K4M291863727        head Q7K4 ............. tail 863727

"AXISCN11" -> {A, B}      "863727" -> {A, C}      "Q7K4" -> {B, C}
```

One narration carrying all three spans yields:

| The agent tests | Model-selected intersection | Verdict |
|---|---|---|
| head + tail | `{A}` | resolve **A** |
| head + hinge | `{B}` | resolve **B** |
| tail + hinge | `{C}` | resolve **C** |
| all three | `{}` | escalate |

The same bank line proves three mutually exclusive answers depending on which
pair the model happened to look at. That is not a weak rule; it is the model
**selecting the winner by selecting where not to look** — the
fishing-by-omission channel DESIGN.md §4.1 and §11 exist to close, reappearing
one level up wearing a conjunction's clothes.

Measured rather than argued: `benchmark/baselines/adversarial.py` carries all
three angles as fixtures, and the harness shows R1 resolving A, B and C
respectively on the identical snapshot.

**A second hole, in the rule already shipped.** The same fixture set exposed a
pre-existing `validator.v1` defect. v1 discarded fragments reaching two or more
candidates as non-probative — correct for a fragment like `"SETL"` that reaches
*every* candidate, but wrong when the fragment reaches two out of three: "the
reference is consistent only with B or C" *refutes* "the reference is A". v1 set
the refutation aside and resolved A. Two fixtures catch it
(`stale_strong_reference_plus_hinge`, `contradiction_after_untested`), and v2
escalates both.

So v1 was not merely less capable than v2. It was less safe, in a way only the
conjunctive analysis made visible.

---

## 2. Five rules compared before any of them shipped

**Severity: procedural. The comparison is the reason the choice is defensible.**

`benchmark/baselines/conjunction.py` implements five candidate rules;
`conjunction_report.py` runs them over the adversarial fixtures, the v4 pilot
and the 240-case DEV residual. `make conjunction-rules`.

| Rule | Evidence set |
|---|---|
| **R0** | v1 as shipped: a lone fragment reaching exactly one candidate |
| **R1** | intersect the reach sets of the agent's fragments |
| **R2** | if the agent surfaced *any* admissible evidence, intersect the **closure** |
| **R3** | intersect the closure regardless of what the agent did |
| **R4** | the agent's fragments must isolate a candidate and the closure must agree |

```
rule                              adv  ord  dup  noEv  devWrong  devT2  vs v1  T3res  v4 res/corr/wrong  SHIP
R0_v1_single_fragment               F    T    T     T         0    171     +0      0             12/8/4  False
R1_model_selected_intersection      F    T    T     T         0    171     +0      0            38/34/4  False
R2_seeded_closure_intersection      T    T    T     T         0    171     +0      0            38/34/4   True
R3_open_closure_intersection        T    T    T     F         0    200    +29      0            38/34/4  False
R4_closure_verified_model_proof     T    T    T     T         0    171     +0      0            38/34/4   True
```

`adv` = safe on all 14 adversarial fixtures. `noEv` = still refuses a case the
investigation gathered no evidence for.

Three things fall out:

- **R0 and R1 are adversarially unsafe.** R0 fails 3 fixtures (2 safety, 1
  capability); R1 fails 5, including all three cherry-picking angles.
- **R2, R3 and R4 are adversarially safe and identical on the v4 pilot.**
  Composition buys the same 26 cases whichever way the evidence set is gated.
- **R3 costs an invariant.** It is the only rule that resolves a case the
  investigation gathered nothing for.

---

## 3. Why R2 shipped rather than R3, and why the deciding criterion is mechanical

**Severity: material. The one place my first instinct was wrong.**

R3 — unconditional closure — looked best. It is the "full deterministic
closure" the brief expressed a preference for, it is tractable (1.4 ms/case on
DEV, 4.3 ms/case on the v4 pilot), and it is the only rule that *improves* DEV
T2 coverage, from 171 to 200 with the same bounded agent.

It was wrong, and the repository said so before I did. Two tests, at two
layers, already assert that a case the investigation gathered no evidence for
must escalate:

- `tests/test_validator.py::TestSurvivorArithmetic::test_no_evidence_at_all_leaves_no_survivor`
- `tests/test_policy.py::TestEvidenceBlockers::test_no_evidence_at_all_escalates`

Both fail under R3. That is not a stale test needing an update — it is a
deliberate, twice-asserted safety property, and the +29 DEV cases R3 gains are
*precisely* the cases where the bounded agent found nothing admissible. R3's
coverage win is the invariant being spent.

A finance controller that auto-resolves a case whose audit trail shows the
investigation contributing nothing is a worse artifact than one that escalates
it, even when the deterministic proof is sound. So R2 ships: **the closure is
the proof, the agent's evidence is the seed.**

The criterion is now measured rather than asserted. `run_no_evidence_invariant`
offers each rule a fixture whose closure *does* identify a candidate, with no
agent evidence, and requires a refusal. It is what disqualifies R3 in the table
above, so a future reader does not have to take my word for the choice.

R2 and R4 are indistinguishable on every measured axis. R2 ships because it is
the simpler claim: its seed carries no selection power whatsoever, whereas R4's
model-intersection component does, and a component with selection power is a
component that needs a threat model.

---

## 4. What the seed does and does not do

The distinction the whole safety argument rests on:

> **Which** fragment the agent surfaced does not change what the closure
> concludes. Only **whether** it surfaced one changes whether the closure is
> consulted.

So the omission attack has nothing to work with — leaving a clue untested
changes neither the closure nor the intersection — while an uninvestigated case
still cannot move money. Asserted directly: every non-empty subset of a
narration's clues, offered as the agent's evidence, reaches the same decision
(`test_which_fragments_the_agent_tested_cannot_change_the_answer`).

**Stated plainly, because it is a real cost:** the agent no longer contributes
to the reference decision. Its fragment selection is now a *measured* quantity
(`agent_surfaced_atom_ids`, reported by Stage 4 as `agent_atom_coverage`) rather
than an input to any predicate. There is no reason to let an untrusted component
choose the evidence when the complete evidence costs four milliseconds.

What the agent keeps is what a closure cannot do: choosing which *records* to
open, and — when structural evidence is eventually admitted — which amount or
date in a narration is worth testing at all.

---

## 5. The safety properties, and where each is established

| Property | How it holds | Where |
|---|---|---|
| Full snapshot | every atom is evaluated against every candidate; the model supplies no candidate axis | `TestTheModelNeverSuppliesTheCandidateAxis` |
| Order invariance | set intersection, not a vote | `TestInvariance` — every permutation of every fixture |
| Duplicate invariance | intersecting a set twice is intersecting it once | `TestInvariance` — ×2 and ×3 |
| Overlap invariance | fragments grouped into atoms by reach set | `TestInvariance` — 3 slices of one span collapse to 1 atom |
| Monotonic contradiction | adding a claim can only shrink an intersection | `TestContradictionIsMonotonic` |
| Fail closed | 5 declared refusal states | `TestFailsClosed` |
| Source provenance | fragment, offsets, span, relation, reach, atom id | `TestProvenance` |

Three of these are properties of the *operation* rather than checks bolted onto
it. They are tested anyway: a future rule that reintroduced vote-counting would
pass a review and fail that file.

**The evidence-atom rule**, since §3.D asked for one to be declared: an atom is
the equivalence class of narration fragments sharing a reach set. The
representative is the longest member, ties broken lexicographically. `"ABC123"`,
`"BC12"` and `"C123"` are therefore one claim read three ways, not three claims
— and because the rule is an intersection rather than a count, there is nothing
for them to inflate even before the grouping.

**The enumeration bound is a refusal, not a truncation.** Above
`MAX_NARRATION_LENGTH` (240 characters; the longest narration in any committed
split is 70) the closure reports `is_complete=False` and nothing is identified.
A partially searched narration cannot support a claim about what the narration
does *not* contain, which is exactly the claim a conjunction makes.

---

## 6. Adversarial results

14 fixtures, through the production validator and gate.

| Fixture | Attack | R0 (v1) | R1 | **v2 (R2)** |
|---|---|---|---|---|
| `cherry_picking` | omission | escalate | **resolve A** | escalate |
| `cherry_picking_toward_b` | omission | escalate | **resolve B** | escalate |
| `cherry_picking_toward_c` | omission | escalate | **resolve C** | escalate |
| `duplicate_cannot_strengthen` | duplicate | escalate | escalate | escalate |
| `overlapping_slices_of_one_span` | false independence | escalate | escalate | escalate |
| `generic_wrapper_plus_specific` | false independence | escalate | escalate | escalate |
| `stale_strong_reference_plus_hinge` | stale reference | **resolve A** | **resolve A** | escalate |
| `contradiction_before` | control | resolve A | resolve A | resolve A |
| `contradiction_after` | contradiction | escalate | escalate | escalate |
| `contradiction_after_untested` | contradiction by omission | **resolve A** | **resolve A** | escalate |
| `fabricated_only` | fabrication | escalate | escalate | escalate |
| `fabricated_plus_real` | fabrication | escalate | escalate | escalate |
| `two_candidate_clean_resolution` | regression control | resolve A | resolve A | resolve A |
| `conjunction_clean_resolution` | capability control | **escalate** | resolve A | resolve A |

Bold marks a wrong outcome. v2: 14/14.

The two control rows are load-bearing. Without `conjunction_clean_resolution` a
rule that escalates everything would score perfectly; without
`two_candidate_clean_resolution` a rule that lost benchmark v3's 200 T2 cases
would too.

---

## 7. v4 pilot: before and after

Real Stage-3 pipeline — actual loop, validator and gate — driven by the bounded
`MechanicalInvestigator`.

| | v1 | **v2** |
|---|---:|---:|
| Resolved | 12 | **38** |
| Correct | 8 | **34** |
| **Wrong** | 4 | **4** |
| Escalated | 52 | 26 |
| Match rate | 0.167 | **0.708** |
| Value at risk | ₹1,34,45,282 | ₹1,34,45,282 |

By required composition (correct / cases):

| Composition | v1 | **v2** |
|---|---|---|
| `single_fragment` | 8/8 | 8/8 |
| `fragment_pair` | 0/20 | **20/20** |
| `fragment_triple` | 0/6 | **6/6** |
| `fragment_and_breakup_amount` | 0/10 | 0/10 |
| `fragment_and_value_date` | 0/4 | 0/4 |
| `none` (16 unresolvable) | 4 wrong | 4 wrong |

**+26 correct resolutions, zero new wrong ones.** All 26 are conjunctive by the
strict definition — no single claim would have sufficed. The 12 single-claim
resolutions are the 8 controls plus the 4 stale-reference cases.

Escalation blockers on the 26 remaining escalations: 20 ×
`ambiguous_reference_link` (the cross-modal families, where the reference
evidence genuinely leaves two candidates) and 6 × `no_reference_link` (the
referenceless narrations).

### The 18 cases still escalated, and why

- **10 `fragment_and_breakup_amount`** and **4 `fragment_and_value_date`** —
  half of each case's evidence is a break-up line amount or a settlement date.
  No reference relation can compare a substring against either, so these are
  out of scope by construction, exactly as §2 of the brief requires.
- **4 `conflict_stale_reference`** — resolved, wrongly, and **unchanged from
  v1**. This is the honest caveat below.

---

## 8. The stale-reference archetype cannot be fixed by reference evidence

**Severity: material. The criterion in the brief that cannot be met, and why
that is not a reason to withhold v2.**

The brief listed "0 stale-reference unsafe resolutions" as a shipping gate. It
is unachievable with reference evidence alone, and the reason is structural
rather than a matter of effort.

A `conflict_stale_reference` case carries a stale reference tail from a
settlement that is not the counterparty, plus a value-date field agreeing with
two *different* candidates. Its reference closure contains exactly one
informative atom — `{A}`. Nothing in the reference evidence contradicts it,
because what refutes A is a **date**. The closure intersection is `{A}`, so any
rule that resolves on a consistent singleton resolves A.

The only reference-only ways out both fail:

- Requiring ≥2 informative atoms would escalate all 200 DEV T2 cases and all 8
  v4 controls. Benchmark v3 T2 is *built* to turn on one recovered reference.
- Raising the pinned-character floor is a policy change out of scope, and the
  stale tail pins 6 characters against a floor of 4 — well clear.

So v2 leaves those four cases exactly as v1 left them: **v2 introduces no new
wrong resolution and removes none of these four.** The gate that matters —
"does this change make anything less safe?" — is met, and the harness records
the stale archetype as explicitly *not* a shippability criterion so nobody
later reads its absence as an oversight.

What *would* fix them is the second capability `benchmark/V4-PILOT.md` §9
names: a declared narration-date-to-settlement-date relation. The pilot's
`conflict_context_resolves` archetype is its positive control and is waiting.

**This is now the single strongest argument for that capability**, and it is
worth stating in those terms: the remaining ₹1.34 crore of value at risk on the
pilot is entirely attributable to one missing deterministic relation, not to any
model behaviour.

---

## 9. Benchmark v3 regression

| | v1 | **v2** |
|---|---:|---:|
| DEV resolved | 171 | 171 |
| DEV correct | 171 | **171** |
| DEV wrong | 0 | **0** |
| T2 correct | 171 | 171 |
| T3 resolved | 0 | **0** |
| Conjunctive resolutions | — | **0** |

Unchanged, case for case. Two details worth recording:

- **No T2 case resolves conjunctively.** v3's T2 tier is built to turn on one
  recovered reference, and it still does. A v2 that had started resolving T2 by
  composition would mean the closure had found corroboration the tier's
  construction never put there — worth knowing, and it has not happened.
- **All 40 T3 cases escalate with `no_admissible_agent_evidence`.** A
  referenceless narration offers the closure nothing, so abstention is forced by
  the evidence rather than by the rule.

The 171-vs-200 gap is the bounded agent's, not the rule's: the closure reaches
all 200, and R2 declines the 29 the agent found nothing for. That is the
invariant from §3 doing its job, visible as a number.

---

## 10. Versioning and replay

`validator.v1` → **`validator.v2`**. Nothing else moved:

| Constant | Value | Why |
|---|---|---|
| `PROMPT_VERSION` | `investigator.v4` | prompt untouched |
| `TOOL_SCHEMA_VERSION` | `tools.v3` | no tool added, none changed |
| `AGENT_LOOP_VERSION` | `loop.v2` | termination states and call bounds unchanged |
| `CACHE_SCHEMA_VERSION` | `trajectory-cache.v3` | the trajectory *record format* is unchanged; v2 reads the same `output["fragment"]` |
| `VALIDATOR_VERSION` | **`validator.v2`** | the reference-identification rule changed in both directions |
| `POLICY_VERSION` | `policy.v1` | see below |

**Why the cache schema did not change.** v2 consumes exactly what v1 consumed
from a trajectory. A v3-format record is still fully parseable and still means
what it meant; what changed is the conclusion drawn from it. Bumping the record
format would have invalidated fixtures for a reason that does not exist.

**Why the policy gate did not change.** This was a design constraint on v2, not
a happy accident. A contradiction is reported to the gate as the *union* of the
contradicting claims, so it fires the existing `ambiguous_reference_link` —
evidence pointing at more than one candidate and therefore at none, which is
what that blocker already means. Introducing a `contradictory_reference_evidence`
blocker would have changed the escalation vocabulary and forced a `policy.v2`
that nothing about the gate's rule justifies. `HARD_BLOCKERS` is asserted
unchanged.

**Replay fails closed.** `validator_version` is part of the trajectory cache
key, so every v1 artifact now keys differently, misses on replay, and aborts the
evaluation with the versions named — never a silent rescore. Asserted three
ways: the key demonstrably changes with the version, a stale artifact raises
`EvaluationError`, and `validator_version` is already one of
`compare.CONFIGURATION_DIMENSIONS`, so a v1-vs-v2 comparison is attributed to
the validator rather than to a model. The committed fixture corpus
(`fixtures/trajectories/`) is empty, so nothing on disk was invalidated.

---

## 11. Cost

Full-substring closure, exhaustive, no sampling:

| Split | Per case | Total |
|---|---:|---:|
| DEV (240 cases, ~34-char narrations) | 1.4 ms | 0.34 s |
| v4 pilot (64 cases, ~50-char narrations) | 4.3 ms | 0.28 s |

Two optimisations get it there, and both are equivalence-checked rather than
trusted:

- A boolean shadow of the relation predicate that skips building
  `ReferenceComparison` and its seven members. ~100× faster, and asserted
  identical to the model path over 100,000+ fragment/reference pairs.
- A conservative character-set prefilter. Asserted to omit nothing over the
  whole DEV and v4 corpus — the critical direction, since omitting an atom can
  only *grow* an intersection, which is the direction that turns an escalation
  into a resolution.

---

## 12. What this is and is not

It is a **deterministic decision-layer capability**. It is not an
LLM-accuracy feature, and the v4 numbers above are not a model result — they
were produced by a non-linguistic fake.

The architecture is unchanged in shape and sharper in fact:

```
AI investigates ......... chooses which records to open; seeds the reference path
Validator proves ........ the closure decides, over evidence the AI cannot select
Policy authorises ....... resolve or escalate; unchanged
```

What v2 changes is the *middle* line's strength. Before, the validator proved
things about evidence the agent had chosen. Now it proves them about all the
evidence there is. That is strictly more of what the deterministic layer was
always for, and strictly less discretion for the untrusted one — and the
measured price is that the agent's fragment selection stopped mattering, which
was already true in effect (`notes/STAGE3-FINDINGS.md` §1) and is now true by
construction.
