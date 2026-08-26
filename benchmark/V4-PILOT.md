# Benchmark v4 — compositional-evidence pilot

**Status: PILOT. Not frozen. Not a reporting artifact.**

No match rate, precision, coverage or value-at-risk figure from the
`v4-pilot` split may be presented as a benchmark result. The split exists to
be inspected case by case and to be argued about; a freeze decision, if one is
taken, gets its own generator version, its own seed, its own manifest and a
`CHANGELOG.md` entry.

Benchmark v3 is untouched by everything described here. Its FROZEN-EVAL
fingerprint is still

```
f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b
```

and `make verify-frozen` passes before and after the pilot exists.

---

## 1. What benchmark v3 is now, and what it is not

v3 remains useful, and it is worth being precise about which uses survive.

**v3 is a good safety-regression benchmark.** Its 40 T3 cases have no
recoverable discriminator, and no strategy tried against them — including
unbounded substring enumeration — has ever manufactured one
(`tests/test_stage3_dev_diagnostic.py::TestAdversarialRefusal`). It is a good
validator and policy regression benchmark, a good tool-contract regression
benchmark, and a good idempotency and audit-trail regression benchmark.

**v3 T2 is not a strong test of AI reasoning.** Exhaustive enumeration of
every contiguous narration substring of length ≥ 4, with no model in the loop,
identifies the correct settlement in **200 of 200** DEV T2 cases and
misidentifies none (`notes/STAGE3-FINDINGS.md` §1). The degraded reference in
a v3 T2 case is *causally necessary* — benchmark v2 established that, and
structured evidence alone still resolves none of them — but necessary turned
out not to imply hard to recover. The search space is a few hundred substrings
against two candidates.

So v3 T2 measures whether a reference can be recovered from noisy text, and
answers: yes, mechanically, at zero risk. That is a real result about
deterministic-first reconciliation architecture. It is **not** evidence that a
language model contributes anything a substring loop does not.

## 2. What v4 is for

One question: *are there realistic reconciliation cases whose answer requires
composing several pieces of evidence, and can a benchmark hold them without
becoming a puzzle?*

The design principle, stated as a prohibition:

> A v4 resolvable case must not be resolvable merely because there exists one
> narration substring that uniquely matches one candidate.

And a semantics for what "resolvable" means, which every archetype is built
against and every ground-truth row obeys:

> A case is resolvable when **exactly one** candidate is consistent with
> **all** the declared evidence. If no candidate is consistent with all of it,
> or more than one is, the correct outcome is escalation.

That second rule is what makes the ambiguous families principled rather than
arbitrary: `ambiguity_conjunction_incomplete` is "more than one candidate
survives everything", and `conflict_stale_reference` is "no candidate survives
everything".

## 3. The architectural conflict the pilot found — since closed

> **Update.** This section describes what the pilot measured against
> `validator.v1`. The gap it identified has since been closed by
> `validator.v2`, which admits conjunctive reference evidence over a
> deterministic closure. It is kept as written because it is the measurement
> that justified the change; sections 5 and 6 carry the after-numbers, and
> `notes/VALIDATOR-V2-FINDINGS.md` carries the design, the threat model and the
> five-rule comparison behind it.

The Stage-3 decision layer, **as of `validator.v1`, could not express a
conjunction.** That was the pilot's principal measurement, and it is worth
reading the code path before reading the numbers.

`finrecon/decide/validator.py` resolves a case when some *one* admissible
fragment reaches exactly one candidate and that candidate is financially
exact:

```python
survivors = set(identified) & set(financially_exact)
```

That is the only conjunction anywhere in the gate, and on any real case it is
degenerate: `finrecon/candidates/generator.py` builds candidates through
`provable_groups()`, which has *already* filtered on exact totals, exact
break-ups and sound break-up references. So every candidate handed to Stage 3
is financially exact, financial exactness never separates candidates within a
case, and the single-fragment reference test is the whole predicate.

Consequences, in order:

1. Every compositional v4 case escalated under `validator.v1`. Not because
   the evidence was missing — because there was no rule that could combine it.
2. v4's match rate under v1 was therefore **8/48 (16.7%)**, with 40 of the 48
   resolvable cases *false escalations*.
3. Fixing it needed a new deterministic capability in the decision layer, not a
   new tool and not a better prompt. That capability is now
   `validator.v2`: conjunctive reference evidence proved over a deterministic
   closure rather than over the agent's selection.

