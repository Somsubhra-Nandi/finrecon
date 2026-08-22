"""Deterministic Stage-2 matchers.

DESIGN.md §8 sketches this package as ``tier1_exact.py`` /
``tier2_tolerance.py``, which names *pipeline stages*. Because the
benchmark separately uses T0–T3 for *case difficulty*, the modules here
are named for what they do instead — :mod:`direct_key_matcher` and
:mod:`derived_reconciliation` — so no reader has to work out which
numbering a "tier 2" refers to. The pipeline order is unchanged.
"""

from .blocking import (
    SettlementGroup,
    enumerate_exact_groups,
    index_by_settlement_date,
    settlements_in_window,
    window_dates,
)
from .derivation import breakup_references_are_sound, derive_group, derive_settlement
from .derived_reconciliation import match_derived, provable_groups, withdraw_contended
from .direct_key_matcher import DirectKeyIndex, match_direct_key
from .evidence import (
    BreakupLineEvidence,
    DateWindowEvidence,
    DecisionEvidence,
    MoneyDerivation,
    ReferenceEvidence,
    SettlementDerivation,
)
from .result import DecisionStatus, ReconciliationDecision

__all__ = [
    "SettlementGroup",
    "enumerate_exact_groups",
    "index_by_settlement_date",
    "settlements_in_window",
    "window_dates",
    "breakup_references_are_sound",
    "derive_group",
    "derive_settlement",
    "match_derived",
    "provable_groups",
    "withdraw_contended",
    "DirectKeyIndex",
    "match_direct_key",
    "BreakupLineEvidence",
    "DateWindowEvidence",
    "DecisionEvidence",
    "MoneyDerivation",
    "ReferenceEvidence",
    "SettlementDerivation",
    "DecisionStatus",
    "ReconciliationDecision",
]
