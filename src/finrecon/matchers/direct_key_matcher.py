"""Direct-key reconciliation — the deterministic exact-join stage.

DESIGN.md §5.2 defines a direct key as one that *survives*: an intact UTR
or a clean ``settlement_id`` link. This matcher resolves exactly those
credits and nothing else.

The rule, in full:

    A bank credit reconciles to a settlement when some whole token of the
    raw narration is exactly equal — after case folding only — to that
    settlement's UTR or settlement ID, exactly one settlement in the batch
    is reached that way, and the credit's amount equals the settlement's
    amount to the paise.

What makes this safe is what it *cannot* do. Comparison is whole-token
equality, so a truncated, masked, separator-altered, reordered or
prefix-glued reference simply is not equal and never matches here — the
entire DESIGN.md §5.2 degradation ladder is out of reach by construction,
which is the intent: degraded-reference recovery belongs to a later stage,
not to this one. There is no substring search, no edit distance, no token
similarity, and no "close enough".

Two or more settlements reachable by exact token match is a refusal, not a
tie-break. So is an amount that disagrees with the settlement by any
amount, including one paise (DESIGN.md §4.3).
"""

from __future__ import annotations

from finrecon.matchers.derivation import breakup_references_are_sound, derive_group
from finrecon.matchers.evidence import DecisionEvidence, ReferenceEvidence
from finrecon.matchers.result import DecisionStatus, ReconciliationDecision
from finrecon.matchers.rules import (
    DIRECT_KEY_MATCHER_ID,
    RULE_DIRECT_KEY_EXACT_TOKEN,
    RULE_UNRESOLVED_MULTIPLE_DIRECT_KEYS,
    RULE_UNRESOLVED_NO_CANDIDATE,
    RULE_UNRESOLVED_UNEXPLAINED_DELTA,
)
from finrecon.normalize.records import (
    NormalizedBankRecord,
    NormalizedBatch,
    NormalizedSettlement,
)


class DirectKeyIndex:
    """Exact-match lookup from an identifier comparison key to settlements.

    A key mapping to more than one settlement is kept, not dropped: the
    matcher must be able to *see* the collision in order to refuse it.
    """

    def __init__(self, settlements: tuple[NormalizedSettlement, ...]) -> None:
        self._by_key: dict[str, list[tuple[str, str, NormalizedSettlement]]] = {}
        for settlement in settlements:
            self._add(settlement.settlement_id_key, "settlement_id", settlement.settlement_id, settlement)
            if settlement.utr_key is not None:
                self._add(settlement.utr_key, "utr", settlement.utr_key, settlement)

    def _add(self, key: str, kind: str, value: str, settlement: NormalizedSettlement) -> None:
        self._by_key.setdefault(key, []).append((kind, value, settlement))

    def lookup(self, token_key: str) -> list[tuple[str, str, NormalizedSettlement]]:
        return self._by_key.get(token_key, [])


def _reference_hits(
    bank_record: NormalizedBankRecord, index: DirectKeyIndex
) -> tuple[dict[str, NormalizedSettlement], tuple[ReferenceEvidence, ...]]:
    reached: dict[str, NormalizedSettlement] = {}
    evidence: list[ReferenceEvidence] = []
    for token, key in zip(bank_record.reference_tokens, bank_record.reference_token_keys):
        for kind, value, settlement in index.lookup(key):
            reached[settlement.settlement_id] = settlement
            evidence.append(
                ReferenceEvidence(
                    matched_token=token,
                    matched_token_key=key,
                    identifier_kind=kind,  # type: ignore[arg-type]
                    identifier_value=value,
                    settlement_id=settlement.settlement_id,
                )
            )
    # Deterministic ordering independent of narration token order.
    evidence.sort(key=lambda e: (e.settlement_id, e.identifier_kind, e.matched_token_key))
    return reached, tuple(evidence)


def match_direct_key(
    bank_record: NormalizedBankRecord,
    batch: NormalizedBatch,
    index: DirectKeyIndex,
    case_id: str,
) -> ReconciliationDecision:
    """Attempt exact-identifier reconciliation for one bank credit."""
    reached, references = _reference_hits(bank_record, index)
    considered = tuple(sorted(reached))

    if not reached:
        return ReconciliationDecision(
            case_id=case_id,
            bank_record_id=bank_record.bank_record_id,
            status=DecisionStatus.UNRESOLVED,
            matcher_id=DIRECT_KEY_MATCHER_ID,
            rule_id=RULE_UNRESOLVED_NO_CANDIDATE,
            evidence=DecisionEvidence(references=references),
        )

    if len(reached) > 1:
        return ReconciliationDecision(
            case_id=case_id,
            bank_record_id=bank_record.bank_record_id,
            status=DecisionStatus.UNRESOLVED,
            matcher_id=DIRECT_KEY_MATCHER_ID,
            rule_id=RULE_UNRESOLVED_MULTIPLE_DIRECT_KEYS,
            evidence=DecisionEvidence(
                references=references,
                considered_settlement_ids=considered,
                competing_solution_ids=tuple((sid,) for sid in considered),
            ),
        )

    settlement = reached[considered[0]]
    payments = batch.payment_by_id()
    refunds = batch.refund_by_id()
    money = derive_group(bank_record, (settlement,), payments, refunds)

    if not money.is_exact or not breakup_references_are_sound(settlement, payments, refunds):
        return ReconciliationDecision(
            case_id=case_id,
            bank_record_id=bank_record.bank_record_id,
            status=DecisionStatus.UNRESOLVED,
            matcher_id=DIRECT_KEY_MATCHER_ID,
            rule_id=RULE_UNRESOLVED_UNEXPLAINED_DELTA,
            evidence=DecisionEvidence(
                references=references,
                money=money,
                considered_settlement_ids=considered,
            ),
        )

    return ReconciliationDecision(
        case_id=case_id,
        bank_record_id=bank_record.bank_record_id,
        status=DecisionStatus.RESOLVED,
        matcher_id=DIRECT_KEY_MATCHER_ID,
        rule_id=RULE_DIRECT_KEY_EXACT_TOKEN,
        settlement_ids=(settlement.settlement_id,),
        relationship="one_to_one",
        evidence=DecisionEvidence(
            references=references,
            money=money,
            considered_settlement_ids=considered,
        ),
    )