Nothing in the validator, the policy gate, the tools or the prompt was changed
while the pilot was being built — the gap was measured first and closed
second, in that order, and the before-column is preserved as baseline arm
`B1`.

## 4. Case families

Nine archetypes, 64 cases. Families are *descriptive* tags and overlap; the
*required composition* is prescriptive and there is exactly one per case.

| Archetype | n | Candidates | Outcome | Required composition |
|---|--:|--:|---|---|
| `single_fragment_control` | 8 | 3 | resolve | `single_fragment` |
| `conjunction_pair` | 12 | 3 | resolve | `fragment_pair` |
| `conjunction_wide` | 8 | 4, 5 | resolve | `fragment_pair` |
| `conjunction_triple` | 6 | 5 | resolve | `fragment_triple` |
| `amount_reference_hop` | 10 | 3 | resolve | `fragment_and_breakup_amount` |
| `conflict_context_resolves` | 4 | 3 | resolve | `fragment_and_value_date` |
| `conflict_stale_reference` | 4 | 3 | **escalate** | `none` |
| `ambiguity_no_discriminator` | 6 | 3, 4, 5 | **escalate** | `none` |
| `ambiguity_conjunction_incomplete` | 6 | 4 | **escalate** | `none` |

48 resolvable, 16 intentionally ambiguous (25%). Candidate-set sizes: 40 cases
with three, 12 with four, 12 with five. Every size carries both outcomes, so
candidate count predicts nothing.

Family coverage of the brief's eight concepts:

| Concept | Family tag | Cases |
|---|---|--:|
| A · multi-fragment conjunction | `multi_fragment` | 32 |
| B · conflicting evidence | `conflict` | 8 |
| C · more than two candidates | `multi_candidate` | 64 |
| D · non-contiguous evidence | `contextual` | 36 |
| E · multi-hop relation | `multi_hop` | 10 |
| F · amount + reference | `amount_reference` | 10 |
| G · decoy evidence | `decoy` | 46 |
| H · true ambiguity | `true_ambiguity` | 16 |

### How the conjunction is built

References have the structure real ones have:

```
A X I S C N 1 1 | 3 7 | 8 6 3 7 2 7
\_____________/   \_/   \_________/
   head (8)       mid     tail (6)
bank(4)+channel(2)+2 digits
```

That is the shape of `AXISCN1153863727`, the literal `utr` value from
Razorpay's published `settlement.processed` webhook example, already captured
verbatim in the frozen Stage-0 narration library.

A `conjunction_pair` narration carries the **head** in one field and the
**tail** in another, split by a batch marker — the `field_truncation` and
`separator_swap` categories from the frozen Stage-0 corruption taxonomy, which
is what a fixed-width statement export does to a long reference. The head is
shared with a same-bank decoy; the tail is shared with a different-bank decoy
whose trailing run coincides. Neither clue alone separates the set; their
intersection does.

`conjunction_triple` adds a third rendering of the same reference with its
digits reordered, so that every *pair* of clues leaves two candidates and only
all three leave one. It is the arity probe, and it is the most synthetic
archetype here — three degraded renderings of one reference in one statement
line is a stretch, and it is labelled as one.

`amount_reference_hop` and `conflict_context_resolves` cross modalities: half
the evidence is a reference relation, and half is a break-up line amount or a
settlement date. No amount of substring comparison reaches the second half,
because the second half is not a reference.

### The unsafe-auto-match probe

`conflict_stale_reference` is the one archetype whose correct outcome is
escalation *and* which today's gate resolves. Its narration carries a stale
reference tail belonging to a settlement that is not the counterparty, plus a
value-date field that agrees with two different candidates. No candidate is
consistent with all the evidence, so escalation is the only correct answer —
and a strategy that stops at the first fragment to separate the candidate set
resolves it confidently and wrongly.

Its ground truth is `ESCALATE` rather than "the date-consistent candidate", on
purpose. Marking one of the two date-consistent candidates as the answer would
be a contestable answer key; marking it unresolvable makes the finding
unambiguous.

### Anti-leakage construction

- The case plan is shuffled with a seeded RNG, so archetype does not correlate
  with case index or with any record identifier derived from it. Longest run
  of one archetype in case-ID order: 2.
- Candidate build order is shuffled per case, so the true settlement's position
  in identifier order carries no signal. Observed: the truth is the
  lowest-numbered candidate in 14 of 48 resolvable cases against a chance rate
  of 14.3, and the highest in 16.
