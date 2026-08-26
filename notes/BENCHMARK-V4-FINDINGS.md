# Benchmark v4 pilot — findings

Recorded as measured. Benchmark v3's generator, seeds, taxonomy, datasets and
frozen hash were untouched throughout; so were the Stage-2 matchers, the
Stage-3 tools, the prompt and the policy gate.

**The validator has since changed, and only after this was written.** Finding 1
below is the measurement that justified `validator.v2`; the pilot was built and
its diagnostics taken while `validator.v1` was still in place, which is the
ordering that makes the finding evidence rather than a justification written
backwards. See `notes/VALIDATOR-V2-FINDINGS.md`.

Frozen FROZEN-EVAL SHA-256, verified before and after this work:

```
f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b
```

**No provider was called at any point.** The five arms are deterministic, the
one Stage-3 pass over the pilot was driven by the non-linguistic
`MechanicalInvestigator` fake, and both facts are asserted structurally rather
than promised.

---

## 1. The shipped decision layer cannot express a conjunction at all

**Severity: material. The pilot's principal finding, and it is about the
system rather than about the benchmark.**

> **Acted on.** This finding was the justification for `validator.v2`, which
> admits conjunctive reference evidence proved over a deterministic closure.
> The section below describes `validator.v1` and is kept as the measurement
> that motivated the change; the design, the threat model, the five-rule
> comparison and the before/after are in
> `notes/VALIDATOR-V2-FINDINGS.md`. On the pilot, v2 turned 8/48 into 34/48
> with no new wrong resolution.

### What was observed

`finrecon/decide/validator.py` resolves a case on exactly one predicate:

```python
survivors = set(identified) & set(financially_exact)
```

where `identified` is the set of candidates named by fragments that reached
*exactly one* candidate. Fragments reaching two or more are recorded and
discarded.

The `&` looks like a conjunction and is not one in practice.
`finrecon/candidates/generator.py` builds candidates through
`provable_groups()`, which already filters on exact group totals, exact
break-ups and sound break-up references — so every candidate that reaches
Stage 3 is financially exact. Measured on the pilot: 228 of 228 candidates,
every one from `exact_total_in_window` blocking. Financial exactness therefore
never separates candidates *within* a case, and the single-discriminating-
fragment test is the entire resolution rule.

### Why it happens

It is a deliberate design decision working exactly as designed, one layer up
from where the design intended it to bite.

`notes/STAGE3-FINDINGS.md` §4 records the reasoning: a fragment like `"SETL"`
stands in a prefix relation to *every* candidate's settlement ID at once, so a
rule that let any reaching fragment veto a resolution would punish a model for
looking around. The corrected rule counts only fragments that discriminate.

That is right, and it has a consequence nobody wrote down: it also discards
precisely the evidence a conjunction is made of. Two fragments that each reach
two candidates and share one are, between them, conclusive — and under this
rule each is individually "not probative" and both are set aside.

### What it implies

Every v4 archetype whose answer requires composition escalates under the
current gate, with `no_reference_link` as the blocker. Measured over all 64
pilot cases through the real validator and the real policy:

| Investigator | Resolved | Correct | Wrong | Escalated |
|---|---:|---:|---:|---:|
| Bounded mechanical fake (6 fragments) | 12 | 8 | 4 | 52 |
| Exhaustive single-fragment enumeration | 12 | 8 | 4 | 52 |

All 52 escalations carry `no_reference_link` and nothing else. The two rows
being identical is the finding restated: on v4, enumerating harder buys
nothing, because the ceiling is the predicate rather than the search. On v3 T2
the same two strategies differed by 29 cases.

Match rate under the shipped architecture on this pilot: **8/48 = 16.7%**, with
40 false escalations.

**This is not licence to change the validator to make v4 pass.** The smallest
honest addition is stated in `benchmark/V4-PILOT.md` §9, it needs its own
version bump and its own decision, and it invalidates every cached trajectory.
It is not a change that rides along with a benchmark pass.

---

## 2. Compositional evidence is genuinely beyond single-fragment matching

**Severity: the pilot's design goal, met.**

Arm B — every admissible narration substring, through the real validator and
the real gate — is the arm that reached 200 of 200 DEV T2 cases in benchmark
v3. On the v4 pilot:

| Required composition | Cases | Arm B resolves |
|---|--:|--:|
| `single_fragment` (control) | 8 | **8** |
| `fragment_pair` | 20 | 0 |
| `fragment_triple` | 6 | 0 |
| `fragment_and_breakup_amount` | 10 | 0 |
| `fragment_and_value_date` | 4 | 0 |

The control family matters as much as the zeros. Without a family the existing
architecture *can* solve, "nothing resolved" is indistinguishable from "the
harness is broken", and the pilot would carry no evidence that its own
plumbing works.

The structural claim behind the zeros is asserted per case, exhaustively, over
every substring of length 4–20 against every candidate under the real declared
relations (`tests/test_benchmark_v4_pilot.py::TestCompositionalStructure`).
The upper bound of 20 is a bound rather than a sample: the generator separately
asserts that no candidate reference appears in the narration (literally or
after separator stripping) and that no mask character appears, which makes
`contains_reference`, `separator_normalized_equal`, `mask_consistent` and
`exact` unreachable at any length, leaving only relations that require the
fragment to be no longer than the reference.

---

## 3. v4 is also fully solvable deterministically, and that was predictable

**Severity: material to whether v4 is worth freezing. Reported as required.**

Arm C2 — the same features composed under a "consistent with everything" rule
— resolves **48 of 48** resolvable cases, correctly, with zero wrong
resolutions and zero at risk.

