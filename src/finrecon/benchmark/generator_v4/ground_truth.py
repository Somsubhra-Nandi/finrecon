"""Hidden ground truth for the benchmark v4 pilot.

A separate model from :class:`finrecon.benchmark.generator.ground_truth.
GroundTruthCase`, and separate on purpose rather than by preference. That
class is frozen with ``extra="forbid"``, and its ``model_dump`` is what the
v3 FROZEN-EVAL fingerprint is computed over. Adding a ``families`` field to
it would add one key to all 890 v3 ground-truth lines and change a hash the
project treats as immutable. So v4 states its own shape here, and v3's file
is never opened by this package.

What v4 adds beyond v3's schema
-------------------------------

``families``
    Descriptive tags for offline analysis. Several per case.
``required_composition``
    The smallest evidence combination that separates the true candidate.
    This is the field that turns "the system escalated" into "the system
    escalated a case needing a capability it does not have".
``expected_candidate_count``
    What the generator built. Stage-2's actual candidate count is checked
    against it, so a case whose candidate set silently widened (because
    another case's settlement landed in the window) fails generation rather
    than quietly changing difficulty.
``discriminating_clues``
    The exact narration fragments the construction relies on, and the
    candidate set each one reaches. Recorded so a diagnostic can state
    *which* clue a case turns on instead of re-deriving it, exactly as
    benchmark v2 recorded ``surviving_evidence``.
``structural_discriminator``
    For the non-lexical archetypes: the break-up amount or settlement date
    that carries the second half of the composition.

Everything here is hidden. Nothing on the reconciliation path may read it,
which ``tests/test_benchmark_isolation.py`` asserts structurally.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

RequiredOutcome = Literal["AUTO_RESOLVABLE", "ESCALATE"]


class ClueRecord(BaseModel):
    """One narration fragment the construction depends on, and what it reaches."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fragment: str
    """The literal substring of the narration a recovery step would work from."""
    clue_kind: str
    """``head_prefix`` / ``tail_suffix`` / ``reordered`` / ``long_prefix`` / ``stale_tail``."""
    reaches_settlement_ids: tuple[str, ...]
    """Every settlement whose reference this fragment stands in a declared relation to."""


class StructuralDiscriminator(BaseModel):
    """The non-lexical half of a composition, stated as data.

    ``kind`` says which feature; ``value`` is the literal the narration
    carries; ``reaches_settlement_ids`` is the candidate set that feature
    selects. A case is resolvable when the lexical reach set and this reach
    set intersect in exactly one settlement.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["breakup_line_amount_paise", "settlement_value_date"]
    value: str
    narration_token: str
    """How the value appears in the bank narration, verbatim."""
    reaches_settlement_ids: tuple[str, ...]


class V4Relationship(BaseModel):
    """The correct link between a bank record and its settlement(s), when unique."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bank_record_id: str
    settlement_ids: tuple[str, ...]
    relationship: Literal["one_to_one", "many_to_one"]


class V4GroundTruthCase(BaseModel):
    """One hidden v4 ground-truth entry, keyed by ``case_id``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    benchmark: str = "v4-pilot"
    tier: str = "V4"
    """v4 has no tier gradient. The field exists because the Stage-4 cohort
    reconciler reports tier composition for every cohort and a missing key
    would read as an unknown tier rather than as a deliberate absence."""
    archetype: str
    families: tuple[str, ...]
    required_composition: str
    record_ids: dict[str, tuple[str, ...]]
    required_outcome: RequiredOutcome
    correct_relationship: V4Relationship | None
    true_reference: str | None
    distractor_settlement_ids: tuple[str, ...] = ()
    expected_candidate_count: int
    clues: tuple[ClueRecord, ...] = ()
    structural_discriminator: StructuralDiscriminator | None = None
    value_at_stake_paise: int

    def to_json_dict(self) -> dict:
        return self.model_dump(mode="json")


__all__ = [
    "ClueRecord",
    "RequiredOutcome",
    "StructuralDiscriminator",
    "V4GroundTruthCase",
    "V4Relationship",
]