- Every digit in a generated reference is drawn from `1-9`. Record identifiers
  are zero-padded six-digit ordinals, so at this scale every identifier suffix
  of length ≥ 4 contains a `0` — which makes it *impossible* for a numeric
  narration fragment to stand in a `suffix_of_reference` relation to a
  settlement ID by accident. That removes a whole class of silent
  discriminators rather than testing for them.
- Amounts are drawn from benchmark v3's band regardless of archetype, and the
  resolvable and ambiguous value ranges overlap.
- No family, archetype or composition label appears anywhere in the visible
  dataset files, in a case snapshot, or in the agent briefing. Asserted by
  search, not by convention.

## 5. Deterministic baselines

`benchmark/baselines/` — six arms, zero provider calls, ground truth read
only after every decision has been made.

| Arm | What it is |
|---|---|
| **A** rules only | The unmodified Stage-2 core |
| **B1** validator.v1 semantics | Every admissible substring under the rule shipped *before* `validator.v2`. Kept as the before-column, so it cannot silently track the after-column |
| **B** the shipped gate, exhaustively fed | Every admissible substring, through the **real** validator and the **real** policy gate. Not an estimate of the shipped ceiling — the ceiling itself, which is why it moved when `validator.v2` landed |
| **C1** lexical composition | Intersect the reach sets of every fragment; resolve iff exactly one candidate is consistent with all of them |
| **C2** lexical + structural | C1 plus two features read mechanically from the narration: money amounts against break-up lines, dates against settlement dates |
| **C3** first subset that isolates | C2's features under the *aggressive* rule — resolve if **any** subset isolates a candidate |

### Results on the pilot, under `validator.v2`

```
arm                                      resolved  correct  WRONG  escalated  match   ₹ at risk
A_rules_only                                    0        0      0         64  0.000           0
B1_validator_v1_semantics                      12        8      4         52  0.167  13,445,282
B_shipped_gate_exhaustive                      38       34      4         26  0.708  13,445,282
C1_lexical_composition                         38       34      4         26  0.708  13,445,282
C2_lexical_and_structural_composition          48       48      0         16  1.000           0
C3_first_subset_that_isolates                  52       48      4         12  1.000  13,445,282
```

By required composition (resolved / cases):

```
composition                       A      B1       B      C1      C2      C3
single_fragment                 0/8     8/8     8/8     8/8     8/8     8/8
fragment_pair                  0/20    0/20   20/20   20/20   20/20   20/20
fragment_triple                 0/6     0/6     6/6     6/6     6/6     6/6
fragment_and_breakup_amount    0/10    0/10    0/10    0/10   10/10   10/10
fragment_and_value_date         0/4     0/4     0/4     0/4     4/4     4/4
none                           0/16    4/16    4/16    4/16    0/16    4/16
```

`B1` is the pre-v2 rule, kept so the before-column does not silently track the
after-column. `B` is the shipped gate, whose ceiling moved when `validator.v2`
landed — the `B1`-to-`B` gap is exactly what v2 bought.

### What the numbers say

**Single-fragment matching genuinely cannot reach the compositional
families.** `B1` is the rule that saturated v3 T2 at 200/200. On v4 it reaches
8 of 48 — the control family and nothing else — and a *bounded*
six-fragment mechanical investigator through the same rule reaches the same 12.
On v4 the ceiling was the predicate, not the search effort, which is the
inverse of v3 where enumerating harder was worth 29 more cases. That
measurement is what justified `validator.v2`.

**The shipped gate now composes, and it cost nothing in safety.** `B` moved
from 12/8/4 to 38/34/4: +26 correct resolutions, all of them conjunctive, and
**no new wrong ones**. See `notes/VALIDATOR-V2-FINDINGS.md` sections 6-7 for
the adversarial suite and the before/after behind that.

**Composition without a consistency rule would not have been a safety
improvement.** `C3` has `C2`'s features under an aggressive rule and resolves
the four stale-reference cases wrongly; `C2` escalates all four. The difference
between "exactly one candidate is consistent with *everything*" and "some
combination points here" is the entire safety result — and it is why the
shipped rule is the conservative one.

**The remaining gap is `B`-to-`C2`, and it is entirely structural.** 14 cases:
10 needing a break-up-line amount, 4 needing a settlement date. Both are
features no reference relation can express, and both are deliberately out of
scope for `validator.v2`. The ₹1.34 crore still at risk on this pilot is
attributable to that one missing deterministic relation, not to any model
behaviour.

