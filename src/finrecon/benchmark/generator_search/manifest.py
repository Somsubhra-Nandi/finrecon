"""Manifest and content hash for the bounded-search challenge."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from finrecon.benchmark.generator_search.config import (
    AMBIGUOUS_CASES,
    BENCHMARK_NAME,
    FAMILY_COUNTS,
    GENERATOR_VERSION,
    MAX_MODEL_STEPS,
    MAX_TOOL_CALLS_PER_STEP,
    RESOLVABLE_CASES,
    SEARCH_SEED,
    TOOL_CALL_BUDGET,
)
from finrecon.benchmark.generator_search.dataset import SearchDatasetBundle


def hashed_files() -> tuple[str, ...]:
    return tuple(
        f"datasets/{BENCHMARK_NAME}/{name}.jsonl"
        for name in ("bank_records", "orders", "payments", "refunds", "settlements")
    ) + (
        f"ground_truth/{BENCHMARK_NAME}.jsonl",
        f"cohorts/{BENCHMARK_NAME}.json",
    )


def compute_search_fingerprint(benchmark_dir: Path) -> str:
    lines = []
    for relative in hashed_files():
        digest = hashlib.sha256((benchmark_dir / relative).read_bytes()).hexdigest()
        lines.append(f"{relative}\t{digest}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def build_manifest(bundle: SearchDatasetBundle, fingerprint: str) -> dict:
    candidate_counts = Counter(
        str(row["expected_candidate_count"]) for row in bundle.ground_truth
    )
    action_counts = [row["plausible_evidence_action_count"] for row in bundle.ground_truth]
    compositions = Counter(row["required_composition"] for row in bundle.ground_truth)
    return {
        "benchmark_name": BENCHMARK_NAME,
        "label": "Synthetic bounded-search challenge",
        "purpose": (
            "Compare evidence-search strategy under the same immutable candidate snapshot, "
            "tool schemas and outputs, validator.v3, policy.v1, and four-call budget."
        ),
        "generator_version": GENERATOR_VERSION,
        "seed": SEARCH_SEED,
        "frozen": False,
        "status": (
            "READY FOR REVIEW, NOT YET FROZEN. Freeze this exact hash before any paid "
            "hosted-model trajectory is observed."
        ),
        "case_count": len(bundle.ground_truth),
        "resolvable_cases": RESOLVABLE_CASES,
        "ambiguous_cases": AMBIGUOUS_CASES,
        "family_counts": dict(sorted(FAMILY_COUNTS.items())),
        "candidate_count_distribution": dict(sorted(candidate_counts.items())),
        "required_composition_counts": dict(sorted(compositions.items())),
        "record_counts": bundle.record_counts(),
        "total_record_count": bundle.total_record_count(),
        "tool_budget": {
            "maximum_executed_tool_calls_per_case": TOOL_CALL_BUDGET,
            "max_model_steps": MAX_MODEL_STEPS,
            "max_tool_calls_per_step": MAX_TOOL_CALLS_PER_STEP,
            "one_tool_call_definition": (
                "One successfully executed registered tool invocation. A "
                "compare_reference_fragment invocation counts once although the tool "
                "deterministically fans that fragment across every immutable candidate."
            ),
            "terminal_turn_semantics": (
                "A case may spend all four steps on tools only if validator/policy resolves "
                "after the fourth output; otherwise step-budget exhaustion is a blocker."
            ),
        },
        "plausible_evidence_action_count": {
            "min": min(action_counts),
            "max": max(action_counts),
            "mean": round(sum(action_counts) / len(action_counts), 2),
        },
        "fairness_contract": {
            "same_snapshot": True,
            "same_candidate_set": True,
            "same_tools": True,
            "same_tool_outputs": True,
            "same_tool_budget": True,
            "same_validator": "validator.v3",
            "same_policy": "policy.v1",
            "only_intended_difference": "selection and ordering of evidence tool calls",
        },
        "benchmark_sha256": fingerprint,
        "hashed_files": list(hashed_files()),
        "hash_algorithm": (
            "For each hashed_files path in order, hash raw bytes; concatenate "
            "'<path>\\t<file_sha256>\\n'; SHA-256 the UTF-8 concatenation."
        ),
        "v3_frozen_eval_sha256_expected": (
            "f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b"
        ),
        "network_or_provider_calls_required": False,
    }


def manifest_path(benchmark_dir: Path) -> Path:
    return benchmark_dir / "manifests" / f"{BENCHMARK_NAME}.json"


def write_manifest(benchmark_dir: Path, manifest: dict) -> Path:
    path = manifest_path(benchmark_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


__all__ = [
    "build_manifest",
    "compute_search_fingerprint",
    "hashed_files",
    "manifest_path",
    "write_manifest",
]
