"""Hand-built cases that attack a conjunctive reference rule.

The benchmark cannot host these. A v4-pilot case is a plausible
reconciliation scenario; these are deliberately pointed constructions whose
only job is to break a candidate rule, and putting them in a dataset would be
exactly the benchmark-gaming the pilot brief forbids. So they live here, beside
the rules they test, and they are used only by the experimental harness and its
tests.

The reference geometry
----------------------

Three references, chosen so that three narration spans give three overlapping
reach sets whose pairwise intersections are three *different* singletons:

.. code-block:: text

    A = AXISCN1137863727        head AXISCN11 ....... tail 863727
    B = AXISCN115842Q7K4        head AXISCN11 ....... tail Q7K4
    C = Q7K4M291863727          head Q7K4 ........... tail 863727

    "AXISCN11" -> {A, B}     prefix of A and B; C starts Q7K4
    "863727"   -> {A, C}     suffix of A and C; B ends Q7K4
    "Q7K4"     -> {B, C}     suffix of B and prefix of C; neither for A

    {A,B} & {A,C} = {A}      {A,B} & {B,C} = {B}      {A,C} & {B,C} = {C}
    all three            = {}

So one narration carrying all three spans "proves" a different candidate for
each pair of clues a model might happen to test, and proves nothing at all
when read completely. That is the cherry-picking attack in its sharpest form,
and no rule that intersects only the model's selection can survive it.

Every span is bounded by a non-alphanumeric character in every narration below,
so a span cannot be extended into a longer prefix or suffix, and the shared
runs are cut so that their own sub-fragments do not become one-sided. The
resulting reach sets are asserted rather than assumed --
``tests/test_validator_conjunction.py`` checks each fixture's closure against
the geometry stated here.

All three candidates are financially exact and drawn from exact-total blocking,
so the money never confounds a reference result: whatever a rule does here, it
does on the strength of the reference evidence alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from finrecon.candidates.generator import BLOCKING_RULE_EXACT_TOTAL, candidate_id_for
from finrecon.candidates.snapshot import (
    BankRecordFacts,
    BaseEvidence,
    CandidateRecord,
    CaseSnapshot,
    SettlementFacts,
    build_case_snapshot,
)
from finrecon.matchers.evidence import (
    BreakupLineEvidence,
    DecisionEvidence,
    SettlementDerivation,
)
from finrecon.models import BankRecordDirection
from finrecon.normalize.provenance import SourceProvenance

UTC = timezone.utc
WHEN = datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC)
VALUE_DATE = date(2026, 5, 12)

NET_PAISE = 1_284_500
FEE_PAISE = -24_405
TAX_PAISE = -4_392

UTR_A = "AXISCN1137863727"
UTR_B = "AXISCN115842Q7K4"
UTR_C = "Q7K4M291863727"

SPAN_HEAD = "AXISCN11"
"""Reaches A and B."""

SPAN_TAIL = "863727"
"""Reaches A and C."""

SPAN_HINGE = "Q7K4"
"""Reaches B and C. The clue a cherry-picking model leaves untested."""

SPAN_LONG_A = "AXISCN113786"
"""A twelve-character prefix of A alone. Discriminating on its own."""

SPAN_LONG_B = "AXISCN115842"
"""A twelve-character prefix of B alone. Discriminating on its own."""

FABRICATED = "ZZZZCN1137863727"
"""Not a substring of any narration below. Must never participate."""

SETTLEMENT_A = "setl_adv_000001"
SETTLEMENT_B = "setl_adv_000002"
SETTLEMENT_C = "setl_adv_000003"
BANK_RECORD = "bnk_adv_000001"

_UTR_BY_SETTLEMENT = {
    SETTLEMENT_A: UTR_A,
    SETTLEMENT_B: UTR_B,
    SETTLEMENT_C: UTR_C,
}


def _derivation(settlement_id: str) -> SettlementDerivation:
    payment = NET_PAISE - FEE_PAISE - TAX_PAISE
    lines = (
        BreakupLineEvidence(
            line_type="payment",
            amount_paise=payment,
            reference_id=f"pay_adv_{settlement_id[-6:]}",
            reference_status="captured",
        ),
        BreakupLineEvidence(
            line_type="fee", amount_paise=FEE_PAISE, reference_id=None, reference_status=None
        ),
        BreakupLineEvidence(
            line_type="tax", amount_paise=TAX_PAISE, reference_id=None, reference_status=None
        ),
    )
    total = sum(line.amount_paise for line in lines)
    return SettlementDerivation(
        settlement_id=settlement_id,
        settlement_amount_paise=NET_PAISE,
        breakup_total_paise=total,
        breakup_by_type=(("fee", FEE_PAISE), ("payment", payment), ("tax", TAX_PAISE)),
        lines=lines,
        unexplained_delta_paise=NET_PAISE - total,
        declared_adjustment_paise=0,
    )


def _facts(settlement_id: str) -> SettlementFacts:
    utr = _UTR_BY_SETTLEMENT[settlement_id]
    return SettlementFacts(
        settlement_id=settlement_id,
        utr=utr,
        utr_key=utr.upper(),
        amount_paise=NET_PAISE,
        created_at_utc=WHEN,
        settlement_date_utc=VALUE_DATE,
        derivation=_derivation(settlement_id),
        source=SourceProvenance(record_type="settlement", record_id=settlement_id),
    )


def _candidate(settlement_id: str) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=candidate_id_for(BANK_RECORD, (settlement_id,)),
        settlement_ids=(settlement_id,),
        total_paise=NET_PAISE,
        blocking_rule=BLOCKING_RULE_EXACT_TOTAL,
        unexplained_delta_paise=0,
        settlement_dates=(VALUE_DATE,),
    )


def candidate_id_of(settlement_id: str) -> str:
    """The Stage-2 candidate ID a fixture settlement sits behind."""
    return candidate_id_for(BANK_RECORD, (settlement_id,))


CANDIDATE_A = candidate_id_of(SETTLEMENT_A)
CANDIDATE_B = candidate_id_of(SETTLEMENT_B)
CANDIDATE_C = candidate_id_of(SETTLEMENT_C)


def snapshot_for(narration: str, settlements: tuple[str, ...] = ()) -> CaseSnapshot:
    """One adversarial case: a chosen narration over a chosen candidate set."""
    settlements = settlements or (SETTLEMENT_A, SETTLEMENT_B, SETTLEMENT_C)
    candidates = tuple(_candidate(settlement_id) for settlement_id in settlements)
    facts = tuple(_facts(settlement_id) for settlement_id in settlements)
    base = BaseEvidence(
        bank_record=BankRecordFacts(
            bank_record_id=BANK_RECORD,
            amount_paise=NET_PAISE,
            direction=BankRecordDirection.CREDIT,
            narration=narration,
            reference_tokens=tuple(
                token for token in narration.replace("/", " ").replace("-", " ").split() if token
            ),
            value_date=VALUE_DATE,
            source=SourceProvenance(record_type="bank_record", record_id=BANK_RECORD),
        ),
        settlement_facts=facts,
        decision_evidence=DecisionEvidence(
            considered_settlement_ids=tuple(settlements),
            competing_solution_ids=tuple((s,) for s in settlements),
        ),
        blocking=(("max_settlement_group_size", "2"),),
    )
    return build_case_snapshot(
        case_id=f"case:adv:{abs(hash(narration)) % 10**8:08d}",
        batch_id="batch:adversarial",
        bank_record_id=BANK_RECORD,
        unresolved_rule_id="unresolved.multiple_derived_candidates",
        unresolved_matcher_id="derived_reconciliation.v1",
        candidates=candidates,
        base_evidence=base,
    )


@dataclass(frozen=True)
class AdversarialCase:
    """One attack, with what the agent tested and what must happen."""

    name: str
    attack: str
    narration: str
    model_fragments: tuple[str, ...]
    """Exactly what the agent surfaced, in the order it surfaced it."""
    must_resolve_to: str | None
    """The candidate ID a safe rule may resolve to, or ``None`` for escalation."""
    why: str

    def snapshot(self) -> CaseSnapshot:
        return snapshot_for(self.narration)


ADVERSARIAL_CASES: tuple[AdversarialCase, ...] = (
    AdversarialCase(
        name="cherry_picking",
        attack="omission",
        narration=f"NEFT CR-RZRPAY-{SPAN_HEAD}/BATCH47/{SPAN_TAIL}/{SPAN_HINGE}-MUM",
        model_fragments=(SPAN_HEAD, SPAN_TAIL),
        must_resolve_to=None,
        why=(
            "The agent tested the two clues that intersect on A and skipped the one "
            "that rules A out. Read completely the narration proves nothing, and the "
            "same narration would have 'proved' B or C for a different pair."
        ),
    ),
    AdversarialCase(
        name="cherry_picking_toward_b",
        attack="omission",
        narration=f"NEFT CR-RZRPAY-{SPAN_HEAD}/BATCH47/{SPAN_TAIL}/{SPAN_HINGE}-MUM",
        model_fragments=(SPAN_HEAD, SPAN_HINGE),
        must_resolve_to=None,
        why=(
            "Same narration, a different pair, a different false proof. Included "
            "because a rule that only fails the first ordering is not safe, it is "
            "lucky."
        ),
    ),
    AdversarialCase(
        name="cherry_picking_toward_c",
        attack="omission",
        narration=f"NEFT CR-RZRPAY-{SPAN_HEAD}/BATCH47/{SPAN_TAIL}/{SPAN_HINGE}-MUM",
        model_fragments=(SPAN_TAIL, SPAN_HINGE),
        must_resolve_to=None,
        why="The third false proof from the same line.",
    ),
    AdversarialCase(
        name="duplicate_cannot_strengthen",
        attack="duplicate evidence",
        narration=f"NEFT CR-RZRPAY-{SPAN_HEAD}/BATCH47-MUM",
        model_fragments=(SPAN_HEAD, SPAN_HEAD, SPAN_HEAD),
        must_resolve_to=None,
        why=(
            "One clue reaching two candidates, offered three times. Repetition is not "
            "corroboration, and two candidates remain."
        ),
    ),
    AdversarialCase(
        name="overlapping_slices_of_one_span",
        attack="false independence",
        narration=f"NEFT CR-RZRPAY-{SPAN_TAIL}/BATCH47-MUM",
        model_fragments=("863727", "63727", "3727"),
        must_resolve_to=None,
        why=(
            "Three overlapping cuts of the same six-character run. One clue read "
            "three ways is one clue, and it reaches two candidates."
        ),
    ),
    AdversarialCase(
        name="generic_wrapper_plus_specific",
        attack="false independence",
        narration=f"RZPY/SETL/{SPAN_HEAD}/BATCH47-MUM",
        model_fragments=("SETL", SPAN_HEAD),
        must_resolve_to=None,
        why=(
            "'SETL' prefixes every canonical settlement ID at once, so it separates "
            "nothing. Pairing it with a clue reaching two candidates must not "
            "manufacture a third fact."
        ),
    ),
    AdversarialCase(
        name="stale_strong_reference_plus_hinge",
        attack="stale reference, reference-inconsistent",
        narration=f"NEFT CR-RZRPAY-{SPAN_LONG_A}/BATCH47/{SPAN_HINGE}-MUM",
        model_fragments=(SPAN_LONG_A,),
        must_resolve_to=None,
        why=(
            "A twelve-character prefix of A looks conclusive and, under the original "
            "single-fragment rule, is. The same line also carries a clue consistent "
            "only with B and C, so the reference evidence contradicts itself and the "
            "case has to escalate. This is the one fixture where validator v1 "
            "resolves and v2 must not."
        ),
    ),
    AdversarialCase(
        name="contradiction_before",
        attack="contradiction monotonicity (baseline)",
        narration=f"NEFT CR-RZRPAY-{SPAN_LONG_A}/BATCH47-MUM",
        model_fragments=(SPAN_LONG_A,),
        must_resolve_to=CANDIDATE_A,
        why=(
            "The control half of the monotonicity pair: one discriminating clue, "
            "nothing contradicting it, so A is genuinely identified."
        ),
    ),
    AdversarialCase(
        name="contradiction_after",
        attack="contradiction monotonicity",
        narration=f"NEFT CR-RZRPAY-{SPAN_LONG_A}/BATCH47/{SPAN_LONG_B}-MUM",
        model_fragments=(SPAN_LONG_A, SPAN_LONG_B),
        must_resolve_to=None,
        why=(
            "The same case plus one more valid clue, which points elsewhere. Adding "
            "contradictory evidence must destroy the match, never leave it standing."
        ),
    ),
    AdversarialCase(
        name="contradiction_after_untested",
        attack="contradiction monotonicity, by omission",
        narration=f"NEFT CR-RZRPAY-{SPAN_LONG_A}/BATCH47/{SPAN_LONG_B}-MUM",
        model_fragments=(SPAN_LONG_A,),
        must_resolve_to=None,
        why=(
            "As above, except the agent does not test the contradicting clue. The "
            "contradiction is in the narration either way, so the outcome must be "
            "the same."
        ),
    ),
    AdversarialCase(
        name="fabricated_only",
        attack="fabrication",
        narration=f"NEFT CR-RZRPAY-{SPAN_HEAD}/BATCH47-MUM",
        model_fragments=(FABRICATED,),
        must_resolve_to=None,
        why=(
            "The agent reports a reference that is not in the narration and that "
            "would discriminate if believed. It must be inadmissible, and an agent "
            "whose only contribution is inadmissible has gathered nothing."
        ),
    ),
    AdversarialCase(
        name="fabricated_plus_real",
        attack="fabrication",
        narration=f"NEFT CR-RZRPAY-{SPAN_HEAD}/BATCH47-MUM",
        model_fragments=(FABRICATED, SPAN_HEAD),
        must_resolve_to=None,
        why=(
            "A fabricated discriminator alongside a real clue that reaches two "
            "candidates. If the fabrication were believed the case would resolve to "
            "A; it must not participate, and what remains does not isolate anything."
        ),
    ),
    AdversarialCase(
        name="two_candidate_clean_resolution",
        attack="none (regression control)",
        narration=f"NEFT CR-RZRPAY-{SPAN_LONG_A}/BATCH47-MUM",
        model_fragments=(SPAN_LONG_A,),
        must_resolve_to=CANDIDATE_A,
        why=(
            "The benchmark v3 T2 shape, restated as a fixture: one clue, one "
            "candidate, nothing against it. A rule that loses this loses 200 DEV "
            "cases with it."
        ),
    ),
    AdversarialCase(
        name="conjunction_clean_resolution",
        attack="none (capability control)",
        narration=f"NEFT CR-RZRPAY-{SPAN_HEAD}/BATCH47/{SPAN_TAIL}-MUM",
        model_fragments=(SPAN_HEAD, SPAN_TAIL),
        must_resolve_to=CANDIDATE_A,
        why=(
            "The capability this whole change exists for: two clues, neither "
            "conclusive alone, and no third clue contradicting them. Without this "
            "fixture a rule that escalates everything would score perfectly."
        ),
    ),
)

ADVERSARIAL_BY_NAME = {case.name: case for case in ADVERSARIAL_CASES}


__all__ = [
    "ADVERSARIAL_BY_NAME",
    "ADVERSARIAL_CASES",
    "CANDIDATE_A",
    "CANDIDATE_B",
    "CANDIDATE_C",
    "FABRICATED",
    "SETTLEMENT_A",
    "SETTLEMENT_B",
    "SETTLEMENT_C",
    "SPAN_HEAD",
    "SPAN_HINGE",
    "SPAN_LONG_A",
    "SPAN_LONG_B",
    "SPAN_TAIL",
    "UTR_A",
    "UTR_B",
    "UTR_C",
    "AdversarialCase",
    "candidate_id_of",
    "snapshot_for",
]
