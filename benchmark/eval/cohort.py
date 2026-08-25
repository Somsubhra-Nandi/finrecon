"""Cohort validation: prove the evaluated set is the set that was asked for.

This module exists because of one specific failure mode. A harness that
scores "whatever it found" will, when a source is short, report a smaller
cohort and a *better* percentage -- and nothing in the output says so. Every
number the evaluator prints is therefore preceded by an explicit reconciliation
of requested vs found, and an exact cohort that is not fully covered stops the
run instead of scoring a subset.

The second failure mode is tier drift. Comparing "50 T2 cases" against a set
that quietly contains a T3 measures two different things: T3 has no uniquely
resolvable answer, so escalating it is correct and resolving it is a defect,
and mixing the two makes both numbers meaningless. Tier composition is
reported for every cohort, and an expected tier can be pinned.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from benchmark.eval.errors import CohortError
from benchmark.eval.groundtruth import GroundTruthEntry

TIERS = ("T0", "T1", "T2", "T3")


@dataclass(frozen=True)
class CohortReport:
    """Requested vs found, reconciled case by case."""

    requested_count: int
    found_count: int
    requested_explicitly: bool
    case_ids: tuple[str, ...]
    duplicate_requested: tuple[str, ...] = ()
    """IDs listed more than once in the requested cohort."""
    duplicate_sources: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """IDs offered by more than one trajectory source."""
    missing: tuple[str, ...] = ()
    """Requested but not found in any source."""
    extra: tuple[str, ...] = ()
    """Found in a source but not requested. Excluded from scoring."""
    unknown_to_ground_truth: tuple[str, ...] = ()
    """In the cohort but absent from ground truth -- unscorable, so fatal."""
    tier_counts: dict[str, int] = field(default_factory=dict)
    expected_tier: str | None = None
    contamination: tuple[dict[str, str], ...] = ()
    """Cases whose tier is not the expected tier, with tier and archetype."""

    @property
    def complete(self) -> bool:
        return not self.missing and not self.unknown_to_ground_truth

    @property
    def all_expected_tier(self) -> bool:
        if self.expected_tier is None:
            return True
        return not self.contamination

    def as_dict(self) -> dict:
        return {
            "requested_count": self.requested_count,
            "found_count": self.found_count,
            "requested_explicitly": self.requested_explicitly,
            "complete": self.complete,
            "duplicate_requested": list(self.duplicate_requested),
            "duplicate_sources": {k: list(v) for k, v in self.duplicate_sources.items()},
            "missing": list(self.missing),
            "extra": list(self.extra),
            "unknown_to_ground_truth": list(self.unknown_to_ground_truth),
            "tier_counts": dict(sorted(self.tier_counts.items())),
            "expected_tier": self.expected_tier,
            "all_expected_tier": self.all_expected_tier,
            "contamination": [dict(sorted(c.items())) for c in self.contamination],
        }


def _duplicates(ids: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    dupes: list[str] = []
    for case_id in ids:
        if case_id in seen and case_id not in dupes:
            dupes.append(case_id)
        seen.add(case_id)
    return tuple(sorted(dupes))


def build_cohort(
    *,
    requested: tuple[str, ...] | None,
    available: tuple[str, ...],
    duplicate_sources: dict[str, tuple[str, ...]],
    ground_truth: dict[str, GroundTruthEntry],
    expected_tier: str | None = None,
) -> CohortReport:
    """Reconcile a requested cohort against what the sources actually hold.

    ``requested=None`` means "evaluate everything the sources provided", in
    which case nothing can be missing by definition -- but the report still
    says so explicitly rather than leaving it implied.
    """
    available_set = set(available)
    if requested is None:
        cohort = tuple(sorted(available_set))
        missing: tuple[str, ...] = ()
        extra: tuple[str, ...] = ()
        duplicate_requested: tuple[str, ...] = ()
        requested_count = len(cohort)
        explicit = False
    else:
        duplicate_requested = _duplicates(requested)
        requested_unique = set(requested)
        cohort = tuple(sorted(requested_unique & available_set))
        missing = tuple(sorted(requested_unique - available_set))
        extra = tuple(sorted(available_set - requested_unique))
        requested_count = len(requested_unique)
        explicit = True

    unknown = tuple(sorted(c for c in cohort if c not in ground_truth))

    tier_counts: dict[str, int] = {}
    contamination: list[dict[str, str]] = []
    for case_id in cohort:
        entry = ground_truth.get(case_id)
        if entry is None:
            continue
        tier_counts[entry.tier] = tier_counts.get(entry.tier, 0) + 1
        if expected_tier is not None and entry.tier != expected_tier:
            contamination.append(
                {
                    "case_id": case_id,
                    "tier": entry.tier,
                    "archetype": entry.archetype,
                }
            )

    return CohortReport(
        requested_count=requested_count,
        found_count=len(cohort),
        requested_explicitly=explicit,
        case_ids=cohort,
        duplicate_requested=duplicate_requested,
        duplicate_sources={
            k: v for k, v in sorted(duplicate_sources.items()) if k in set(cohort)
        },
        missing=missing,
        extra=extra,
        unknown_to_ground_truth=unknown,
        tier_counts=tier_counts,
        expected_tier=expected_tier,
        contamination=tuple(contamination),
    )


def enforce(report: CohortReport, *, require_exact: bool, require_tier: bool) -> None:
    """Fail loudly on an incomplete or contaminated cohort.

    Separated from :func:`build_cohort` so the caller can always *report* the
    reconciliation before deciding whether it is fatal -- an operator staring
    at "12 missing" needs to see which twelve, not just a traceback.
    """
    if report.unknown_to_ground_truth:
        raise CohortError(
            f"{len(report.unknown_to_ground_truth)} case(s) in the cohort have no "
            f"ground-truth row and cannot be scored: "
            f"{', '.join(report.unknown_to_ground_truth[:5])}"
            + (" ..." if len(report.unknown_to_ground_truth) > 5 else "")
        )
    if require_exact and report.missing:
        raise CohortError(
            f"exact cohort requested but coverage is incomplete: "
            f"{report.found_count}/{report.requested_count} found, "
            f"{len(report.missing)} missing "
            f"({', '.join(report.missing[:5])}"
            + (" ..." if len(report.missing) > 5 else "")
            + "). Add the source that holds them, or drop --require-exact-cohort "
            "to score the subset knowingly."
        )
    if report.duplicate_requested:
        raise CohortError(
            "the requested cohort lists duplicate case IDs: "
            f"{', '.join(report.duplicate_requested)}"
        )
    if require_tier and report.contamination:
        raise CohortError(
            f"cohort was pinned to {report.expected_tier} but contains "
            f"{len(report.contamination)} case(s) of another tier: "
            + ", ".join(
                f"{c['case_id']}={c['tier']}" for c in report.contamination[:5]
            )
            + (" ..." if len(report.contamination) > 5 else "")
        )


__all__ = ["TIERS", "CohortReport", "build_cohort", "enforce"]
