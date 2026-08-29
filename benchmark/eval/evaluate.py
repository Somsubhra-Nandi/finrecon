"""The Stage-4 evaluation itself: sources in, machine-readable report out.

The pipeline, in order, and the order matters:

1. **Load ground truth** for the split, under the split policy.
2. **Read every source** and assemble them into one candidate set.
3. **Reconcile the cohort** -- requested vs found, duplicates, missing, extra,
   tier composition, contamination -- and *report it before scoring*, so an
   operator sees the shape of the evaluated set even when the run then aborts.
4. **Enforce** the cohort contract, failing closed on an incomplete exact
   cohort.
5. **Replay offline** through the real validator and policy.
6. **Score** with the single correctness predicate, plus soundness, agent and
   telemetry metrics.

Report stability is a deliberate property, not an accident. The report
contains no wall-clock timestamp, no absolute path, no run ID and no
iteration-ordered set: the same inputs produce byte-identical JSON. That is
what makes ``git diff`` meaningful between two evaluations and what
``tests/test_stage4_evaluator.py`` pins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from finrecon.agent.loop import DEFAULT_MAX_STEPS, MAX_TOOL_CALLS_PER_STEP
from finrecon.decide.config import DEFAULT_POLICY, Stage3Policy

from benchmark.eval import EVALUATOR_VERSION
from benchmark.eval.cohort import CohortReport, build_cohort, enforce
from benchmark.eval.groundtruth import GroundTruthEntry, load_ground_truth
from benchmark.eval.replay import ReplayResult, replay_cohort
from benchmark.eval.scoring import (
    CaseVerdict,
    agent_metrics,
    aggregate_scores,
    conjunction_metrics,
    structural_metrics,
    metrics_by_archetype,
    metrics_by_candidate_count,
    metrics_by_family,
    metrics_by_required_composition,
    metrics_by_tier,
    soundness_violations,
    telemetry,
    telemetry_from_payloads,
    trajectory_metrics,
    verdict_for,
    versions_from_payloads,
    versions_of,
)
from benchmark.eval.sources import (
    AssembledSources,
    TrajectoryRecord,
    assemble,
    load_cache_dir,
    load_run_dump,
)


@dataclass(frozen=True)
class EvaluationConfig:
    """Everything that decides what is evaluated and how."""

    benchmark_dir: Path
    split: str = "dev"
    trajectory_dirs: tuple[Path, ...] = ()
    run_dumps: tuple[Path, ...] = ()
    cohort_ids: tuple[str, ...] | None = None
    expected_tier: str | None = None
    provider_id: str = "gorouter"
    model: str = "claude-opus-5-thinking"
    max_steps: int = DEFAULT_MAX_STEPS
    max_tool_calls_per_step: int = MAX_TOOL_CALLS_PER_STEP
    policy: Stage3Policy = field(default_factory=lambda: DEFAULT_POLICY)
    require_exact_cohort: bool = True
    require_expected_tier: bool = True
    allow_frozen_truth: bool = False
    label: str = "evaluation"
    replay: bool = True
    """Replay through the real validator and policy to obtain decisions.

    Set ``False`` only for artifacts recorded under a *superseded* contract,
    which today's validator cannot parse and today's cache key cannot address
    (see :mod:`benchmark.eval.replay`). Such a run still yields termination,
    tool-validation and telemetry facts, but **no correctness**: correctness
    requires a decision, a decision requires the gate, and the gate cannot run
    on a record shape it no longer understands. The report says so in place of
    the metrics rather than leaving a zero that reads like a result.
    """

    def as_dict(self) -> dict:
        """Machine-readable configuration, with nothing machine-specific in it.

        Paths are reduced to their final component on purpose: an absolute
        path would make two reports of the same run differ between machines,
        and the report is meant to be diffable.
        """
        return {
            "evaluator_version": EVALUATOR_VERSION,
            "label": self.label,
            "split": self.split,
            "trajectory_dirs": [p.name for p in self.trajectory_dirs],
            "run_dumps": [p.name for p in self.run_dumps],
            "provider_id_requested": self.provider_id,
            "model_requested": self.model,
            "max_steps": self.max_steps,
            "max_tool_calls_per_step": self.max_tool_calls_per_step,
            "policy_declaration": self.policy.describe(),
            "expected_tier": self.expected_tier,
            "require_exact_cohort": self.require_exact_cohort,
            "require_expected_tier": self.require_expected_tier,
            "explicit_cohort_supplied": self.cohort_ids is not None,
            "allow_frozen_truth": self.allow_frozen_truth,
            "replay": self.replay,
            "offline": True,
            "provider_constructed": False,
        }


@dataclass(frozen=True)
class EvaluationResult:
    """The report, plus the objects behind it for programmatic callers."""

    report: dict
    cohort: CohortReport
    verdicts: tuple[CaseVerdict, ...]
    replay: ReplayResult | None
    ground_truth: dict[str, GroundTruthEntry]

    @property
    def wrong(self) -> tuple[CaseVerdict, ...]:
        return tuple(v for v in self.verdicts if v.correct is False)


def _read_sources(config: EvaluationConfig) -> AssembledSources:
    groups: list[list[TrajectoryRecord]] = []
    for directory in config.trajectory_dirs:
        groups.append(load_cache_dir(directory))
    for dump in config.run_dumps:
        groups.append(load_run_dump(dump))
    if not groups:
        from benchmark.eval.errors import EvaluationInputError

        raise EvaluationInputError(
            "no trajectory source given. Pass --trajectories DIR and/or "
            "--run-dump FILE; the evaluator is offline and cannot produce them."
        )
    return assemble(groups)


def _recorded_only_result(
    *,
    config: EvaluationConfig,
    cohort: CohortReport,
    sources: AssembledSources,
    payloads: list[dict],
    ground_truth: dict[str, GroundTruthEntry],
) -> EvaluationResult:
    """Describe a cohort that cannot be replayed, without inventing accuracy.

    Every correctness field is ``None``, not zero, and ``scoring_available``
    says why. A zero here would be read as "no wrong resolutions", which is a
    claim this mode has no evidence for.
    """
    unavailable = (
        "not scored: --no-replay was used, so no decision was produced. "
        "Correctness requires the deterministic gate, which cannot run on a "
        "trajectory recorded under a superseded contract."
    )
    metrics = {
        "investigated": len(payloads),
        "auto_resolved": None,
        "correct_auto_resolutions": None,
        "wrong_auto_resolutions": None,
        "escalated": None,
        "auto_resolution_accuracy": None,
        "overall_match_rate": None,
        "auto_resolution_coverage": None,
        "unsafe_auto_match_rate": None,
        "escalation_recall": None,
        "uniquely_resolvable_cases": sum(
            1 for c in cohort.case_ids if ground_truth[c].is_uniquely_resolvable
        ),
        "truly_ambiguous_cases": sum(
            1 for c in cohort.case_ids if not ground_truth[c].is_uniquely_resolvable
        ),
        "correctly_escalated": None,
        "value_at_risk_paise": None,
        "scoring_available": False,
        "scoring_unavailable_reason": unavailable,
    }
    report = {
        "evaluator_version": EVALUATOR_VERSION,
        "report_kind": "evaluation",
        "configuration": config.as_dict(),
        "offline_guarantee": {
            "provider_calls_made": False,
            "cache_hits": 0,
            "replay_only": False,
            "chain": None,
            "note": "recorded-only: artifacts were read, nothing was executed",
        },
        "cohort": {
            **cohort.as_dict(),
            "case_ids": list(cohort.case_ids),
            "sources_contributing": _sources_in_cohort(sources, cohort.case_ids),
            "sources_offered": sources.per_source_counts,
            "case_source": {
                case_id: sources.records[case_id].source for case_id in cohort.case_ids
            },
        },
        "recorded_versions": versions_from_payloads(payloads),
        "metrics": metrics,
        # No decision was produced, so there is nothing to slice. Empty rather
        # than absent, and empty rather than zero-filled: a zero here would
        # read as a measured result.
        "metrics_by_tier": {},
        "metrics_by_archetype": {},
        "metrics_by_family": {},
        "metrics_by_required_composition": {},
        "metrics_by_candidate_count": {},
        "agent": {
            **trajectory_metrics(payloads),
            "accepted_evidence_relations": {},
            "reference_kinds_used": {},
            "escalation_blockers": {},
            "evidence_detail_available": False,
        },
        "telemetry": telemetry_from_payloads(payloads),
        # No decision was produced, so there is no evidence shape to describe.
        "conjunction": {
            "resolutions_total": None,
            "closure_is_the_decision_input": None,
            "note": unavailable,
        },
        "soundness": {
            "total_violations": None,
            "violations_by_check": {},
            "violations": [],
            "checks_available": False,
            "checks_unavailable_reason": unavailable,
        },
        "wrong_resolutions": [],
        "per_case": [
            {
                "case_id": case_id,
                "tier": ground_truth[case_id].tier,
                "archetype": ground_truth[case_id].archetype,
                "termination_reason": payload.get("termination_reason"),
                "scored": False,
            }
            for case_id, payload in zip(cohort.case_ids, payloads)
        ],
    }
    return EvaluationResult(
        report=report,
        cohort=cohort,
        verdicts=(),
        replay=None,
        ground_truth=ground_truth,
    )


def _sources_in_cohort(
    sources: AssembledSources, cohort: tuple[str, ...]
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case_id in cohort:
        label = sources.records[case_id].source
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def evaluate(config: EvaluationConfig, *, staging_dir: Path) -> EvaluationResult:
    """Run one offline evaluation. Makes zero provider calls, by construction."""
    ground_truth = load_ground_truth(
        config.benchmark_dir, config.split, allow_frozen_truth=config.allow_frozen_truth
    )

    sources = _read_sources(config)

    cohort = build_cohort(
        requested=config.cohort_ids,
        available=sources.case_ids,
        duplicate_sources=sources.duplicates,
        ground_truth=ground_truth,
        expected_tier=config.expected_tier,
    )
    enforce(
        cohort,
        require_exact=config.require_exact_cohort and config.cohort_ids is not None,
        require_tier=config.require_expected_tier and config.expected_tier is not None,
    )

    cohort_payloads = [sources.records[c].payload for c in cohort.case_ids]

    if not config.replay:
        return _recorded_only_result(
            config=config,
            cohort=cohort,
            sources=sources,
            payloads=cohort_payloads,
            ground_truth=ground_truth,
        )

    replay = replay_cohort(
        benchmark_dir=config.benchmark_dir,
        split=config.split,
        records=sources.records,
        cohort=cohort.case_ids,
        provider_id=config.provider_id,
        model=config.model,
        staging_dir=staging_dir,
        max_steps=config.max_steps,
        max_tool_calls_per_step=config.max_tool_calls_per_step,
        policy=config.policy,
    )

    outcomes = [replay.outcomes_by_case[case_id] for case_id in cohort.case_ids]
    verdicts = tuple(verdict_for(o, ground_truth[o.case_id]) for o in outcomes)
    violations = soundness_violations(outcomes)

    by_check: dict[str, int] = {}
    for violation in violations:
        by_check[violation.check] = by_check.get(violation.check, 0) + 1

    report = {
        "evaluator_version": EVALUATOR_VERSION,
        "report_kind": "evaluation",
        "configuration": config.as_dict(),
        "offline_guarantee": {
            "provider_calls_made": replay.provider_calls_made,
            "cache_hits": replay.cache_hits,
            "replay_only": True,
            "chain": None,
        },
        "cohort": {
            **cohort.as_dict(),
            "case_ids": list(cohort.case_ids),
            # Counted over the *cohort*, not over everything the sources held.
            # A source that supplied 50 records of which 38 were in the cohort
            # contributed 38 here, which is the number that explains the result.
            "sources_contributing": _sources_in_cohort(sources, cohort.case_ids),
            "sources_offered": sources.per_source_counts,
            "case_source": {
                case_id: sources.records[case_id].source for case_id in cohort.case_ids
            },
        },
        "recorded_versions": versions_of(outcomes),
        "metrics": aggregate_scores(verdicts),
        # Sliced views of the same verdicts. Present for every split, empty
        # where the split's ground truth carries no such label -- an empty
        # block reads as "this benchmark generation has no families", which
        # is true, while an absent key would read as "not measured".
        "metrics_by_tier": metrics_by_tier(verdicts),
        "metrics_by_archetype": metrics_by_archetype(verdicts),
        "metrics_by_family": metrics_by_family(verdicts),
        "metrics_by_required_composition": metrics_by_required_composition(verdicts),
        "metrics_by_candidate_count": metrics_by_candidate_count(verdicts),
        "agent": {**agent_metrics(outcomes), "evidence_detail_available": True},
        "conjunction": conjunction_metrics(outcomes),
        "structural": structural_metrics(outcomes),
        "telemetry": telemetry(outcomes),
        "soundness": {
            "total_violations": len(violations),
            "violations_by_check": dict(sorted(by_check.items())),
            "violations": [v.as_dict() for v in violations],
            "checks_available": True,
            "checks_unavailable_reason": None,
        },
        "wrong_resolutions": [
            v.as_dict() for v in verdicts if v.correct is False
        ],
        "per_case": [v.as_dict() for v in verdicts],
    }

    return EvaluationResult(
        report=report,
        cohort=cohort,
        verdicts=verdicts,
        replay=replay,
        ground_truth=ground_truth,
    )


__all__ = ["EvaluationConfig", "EvaluationResult", "evaluate"]