**And the honest finding: C2 solves the pilot completely.** 48 of 48, zero
wrong. So v4 as it stands is *fully* solvable by a deterministic composition
baseline.

That is close to a tautology and must be read as one. C2 composes exactly the
feature vocabulary the generator uses to *define* its cases. A benchmark whose
difficulty is built from a declared, finite set of mechanical features is
solvable by exhaustively composing that same set. Raising the conjunction
arity raises the exponent, not the complexity class: the `fragment_triple`
family defeats a strictly pairwise solver and falls to a three-wise one, and
nothing about that generalises.

**What this means for the C-vs-D ablation.** v4 does not restore room for a
model to beat a deterministic arm on final accuracy, and no amount of further
v4 design in this direction will. What v4 *did* isolate, and v3 did not, is a
narrower and more useful claim: a capability gap in the shipped decision layer,
found for zero tokens and since closed. The pilot's value was diagnostic, and
the diagnosis was acted on.

## 6. Success criteria, checked

| # | Criterion | Status |
|--:|---|---|
| 1 | v3 remains frozen | **Met.** Fingerprint unchanged; `make verify-frozen` passes |
| 2 | No production ground-truth dependency introduced | **Met.** Isolation tests extended to `benchmark.generator_v4` and to v4's label vocabulary |
| 3 | Intended truths present in candidate snapshots | **Met.** 48/48; zero missing |
| 4 | Ambiguous cases genuinely ambiguous | **Met.** 12 of 16 admit no lexical composition at any arity; the other 4 are the stale-reference probe, whose ambiguity is a *conflict* rather than an absence |
| 5 | Single-fragment matching cannot solve the compositional subset | **Met, decisively.** 0 of 40 compositional-resolvable cases under the pre-v2 rule (baseline arm `B1`). `validator.v2` now reaches 26 of them, which is what the criterion was diagnosing |
| 6 | No wrong automatic deterministic resolutions | **Met with a stated exception.** Arms `B1`, `B`, `C1` and `C3` each resolve the same 4 cases wrongly, and every one is `conflict_stale_reference` — a case with no correct answer, built to expose exactly that. No arm ever resolves a *resolvable* case incorrectly, which is the property that would mean the benchmark misleads. `validator.v2` added none of them and removed none: separating them needs a settlement-date relation, not a conjunction |
| 7 | Cases remain realistic and inspectable | **Met, with one caveat.** 64 cases, 778 records. `conjunction_triple` is the most synthetic archetype and is flagged as such |
| 8 | No trivial ID or order leakage | **Met.** See §4; the audit ships in the baseline report |
| 9 | Stage 4 reports family-level metrics | **Met.** `metrics_by_family`, `metrics_by_required_composition`, `metrics_by_candidate_count`, `metrics_by_archetype`, `metrics_by_tier` |
| 10 | No paid provider calls | **Met.** Zero. Structurally asserted for `benchmark/baselines/` and `benchmark/eval/` |

## 7. Known limitations of the pilot

**Three narration shapes appear only in resolvable cases.** The refund-amount
field, the long truncated reference and the reordered rendering are each used
by resolvable archetypes only, so "this line has an RFND field, therefore this
case has an answer" is a true rule over 24 of the 64 cases. It scores nothing —
every metric requires naming a settlement, and a shape label names none — but
it is a real correlation and it is measured in the leakage audit rather than
omitted. Removing it means building ambiguous variants of those archetypes,
which is full-v4 work.

**Difficulty is relative to the declared relation set.** The invariant checker
imports `finrecon.evidence.reference` rather than restating it, deliberately:
the question is what the shipped validator will actually do. The cost is that
"no single fragment identifies the truth" means "under these seven relations".
This is the same limitation `notes/STAGE3-FINDINGS.md` §1 records for v3, and
it does not go away by making cases harder.

**The taxonomy is still one person's.** v4 does not fix the concern DESIGN.md
§10 states about v1–v3; a pilot authored by the same person who built the
matcher, corrected by that person, is what it is.

**`conjunction_triple` sits closest to the line the brief draws.** Three
degraded renderings of one reference in a single statement line is defensible
— a bank reference field and a remitter remark field can both carry a mangled
copy — but it is closer to a construction than to an observation, and it exists
for a diagnostic purpose rather than a realistic one.

