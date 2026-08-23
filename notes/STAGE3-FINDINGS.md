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
