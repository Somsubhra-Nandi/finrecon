"""DEV / FROZEN-EVAL ground truth, loaded for offline scoring only.

Keying matches ``tests/conftest.py::dev_ground_truth`` exactly — Stage-2's
case identity is derived from the bank credit under reconciliation, so a
ground-truth row is reached through ``case_id_for(record_ids.bank_records[0])``
and never through the benchmark's own internal case ID.

**Split policy (DESIGN.md §5.1 step 7).** Build against DEV, report against
FROZEN. DEV truth is readable here without ceremony. FROZEN-EVAL truth is
gated behind an explicit opt-in, because the freeze protocol's whole value is
that held-out *outcomes* are not consulted while iterating. The gate is not
security — anyone can pass the flag — it is a speed bump that makes reading
held-out answers a decision someone made on purpose and can be asked about.

Note what this module cannot do: it has no provider, no decision path and no
write access to anything. It reads two files and returns dataclasses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from finrecon.pipeline import case_id_for

from benchmark.eval.errors import EvaluationInputError, GroundTruthPolicyError

SCORABLE_WITHOUT_OPT_IN = ("dev", "v4-pilot", "bounded-search-v1")
"""Splits whose truth may be read for scoring with no further ceremony.

``v4-pilot`` is here for the same reason ``dev`` is: it is a *development*
artifact. The freeze protocol's gate exists to stop held-out outcomes being
consulted while iterating, and a pilot that is still being designed has no
held-out status to protect. A frozen v4, if one is ever cut, would be a
different split name and would go into :data:`GATED_SPLITS`.
"""

GATED_SPLITS = ("frozen-eval",)
"""Splits whose truth requires an explicit opt-in. See module docstring."""


@dataclass(frozen=True)
class GroundTruthEntry:
    """One hidden answer, in the shape the evaluator actually consumes."""

    case_id: str
    tier: str
    archetype: str
    required_outcome: str
    correct_relationship: dict | None
    true_reference: str | None
    value_at_stake_paise: int
    families: tuple[str, ...] = ()
    """Benchmark v4 analysis tags. Empty for v1-v3 splits, which have no families.

    Defaulted rather than required so one loader serves both generations: a v3
    ground-truth line has no ``families`` key and must keep parsing unchanged,
    since re-reading v3 truth is how the evaluator's own regression tests pin
    that v4 changed nothing about v3 scoring.
    """
    required_composition: str = ""
    """The smallest evidence combination v4 says identifies the true candidate."""
    expected_candidate_count: int | None = None
    """What the v4 generator built. ``None`` for splits that do not record it."""

    @property
    def candidate_count_bucket(self) -> str:
        """A stable label for candidate-count reporting, or ``unknown``."""
        if self.expected_candidate_count is None:
            return "unknown"
        return str(self.expected_candidate_count)

    @property
    def is_uniquely_resolvable(self) -> bool:
        """True when a single correct answer exists.

        This is the §5.3 match-rate denominator: T3 cases are excluded by
        construction, because they have no uniquely resolvable ground truth.
        The property is derived from ``correct_relationship`` rather than from
        the tier label, so it stays correct if a tier's construction changes.
        """
        return self.correct_relationship is not None

    @property
    def expected_settlement_ids(self) -> tuple[str, ...]:
        """The correct settlement set, ordered exactly as the predicate compares it."""
        if self.correct_relationship is None:
            return ()
        return tuple(sorted(self.correct_relationship["settlement_ids"]))


def load_ground_truth(
    benchmark_dir: Path | str,
    split: str,
    *,
    allow_frozen_truth: bool = False,
) -> dict[str, GroundTruthEntry]:
    """Load one split's ground truth, keyed by Stage-2 case ID.

    ``allow_frozen_truth`` gates the held-out split and nothing else; it has
    no effect on DEV.
    """
    if split in GATED_SPLITS and not allow_frozen_truth:
        raise GroundTruthPolicyError(
            f"scoring against {split!r} ground truth is gated: it is the held-out "
            "artifact, and DESIGN.md 5.1 step 7 says build against DEV and report "
            "against FROZEN. Pass allow_frozen_truth=True (CLI: --allow-frozen-truth) "
            "to state that this is a deliberate frozen report, not a tuning loop."
        )
    if split not in SCORABLE_WITHOUT_OPT_IN and split not in GATED_SPLITS:
        raise GroundTruthPolicyError(
            f"no ground-truth policy is declared for split {split!r}; "
            f"known splits are {sorted((*SCORABLE_WITHOUT_OPT_IN, *GATED_SPLITS))}"
        )

    path = Path(benchmark_dir) / "ground_truth" / f"{split}.jsonl"
    if not path.exists():
        raise EvaluationInputError(f"ground truth not found: {path}")

    entries: dict[str, GroundTruthEntry] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        raw = json.loads(line)
        case_id = case_id_for(raw["record_ids"]["bank_records"][0])
        entries[case_id] = GroundTruthEntry(
            case_id=case_id,
            tier=raw["tier"],
            archetype=raw["archetype"],
            required_outcome=raw["required_outcome"],
            correct_relationship=raw["correct_relationship"],
            true_reference=raw["true_reference"],
            value_at_stake_paise=raw["value_at_stake_paise"],
            families=tuple(raw.get("families") or ()),
            required_composition=raw.get("required_composition") or "",
            expected_candidate_count=raw.get("expected_candidate_count"),
        )
    if not entries:
        raise EvaluationInputError(f"ground truth at {path} contained no rows")
    return entries


__all__ = [
    "GATED_SPLITS",
    "SCORABLE_WITHOUT_OPT_IN",
    "GroundTruthEntry",
    "load_ground_truth",
]