This is close to a tautology and should be read as one. C2 composes exactly the
feature vocabulary the generator uses to *define* its cases: reach sets under
the declared relations, break-up line amounts, settlement dates. A benchmark
whose difficulty is assembled from a declared, finite set of mechanical
features is solvable by exhaustively composing that same set.

Raising the conjunction arity does not change this. It changes an exponent.
The `conjunction_triple` family is the demonstration: it defeats a strictly
pairwise solver and falls to a three-wise one, and there is no reason to think
a four-wise family would behave differently.

**So the honest statement about the C-vs-D ablation is that v4 does not restore
room for a model to beat a deterministic arm on final accuracy, and no further
benchmark design along this axis will.** What v4 *does* isolate, which v3 did
not, is finding 1 — a capability gap in the shipped decision layer, measured
for zero tokens.

Three things this does **not** license:

- Weakening the declared relation set so the deterministic arm fails. That
  trades correctly reconciled money for a better-looking ablation, which
  DESIGN.md §1 rules out in its first paragraph, and `notes/STAGE3-FINDINGS.md`
  §1 already refused for v3.
- Making the search space merely *larger* — longer narrations, more candidates
  — so exhaustion becomes expensive rather than impossible. That measures
  compute, not reasoning, and the pilot brief forbids it by name.
- Encrypting, obscuring or hiding a mapping. Same reason.

If a benchmark on which a model can beat a deterministic composer is wanted,
the evidence has to stop being drawn from a declared feature vocabulary — which
means real narrations with real degradations nobody enumerated, and that is a
data-sourcing problem rather than a generator problem.

---

## 4. Lexical composition without a consistency rule is not safer, only broader

**Severity: material. A safety result, and the sharpest thing the pilot found
about how to build the missing capability.**

Four arms, one archetype, three different answers:

| Arm | Rule | `conflict_stale_reference` (4 cases) | Wrong overall | ₹ at risk |
|---|---|---|---:|---:|
| B | one discriminating fragment | resolves all 4 | 4 | 1,34,45,282 paise |
| C1 | lexical, consistent-with-everything | resolves all 4 | 4 | 1,34,45,282 paise |
| C2 | lexical **+ structural**, consistent-with-everything | escalates all 4 | **0** | **0** |
| C3 | lexical + structural, first-subset-that-isolates | resolves all 4 | 4 | 1,34,45,282 paise |

The archetype is a stale reference tail from a settlement that is not the
counterparty, plus a value-date field agreeing with two *different* candidates.
No candidate is consistent with all of the evidence, so escalation is the only
correct outcome.

Two lessons, and the second is the one that would have been easy to miss.

**Adding composition is not automatically adding safety.** C1 nearly
quadruples arm B's coverage and carries exactly the same four unsafe
auto-matches and exactly the same value at risk. Composing more of the same
*kind* of evidence made the arm broader, not more careful.

**The rule matters more than the features.** C2 and C3 have identical
features. C2 escalates all four; C3 resolves all four wrongly. The difference
is "exactly one candidate is consistent with everything" versus "some
combination points here" — and the second is first-positive-match wearing a
conjunction's clothes. It is also the rule most people write first.

C2 pays for that: it is the only arm that escalates the four stale cases, and
it does so because a contradicting feature empties its intersection. That is
the same stance DESIGN.md §4.3 takes when it makes "more than one candidate
satisfies the predicates" a hard blocker, arrived at from the other direction.

---

## 5. Recommendation

**Do not proceed to a full frozen v4 yet. Do not spend model tokens on the
pilot as it stands.**

The reasoning, in order of weight:

1. **The pilot has already produced its most valuable result, deterministically
   and for nothing.** Finding 1 is a capability gap in the shipped decision
   layer. A live run over the pilot today would produce 48 false escalations
   and tell us what finding 1 already tells us, at token cost.
2. **A frozen v4 would freeze a benchmark a deterministic arm solves
   completely.** Finding 3. Freezing it invites exactly the reading the freeze
   protocol exists to prevent — a headline number against a set whose ceiling
   is a 200-line composer.
3. **The next decision is about the validator, not the benchmark.** Either the
   decision layer gains an evidence-intersection rule (`benchmark/V4-PILOT.md`
   §9) or it does not. If it does, the pilot becomes a real regression suite
   for it overnight and the same 64 cases are worth running live. If it does
   not, then v4's resolvable families are unreachable by construction and there
   is nothing for a model to be measured on.

Concretely, in order:

- **Take the validator decision first.** ~~The minimal version — intersect
  the reach sets of the fragments the agent actually tested~~ **Done, and not
  that way.** Intersecting the agent's own fragments turned out to be unsafe:
  it lets one narration prove three different candidates depending on which
  pair the agent tests. `validator.v2` intersects the deterministic *closure*
  instead, seeded by the agent's evidence, and moved 26 of the pilot's 48
  resolvable cases into reach with no new wrong resolution. See
  `notes/VALIDATOR-V2-FINDINGS.md` §1 and §3.
- **Decide the structural relations separately.** The remaining 14 cases need a
  narration-token-to-break-up-amount relation and a
  narration-token-to-settlement-date relation. That genuinely widens what
  counts as evidence and should not ride along with the intersection rule.
- **Then, and only then, run the pilot live** — 64 cases, one provider, one
  model, trajectories committed — and compare against arms B, C1 and C2 on the
  identical cohort through `benchmark/eval`'s comparison mode.
- **Keep v3 as the safety and contract regression suite.** It is good at that,
  and `benchmark/V4-PILOT.md` §1 states exactly which of its claims survive.

If the validator decision goes the other way — no new capability — then the
right move is to report v3's ceiling honestly as
`notes/STAGE3-FINDINGS.md` §1 already recommends, ship arm C's architecture per
DESIGN.md §9's day-8 fallback, and let the pilot stand as the measurement that
justified the choice.