## 8. Reproducing it

```bash
make generate-v4-pilot     # build and write the pilot (~65s; every case is verified twice)
make verify-v4-pilot       # recompute the pilot fingerprint against its manifest
make reconcile-v4-pilot    # one deterministic Stage-2 pass; reports no accuracy
make baselines-v4-pilot    # the five arms, zero provider calls
make test-v4               # the pilot's own tests
make verify-frozen         # benchmark v3, still frozen
```

Pilot fingerprint (reproducibility marker, **not** a freeze):

```
38e7e67eb79f51f946f2a0042f5ee2f0edd9497dea24d83f067bd0082bee1e1c
```

## 9. The capability that was missing — half of it now exists

Required by 40 of the 48 resolvable cases when the pilot was built. 26 of those
40 are now reachable; 14 are not.

### Done: conjunctive reference evidence (`validator.v2`)

`validator.v1` had no way to intersect evidence. It computed, per fragment, the
set of candidates that fragment reached, and used only the fragments reaching
exactly one. Fragments reaching two or more were recorded and discarded, which
made conjunction unreachable — and, as the pilot's adversarial
fixtures then showed, also made a contradicting two-candidate fragment
invisible, which was a safety hole rather than a coverage one.

`validator.v2` resolves a case when exactly one candidate is consistent with
**every** informative claim in the narration's deterministic *closure* —
every fragment standing in a declared relation to any candidate reference,
whether the agent asked about it or not.

The obvious version of this — intersect the fragments the *agent* tested —
was measured and rejected as unsafe: one narration can prove three different
candidates depending on which pair the agent happens to test, which is the
model selecting the winner by selecting where not to look. Full design, threat
model, five-rule comparison and adversarial results:
`notes/VALIDATOR-V2-FINDINGS.md`.

| Required composition | Cases | Status under `validator.v2` |
|---|--:|---|
| `fragment_pair` | 20 | **resolved** |
| `fragment_triple` | 6 | **resolved** |
| `fragment_and_breakup_amount` | 10 | still escalated |
| `fragment_and_value_date` | 4 | still escalated |

Cost: `validator.v1` —> `validator.v2`. Nothing else moved — same prompt,
same tools, same loop, same trajectory record format, same policy gate and its
same blocker vocabulary. Because the validator version is part of the
trajectory cache key, every v1 artifact now fails replay closed rather than
being reinterpreted.

### Still owed: structural evidence

The remaining 14 cases need a second, larger addition: two declared structural
relations, comparing a narration token against a **break-up line amount** and
against a **settlement date**. That is a genuine widening of what counts as
evidence and deserves its own decision.

**The tools already expose everything required.** This was never a tool gap.
`inspect_settlement_breakup` returns every break-up line with its amount,
referenced record and that record's status, so the refund magnitude in an
`amount_reference_hop` case is already visible. `lookup_candidate_records`
returns each settlement's date. `compare_reference_fragment` returns, per
candidate, every declared relation and its pinned-character count. The evidence
is in the trajectory; the gate has no predicate that reads it that way.

**This is now the strongest remaining argument for that capability.** The
₹1.34 crore of value at risk on this pilot is entirely attributable to the
four `conflict_stale_reference` cases, and what refutes those is a *date*. No
reference-only rule can escalate them — their reference closure contains one
informative claim and nothing contradicts it — so the remaining risk on this
pilot traces to one missing deterministic relation rather than to any model
behaviour. The pilot's `conflict_context_resolves` archetype is that
capability's positive control, and is waiting.

**Recommendation.** Unchanged on the benchmark, updated on the backend:

- **Do not freeze a full v4 yet.** `notes/BENCHMARK-V4-FINDINGS.md` §5 still
  holds: a deterministic composition baseline solves the pilot completely, so a
  frozen v4 would freeze a benchmark whose ceiling is a 200-line composer.
- **The validator decision has been taken**, and the pilot is now a live
  regression suite for it rather than a pending question.
- **Take the structural-evidence decision next.** It is the only thing standing
  between this pilot and zero value at risk, and it is a decision about
  declared evidence rather than about a model.
- **A live run is now worth its tokens.** With 26 of the 48 resolvable cases
  reachable by the deployed gate, a hosted-model pass over the pilot would
  measure something — how efficiently an agent seeds a path whose proof it
  does not control — rather than 48 false escalations. That run has not been
  made; no number here is a model result.
