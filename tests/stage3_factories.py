"""Hand-built case snapshots for Stage-3 unit tests.

The frozen benchmark deliberately does not contain the cases these tests
need -- a candidate whose break-up is one paise short, a credit worth
Rs 6,00,000, two candidates a single fragment reaches equally -- and the
benchmark is frozen, so they are constructed here instead of generated.

Every snapshot is sealed through
:func:`finrecon.candidates.snapshot.build_case_snapshot`, the same
constructor the Stage-2 pipeline uses, so a test case carries an honest
content hash and ``verify_integrity()`` means the same thing it does in
production.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from finrecon.candidates.generator import (
    BLOCKING_RULE_DATE_WINDOW_ONLY,
    BLOCKING_RULE_EXACT_TOTAL,
    candidate_id_for,
)
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
WHEN = datetime(2026, 4, 2, 10, 0, 0, tzinfo=UTC)
VALUE_DATE = date(2026, 4, 2)

NET_PAISE = 4_187_450
GROSS_PAISE = 4_267_000
FEE_PAISE = -67_390
TAX_PAISE = -12_160


def derivation(
    settlement_id: str,
    *,
    amount: int = NET_PAISE,
    payment_status: str = "captured",
    breakup_delta: int = 0,
) -> SettlementDerivation:
    """A sound payment/fee/tax break-up, optionally short by ``breakup_delta``."""
    payment = amount - FEE_PAISE - TAX_PAISE - breakup_delta
    lines = (
        BreakupLineEvidence(
            line_type="payment",
            amount_paise=payment,
            reference_id=f"pay_{settlement_id}",
            reference_status=payment_status,
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
        settlement_amount_paise=amount,
        breakup_total_paise=total,
        breakup_by_type=(("fee", FEE_PAISE), ("payment", payment), ("tax", TAX_PAISE)),
        lines=lines,
        unexplained_delta_paise=amount - total,
        declared_adjustment_paise=0,
    )


def settlement_facts(
    settlement_id: str,
    utr: str | None,
    *,
    amount: int = NET_PAISE,
    payment_status: str = "captured",
    breakup_delta: int = 0,
) -> SettlementFacts:
    return SettlementFacts(
        settlement_id=settlement_id,
        utr=utr,
        utr_key=utr.upper() if utr else None,
        amount_paise=amount,
        created_at_utc=WHEN,
        settlement_date_utc=WHEN.date(),
        derivation=derivation(
            settlement_id,
            amount=amount,
            payment_status=payment_status,
            breakup_delta=breakup_delta,
        ),
        source=SourceProvenance(record_type="settlement", record_id=settlement_id),
    )


def candidate(
    bank_record_id: str,
    settlement_ids: tuple[str, ...],
    *,
    total: int = NET_PAISE,
    delta: int = 0,
    blocking_rule: str = BLOCKING_RULE_EXACT_TOTAL,
) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=candidate_id_for(bank_record_id, settlement_ids),
        settlement_ids=settlement_ids,
        total_paise=total,
        blocking_rule=blocking_rule,
        unexplained_delta_paise=delta,
        settlement_dates=tuple(WHEN.date() for _ in settlement_ids),
    )


def snapshot_of(
    *,
    narration: str,
    settlements: tuple[SettlementFacts, ...],
    bank_amount: int = NET_PAISE,
    bank_record_id: str = "bnk_test_0001",
    case_id: str | None = None,
    batch_id: str = "batch:test",
    candidate_delta: int = 0,
    blocking_rule: str = BLOCKING_RULE_EXACT_TOTAL,
) -> CaseSnapshot:
    """One unresolved case: one candidate per settlement, all single-settlement."""
    case_id = case_id or f"case:{bank_record_id}"
    candidates = tuple(
        candidate(
            bank_record_id,
            (facts.settlement_id,),
            total=facts.amount_paise,
            delta=candidate_delta,
            blocking_rule=blocking_rule,
        )
        for facts in settlements
    )
    base = BaseEvidence(
        bank_record=BankRecordFacts(
            bank_record_id=bank_record_id,
            amount_paise=bank_amount,
            direction=BankRecordDirection.CREDIT,
            narration=narration,
            reference_tokens=tuple(narration.replace("/", " ").replace("-", " ").split()),
            value_date=VALUE_DATE,
            source=SourceProvenance(record_type="bank_record", record_id=bank_record_id),
        ),
        settlement_facts=settlements,
        decision_evidence=DecisionEvidence(),
        blocking=(("generator_id", "candidate_generator.v1"),),
    )
    return build_case_snapshot(
        case_id=case_id,
        batch_id=batch_id,
        bank_record_id=bank_record_id,
        unresolved_rule_id="unresolved.multiple_derived_candidates",
        unresolved_matcher_id="derived_reconciliation.v1",
        candidates=candidates,
        base_evidence=base,
        )


TRUE_SETTLEMENT_ID = "setl_bravo"
"""The settlement the masked narration below actually points at."""

OTHER_SETTLEMENT_ID = "setl_alpha"
"""The one it does not. Named neutrally on purpose: a test asserts the case
briefing leaks no ground truth, and an ID reading ``setl_decoy`` would make
that assertion pass or fail on the fixture's naming rather than on the
production code it is checking."""

TRUE_UTR = "PF1CEIYFJVQ"
DECOY_UTR = "EQPJ4E94BAD7U4Y"
MASKED_NARRATION = "RTGS CR REF PF*******VQ RAZORPAY SOFTWARE"


def two_candidate_snapshot(**overrides) -> CaseSnapshot:
    """The canonical shape: a masked reference plus one unrelated settlement."""
    settlements = overrides.pop(
        "settlements",
        (
            settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR),
            settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR),
        ),
    )
    return snapshot_of(
        narration=overrides.pop("narration", MASKED_NARRATION),
        settlements=settlements,
        **overrides,
    )


def no_reference_snapshot(**overrides) -> CaseSnapshot:
    """Two indistinguishable settlements, neither carrying a UTR. Must escalate."""
    return snapshot_of(
        narration=overrides.pop("narration", "NEFT CREDIT - SETTLEMENT"),
        settlements=overrides.pop(
            "settlements",
            (settlement_facts("setl_aaa", None), settlement_facts("setl_bbb", None)),
        ),
        **overrides,
    )


__all__ = [
    "BLOCKING_RULE_DATE_WINDOW_ONLY",
    "BLOCKING_RULE_EXACT_TOTAL",
    "DECOY_UTR",
    "MASKED_NARRATION",
    "NET_PAISE",
    "OTHER_SETTLEMENT_ID",
    "TRUE_SETTLEMENT_ID",
    "TRUE_UTR",
    "VALUE_DATE",
    "WHEN",
    "candidate",
    "derivation",
    "no_reference_snapshot",
    "settlement_facts",
    "snapshot_of",
    "two_candidate_snapshot",
]
