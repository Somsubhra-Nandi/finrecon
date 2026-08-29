"""Compare two offline evaluations over one identical cohort.

The comparison's only job is to make a same-cohort difference legible without
letting it become a causal claim.

**Cohort identity is verified, not assumed.** Two runs over "fifty T2 cases"
are not comparable unless they are the *same* fifty; the comparison refuses
to emit deltas otherwise. Tier composition is checked separately, because two
cohorts can share a size and a tier count while differing case for case.

**Attribution is withheld by default.** A run differs from another along as
many axes as the operator changed: model, prompt version, tool schema, loop,
validator, policy. When more than one moved, the difference in outcomes cannot
be assigned to any single one of them, and this module says exactly that
instead of picking the interesting one. The rule is mechanical -- count the
differing configuration dimensions -- so it cannot be argued into a
conclusion by whoever writes the summary.

That is not a stylistic preference. An A/B whose treatment changed three
things at once has no identified effect, and reporting one anyway is how a
tooling change gets credited with a model's capability.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchmark.eval import EVALUATOR_VERSION
from benchmark.eval.errors import CohortError

CONFIGURATION_DIMENSIONS = (
    "provider_model",
    "max_steps",
    "max_tool_calls_per_step",
    "prompt_version",
    "tool_schema_version",
    "agent_loop_version",
    "validator_version",
    "policy_version",
)
"""Axes along which two runs may differ. Each one is a confound if it moved."""

COMPARED_METRICS = (
    ("auto_resolved", "metrics", "auto_resolved"),
    ("escalated", "metrics", "escalated"),
    ("correct_auto_resolutions", "metrics", "correct_auto_resolutions"),
    ("wrong_auto_resolutions", "metrics", "wrong_auto_resolutions"),
    ("auto_resolution_accuracy", "metrics", "auto_resolution_accuracy"),
    ("overall_match_rate", "metrics", "overall_match_rate"),
    ("value_at_risk_paise", "metrics", "value_at_risk_paise"),
    # Termination counts come straight off the recorded trajectory, so they
    # remain comparable even when one side is a recorded-only baseline whose
    # decisions cannot be reproduced under the current contract.
    ("deterministic_policy_resolved", "agent", "deterministic_policy_resolved"),
    ("investigation_complete", "agent", "investigation_complete"),
    ("tool_validation_failed", "agent", "tool_validation_failed"),
    ("tool_validation_rejections_total", "agent", "tool_validation_rejections_total"),
    ("tool_calls_executed_total", "agent", "tool_calls_executed_total"),
    ("tool_calls_mean_per_case", "agent", "tool_calls_mean_per_case"),
    ("tool_calls_median_per_case", "agent", "tool_calls_median_per_case"),
    ("tool_budget_exhaustion_rate", "agent", "tool_budget_exhaustion_rate"),
    ("provider_failed_attempts", "telemetry", "provider_failed_attempts"),
    ("tokens_mean_per_case", "telemetry", "tokens_mean_per_case"),
    ("model_steps_mean_per_case", "telemetry", "model_steps_mean_per_case"),
    ("tokens_total", "telemetry", "tokens_total"),
)


@dataclass(frozen=True)
class SideBySide:
    metric: str
    a: object
    b: object

    @property
    def delta(self) -> object:
        if isinstance(self.a, (int, float)) and isinstance(self.b, (int, float)):
            return round(self.b - self.a, 6)
        return None

    def as_dict(self) -> dict:
        return {"metric": self.metric, "a": self.a, "b": self.b, "delta": self.delta}


def _provider_model(report: dict) -> dict:
    telemetry = report.get("telemetry", {})
    return {
        "requested": sorted(telemetry.get("models_requested", {})),
        "reported": sorted(telemetry.get("models_reported", {})),
    }


def _config_signature(report: dict) -> dict:
    versions = report.get("recorded_versions", {})
    configuration = report.get("configuration", {})
    return {
        "provider_model": _provider_model(report),
        "max_steps": configuration.get("max_steps"),
        "max_tool_calls_per_step": configuration.get("max_tool_calls_per_step"),
        "prompt_version": versions.get("prompt_version", []),
        "tool_schema_version": versions.get("tool_schema_version", []),
        "agent_loop_version": versions.get("agent_loop_version", []),
        "validator_version": versions.get("validator_version", []),
        "policy_version": versions.get("policy_version", []),
    }


def compare(
    report_a: dict,
    report_b: dict,
    *,
    label_a: str = "A",
    label_b: str = "B",
    require_identical_cohort: bool = True,
) -> dict:
    """Build a side-by-side comparison of two evaluation reports."""
    cohort_a = tuple(report_a.get("cohort", {}).get("case_ids", ()))
    cohort_b = tuple(report_b.get("cohort", {}).get("case_ids", ()))
    identical_cohort = tuple(sorted(cohort_a)) == tuple(sorted(cohort_b))

    tiers_a = dict(report_a.get("cohort", {}).get("tier_counts", {}))
    tiers_b = dict(report_b.get("cohort", {}).get("tier_counts", {}))
    identical_tiers = tiers_a == tiers_b

    only_a = sorted(set(cohort_a) - set(cohort_b))
    only_b = sorted(set(cohort_b) - set(cohort_a))

    if require_identical_cohort and not identical_cohort:
        raise CohortError(
            "refusing to compare: the two evaluations do not cover the same cases "
            f"({len(only_a)} only in {label_a}, {len(only_b)} only in {label_b}). "
            "Pin both runs to one cohort with --cohort, or pass "
            "--allow-cohort-mismatch to see the reconciliation without deltas."
        )

    signature_a = _config_signature(report_a)
    signature_b = _config_signature(report_b)
    differing = [
        dimension
        for dimension in CONFIGURATION_DIMENSIONS
        if signature_a.get(dimension) != signature_b.get(dimension)
    ]

    comparable = identical_cohort and identical_tiers
    rows = []
    if comparable:
        for name, section, key in COMPARED_METRICS:
            rows.append(
                SideBySide(
                    metric=name,
                    a=report_a.get(section, {}).get(key),
                    b=report_b.get(section, {}).get(key),
                ).as_dict()
            )

    if len(differing) == 0:
        attribution = "identical configuration; any difference is run-to-run variation"
    elif len(differing) == 1:
        attribution = (
            f"exactly one configuration dimension differs ({differing[0]}); a "
            "single-factor reading is defensible only if the runs are otherwise "
            "matched and the cohort is identical"
        )
    else:
        attribution = (
            f"{len(differing)} configuration dimensions changed together "
            f"({', '.join(differing)}). No outcome difference can be attributed to "
            "any one of them. A same-model A/B varying one dimension is required "
            "to identify an effect."
        )

    return {
        "evaluator_version": EVALUATOR_VERSION,
        "report_kind": "comparison",
        "labels": {"a": label_a, "b": label_b},
        "cohort_identity": {
            "identical_case_ids": identical_cohort,
            "identical_tier_composition": identical_tiers,
            "count_a": len(cohort_a),
            "count_b": len(cohort_b),
            "tier_counts_a": dict(sorted(tiers_a.items())),
            "tier_counts_b": dict(sorted(tiers_b.items())),
            "only_in_a": only_a,
            "only_in_b": only_b,
            "comparable": comparable,
        },
        "configuration": {
            "a": signature_a,
            "b": signature_b,
            "differing_dimensions": differing,
            "single_factor": len(differing) == 1,
        },
        "attribution": {
            "causal_claim": None,
            "statement": attribution,
            "differing_dimension_count": len(differing),
        },
        "side_by_side": rows,
        "by_family_side_by_side": _compare_families(report_a, report_b)
        if comparable
        else {},
    }


def _compare_families(report_a: dict, report_b: dict) -> dict:
    """Compare the same safety/coverage counts within every shared family."""
    block_a = report_a.get("metrics_by_family", {})
    block_b = report_b.get("metrics_by_family", {})
    fields = (
        "cases",
        "uniquely_resolvable",
        "auto_resolved",
        "correct_auto_resolutions",
        "wrong_auto_resolutions",
        "escalated",
        "match_rate",
        "auto_resolution_accuracy",
        "value_at_risk_paise",
        "tool_calls_executed_total",
        "tool_calls_mean_per_case",
        "tool_calls_median_per_case",
        "tool_budget_exhausted_cases",
        "tool_budget_exhaustion_rate",
    )
    result: dict[str, list[dict]] = {}
    for family in sorted(set(block_a) | set(block_b)):
        a = block_a.get(family, {})
        b = block_b.get(family, {})
        result[family] = [
            SideBySide(field, a.get(field), b.get(field)).as_dict() for field in fields
        ]
    return result


__all__ = ["COMPARED_METRICS", "CONFIGURATION_DIMENSIONS", "SideBySide", "compare"]
