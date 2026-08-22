"""Declared deterministic rules, bounds and windows for the Stage-2 core.

Every constant here is a **declared** rule in the DESIGN.md §4.3 sense:
stated up front, applied uniformly, and recorded in the audit trail by the
rule ID that names it. Nothing in the reconciliation path may widen a
window, relax a bound, or invent a tolerance at runtime.

On terminology: DESIGN.md §8's file names (``tier1_exact.py`` /
``tier2_tolerance.py``) refer to *pipeline stages*, while the benchmark's
T0/T1/T2/T3 label *case difficulty tiers*. Those two numberings do not
line up, so this package uses explicit names instead —
``direct_key_matcher`` and ``derived_reconciliation`` — and never the word
"tier" for a pipeline stage.

**The matchers are tier-blind.** A case's benchmark tier lives only in the
hidden ground truth, so no rule below can consult it, and none tries to.
Each rule is applied uniformly to every bank record in the batch.
"""

from __future__ import annotations

from typing import Final

# --- Matcher identities ---------------------------------------------------

DIRECT_KEY_MATCHER_ID: Final = "direct_key.v1"
DERIVED_MATCHER_ID: Final = "derived_reconciliation.v1"
CANDIDATE_GENERATOR_ID: Final = "candidate_generator.v1"

# --- Rule identities recorded on every decision ---------------------------

RULE_DIRECT_KEY_EXACT_TOKEN: Final = "direct_key.exact_identifier_token"
RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING: Final = "derived.exact_settlement_accounting"
RULE_UNRESOLVED_NO_CANDIDATE: Final = "unresolved.no_candidate"
RULE_UNRESOLVED_MULTIPLE_DIRECT_KEYS: Final = "unresolved.multiple_direct_key_candidates"
RULE_UNRESOLVED_MULTIPLE_DERIVED: Final = "unresolved.multiple_derived_candidates"
RULE_UNRESOLVED_COUNTERPARTY_CONTENTION: Final = "unresolved.counterparty_contention"
RULE_UNRESOLVED_UNEXPLAINED_DELTA: Final = "unresolved.unexplained_delta"

# --- Declared date window -------------------------------------------------

VALUE_DATE_WINDOW_DAYS_BEFORE: Final = 1
"""A bank value date may precede the settlement date by at most this many days.

Non-zero because a settlement group can legitimately straddle midnight:
the bank posts the credit against the first settlement's date while a
later member of the same group carries the next calendar date.
"""

VALUE_DATE_WINDOW_DAYS_AFTER: Final = 1
"""A bank value date may follow the settlement date by at most this many days."""

# --- Declared blocking bounds --------------------------------------------

MAX_SETTLEMENT_GROUP_SIZE: Final = 2
"""Largest settlement group the deterministic core will consider for one credit.

A declared bound, not a silent cap: a credit that can only be explained by
three or more settlements is reported unresolved rather than searched for,
and the bound is recorded on the case snapshot so an auditor can see the
search was bounded. Raising it enlarges the subset-sum search
super-linearly and — more importantly — enlarges the space of coincidental
sums, which trades precision for coverage. DESIGN.md's asymmetry (§1) says
do not make that trade.
"""

# --- Declared money invariants -------------------------------------------

MAX_UNEXPLAINED_DELTA_PAISE: Final = 0
"""DESIGN.md §4.3: "Any unexplained delta greater than 0 paise" is a hard blocker.

A delta is *explained* only when a settlement break-up line accounts for
it exactly. An explicit ``adjustment`` line of -1 paise explains one paise;
nothing explains a delta that no line represents. There is no tolerance
band here, and adding one would be the "probably rounding" rule DESIGN.md
forbids by name.
"""
