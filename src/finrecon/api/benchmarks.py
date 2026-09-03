"""Read-only benchmark catalogue for the product UI.

This module intentionally has no dependency on ``benchmark.eval`` or any
provider implementation. It reads only committed manifests, reports, visible
record files, and persisted trajectory JSON. Hidden truth stays outside both
browsing and replay payloads.
"""

from __future__ import annotations

import importlib
import json
from functools import cached_property
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError

from finrecon.agent.trajectory import Trajectory
from finrecon.loader import load_visible_split
from finrecon.normalize import normalize_batch
from finrecon.pipeline import case_id_for, reconcile_batch
from finrecon.decide.config import DEFAULT_POLICY
from finrecon.decide.policy import adjudicate
from finrecon.agent.version import POLICY_VERSION, VALIDATOR_VERSION


CONTROLLER_REJECTION_DEMO = "case:bnk_bsearch_000012"
TRAJECTORY_DIRECTORIES = {
    "openrouter-free": "bounded-search-v1-openrouter-free-final",
    "opus": "bounded-search-v1-opus5-thinking-final",
    "opus-provider-recovered": "frozen-eval-v3-opus5-thinking-final",
}
ORIGINAL_V3_FAILURE_DIRECTORY = "frozen-eval-v3-opus5-thinking-t2-provider-failures-original"


class BenchmarkCatalog:
    """Immutable-on-disk catalogue; all methods are read-only."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.benchmark_root = project_root / "benchmark"
        self.trajectories_root = project_root / "fixtures" / "trajectories"

    def _json(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={
                "code": "benchmark_artifact_malformed",
                "message": f"Benchmark artifact {path.name} cannot be read safely.",
            }) from exc
        if not isinstance(value, dict):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={
                "code": "benchmark_artifact_malformed",
                "message": f"Benchmark artifact {path.name} must be a JSON object.",
            })
        return value

    def _not_found(self, benchmark_id: str) -> HTTPException:
        return HTTPException(status_code=404, detail={
            "code": "benchmark_not_found", "message": f"Benchmark {benchmark_id!r} does not exist."
        })

    @cached_property
    def _manifests(self) -> dict[str, dict[str, Any]]:
        return {
            "frozen-eval-v3": self._json(self.benchmark_root / "manifests" / "v3.json"),
            "bounded-search-v1": self._json(self.benchmark_root / "manifests" / "bounded-search-v1.json"),
            "v4-pilot": self._json(self.benchmark_root / "manifests" / "v4-pilot.json"),
        }

    def list(self) -> dict[str, Any]:
        summaries = []
        # The pilot remains addressable for engineering/audit work, but is not
        # part of the judge-facing evaluation catalogue.
        for benchmark_id in ("frozen-eval-v3", "bounded-search-v1"):
            item = self.detail(benchmark_id)
            summaries.append({key: item[key] for key in ("benchmark_id", "title", "status", "case_count", "description", "replay_available", "report_available", "investigators")})
        return {
            "benchmarks": summaries,
            "evolution": [
                {"version": "v1", "status": "SUPERSEDED", "summary": "Retained for audit; superseded by a T2 validity correction."},
                {"version": "v2", "status": "SUPERSEDED", "summary": "Retained for audit; superseded by a T0 validity correction."},
                {"version": "v3", "status": "CURRENT FROZEN", "summary": "Current full-pipeline safety and regression benchmark."},
            ],
        }

    def detail(self, benchmark_id: str) -> dict[str, Any]:
        manifest = self._manifests.get(benchmark_id)
        if manifest is None:
            raise self._not_found(benchmark_id)
        if benchmark_id == "frozen-eval-v3":
            return {
                "benchmark_id": benchmark_id, "title": "Frozen Eval v3", "status": "FROZEN",
                "case_count": int(manifest["case_counts"]["frozen-eval"]),
                "description": "Complete offline replay of rules, frozen investigation trajectories, deterministic validation, and financial policy.",
                "replay_available": True, "report_available": True, "investigators": ["opus-provider-recovered"],
                "integrity": {"sha256": manifest["frozen_eval_sha256"], "hash_verified_during_replay": True},
                "constraints": {"replay": "Exactly 240 committed trajectories; cache misses fail closed and no provider chain is constructed.", "ground_truth": "Read only after reconciliation, inside the Stage-4 evaluation boundary."},
                "notices": ["OFFLINE REPLAY · ZERO MODEL CALLS", "AI investigates. Deterministic controls decide."],
            }
        if benchmark_id == "bounded-search-v1":
            return {
                "benchmark_id": benchmark_id, "title": "Bounded Search v1", "status": "FROZEN",
                "case_count": int(manifest["case_count"]),
                "description": "Adversarial bounded evidence-search benchmark: 4 steps × 1 tool call.",
                "replay_available": True, "report_available": True,
                "investigators": ["mechanical", "openrouter-free", "opus"],
                "integrity": {"sha256": manifest["benchmark_sha256"], "frozen": bool(manifest["frozen"])},
                "constraints": {"max_model_steps": manifest["tool_budget"]["max_model_steps"], "max_tool_calls_per_step": manifest["tool_budget"]["max_tool_calls_per_step"], "replay": "Recorded trajectory files only; zero provider calls."},
                "notices": ["OpenRouter has 50 persisted records but its authoritative scored cohort contains 45 valid provider-response cases.", "Opus has a complete 50-case frozen scored cohort. Mechanical is report/comparison-only."],
            }
        return {
            "benchmark_id": benchmark_id, "title": "v4 Compositional Evidence Pilot", "status": "PILOT",
            "case_count": int(manifest["case_count"]),
            "description": "Experimental compositional evidence pilot; not frozen and not a headline benchmark.",
            "replay_available": False, "report_available": False, "investigators": [],
            "integrity": {"sha256": manifest["pilot_sha256"], "frozen": False},
            "constraints": {"replay": "No persisted trajectories are exposed.", "reporting": "No benchmark accuracy claims are published for this pilot."},
            "notices": ["Case exploration only. No replay or frozen benchmark metrics are fabricated."],
        }

    def reports(self, benchmark_id: str) -> dict[str, Any]:
        self.detail(benchmark_id)
        paths: list[tuple[str, Path, str]] = []
        if benchmark_id == "frozen-eval-v3":
            paths = [("provider-recovered", self.benchmark_root / "reports" / "frozen-eval-v3-opus5-thinking-provider-recovered-240.json", "Canonical provider-recovered Stage-3 report")]
        elif benchmark_id == "bounded-search-v1":
            paths = [
                ("mechanical", self.benchmark_root / "reports" / "bounded-search-v1-mechanical.json", "Mechanical baseline; report/comparison only"),
                ("openrouter-free", self.benchmark_root / "reports" / "bounded-search-v1-openrouter-free-valid-45.json", "45-case valid provider-response scored cohort"),
                ("opus", self.benchmark_root / "reports" / "bounded-search-v1-opus5-thinking-full-50.json", "Authoritative complete 50-case frozen scored cohort"),
            ]
        reports = []
        for report_id, path, label in paths:
            report = self._json(path)
            reports.append({
                "report_id": report_id, "label": label, "metrics": report.get("metrics") or report.get("frozen_core", {}).get("metrics"),
                "telemetry": report.get("telemetry", {}), "cohort": self._safe_cohort(report.get("cohort", {})),
                "recorded_versions": report.get("recorded_versions") or report.get("architecture_versions", {}),
            })
        return {"benchmark_id": benchmark_id, "reports": reports}

    @staticmethod
    def _safe_cohort(cohort: Any) -> dict[str, Any]:
        if not isinstance(cohort, dict):
            return {}
        # Case IDs are sufficient for denominator transparency. Do not return
        # any per-case Stage-4 verdict/truth fields from reports.
        return {key: cohort[key] for key in ("requested_count", "found_count", "complete", "tier_counts", "sources_contributing") if key in cohort}

    @cached_property
    def _cases(self) -> dict[str, dict[str, dict[str, Any]]]:
        return {key: self._build_cases(split, batch_id) for key, (split, batch_id) in {
            "frozen-eval-v3": ("frozen-eval", "batch:frozen-eval"),
            "bounded-search-v1": ("bounded-search-v1", "batch:bounded-search-v1"),
            "v4-pilot": ("v4-pilot", "batch:v4-pilot"),
        }.items()}

    def _build_cases(self, split: str, batch_id: str) -> dict[str, dict[str, Any]]:
        visible = load_visible_split(self.benchmark_root, split)
        batch = normalize_batch(orders=visible.orders, payments=visible.payments, refunds=visible.refunds, settlements=visible.settlements, bank_records=visible.bank_records)
        decisions, snapshots, _candidates = reconcile_batch(batch, batch_id)
        decision_by_case = {decision.case_id: decision for decision in decisions}
        snapshot_by_case = {snapshot.case_id: snapshot for snapshot in snapshots}
        settlements = {item.settlement_id: item.model_dump(mode="json") for item in visible.settlements}
        result: dict[str, dict[str, Any]] = {}
        for bank in visible.bank_records:
            case_id = case_id_for(bank.bank_record_id)
            snapshot = snapshot_by_case.get(case_id)
            candidate_ids = [candidate.candidate_id for candidate in snapshot.candidates] if snapshot else []
            settlement_ids = {settlement_id for candidate in (snapshot.candidates if snapshot else ()) for settlement_id in candidate.settlement_ids}
            result[case_id] = {
                "case_id": case_id, "bank_record_id": bank.bank_record_id, "narration": bank.narration,
                "amount_paise": int(bank.amount), "candidate_count": len(candidate_ids) if snapshot else None,
                "candidate_snapshot": snapshot.model_dump(mode="json") if snapshot else None,
                "_snapshot": snapshot,
                "_stage2_decision": decision_by_case.get(case_id),
                "visible_records": {"bank_record": bank.model_dump(mode="json"), "settlements": [settlements[item] for item in sorted(settlement_ids)]},
            }
        return result

    @staticmethod
    def _v3_projection_error(message: str) -> HTTPException:
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "code": "benchmark_v3_projection_unavailable", "message": message,
        })

    @cached_property
    def _v3_case_evaluations(self) -> dict[str, dict[str, Any]]:
        """Build the safe v3 projection from recorded final outcomes only.

        The final report records all Stage-3 residual dispositions and tiers.
        The other cases are recorded Stage-2 decisions recreated from immutable
        visible inputs; their two recorded matcher categories map to the public
        T0/T1 benchmark categories. Candidate, settlement, reference, truth,
        and correctness values never enter this index.
        """
        report = self._json(self.benchmark_root / "reports" / "frozen-eval-v3-opus5-thinking-provider-recovered-240.json")
        per_case = report.get("per_case")
        if not isinstance(per_case, list):
            raise self._v3_projection_error("Stage-3 per-case outcomes are missing")

        stage3: dict[str, dict[str, Any]] = {}
        for item in per_case:
            if not isinstance(item, dict):
                raise self._v3_projection_error("a Stage-3 outcome is malformed")
            case_id, tier, resolved, blockers = item.get("case_id"), item.get("tier"), item.get("resolved"), item.get("blockers")
            termination = item.get("termination_reason")
            tool_calls = item.get("tool_calls_executed")
            relations = item.get("evidence_relations")
            if not isinstance(case_id, str) or tier not in {"T2", "T3"} or not isinstance(resolved, bool) or not isinstance(blockers, list) or not all(isinstance(x, str) for x in blockers) or not isinstance(termination, str) or not isinstance(tool_calls, int) or not isinstance(relations, list):
                raise self._v3_projection_error("a Stage-3 outcome lacks safe disposition fields")
            stage3[case_id] = {
                "tier": tier,
                "final_disposition": "RESOLVED" if resolved else "ESCALATED",
                "resolution_stage": "STAGE_3",
                "resolution_method": "Frozen evidence replay" if resolved else None,
                "blockers": blockers,
                "replay_available": True,
                "replay_note": "Canonical frozen investigation trajectory; replayed with zero provider calls.",
                "termination_reason": termination,
                "tool_call_count": tool_calls,
                "evidence_relations": sorted({str(relation.get("relation_id")) for relation in relations if isinstance(relation, dict) and relation.get("relation_id")}),
                "frozen_trajectory": True,
            }

        projection: dict[str, dict[str, Any]] = {}
        for case_id, row in self._cases["frozen-eval-v3"].items():
            if case_id in stage3:
                evaluation = stage3[case_id]
            else:
                decision = row["_stage2_decision"]
                if decision is None or decision.status.value != "resolved":
                    raise self._v3_projection_error(f"no recorded final disposition for {case_id}")
                tier = {
                    "direct_key.v1": "T0",
                    "derived_reconciliation.v1": "T1",
                }.get(decision.matcher_id)
                evaluation = {
                    "tier": tier, "final_disposition": "RESOLVED", "resolution_stage": "STAGE_2",
                    "resolution_method": decision.rule_id, "blockers": [], "replay_available": False,
                    "replay_note": "Resolved by Stage 2 before an investigation trajectory was needed.",
                    "termination_reason": "stage2_deterministic_resolution", "tool_call_count": 0,
                    "evidence_relations": [], "frozen_trajectory": False,
                }
            if evaluation["tier"] not in {"T0", "T1", "T2", "T3"}:
                raise self._v3_projection_error(f"no safe tier is available for {case_id}")
            projection[case_id] = evaluation

        aggregate = {
            "total": len(projection),
            "resolved": sum(item["final_disposition"] == "RESOLVED" for item in projection.values()),
            "escalated": sum(item["final_disposition"] == "ESCALATED" for item in projection.values()),
            "stage2": sum(item["resolution_stage"] == "STAGE_2" and item["final_disposition"] == "RESOLVED" for item in projection.values()),
            "stage3": sum(item["resolution_stage"] == "STAGE_3" and item["final_disposition"] == "RESOLVED" for item in projection.values()),
        }
        expected = {
            "total": 890,
            "resolved": 650 + report.get("metrics", {}).get("correct_auto_resolutions", -650),
            "escalated": report.get("metrics", {}).get("escalated"),
            "stage2": 650,
            "stage3": report.get("metrics", {}).get("correct_auto_resolutions"),
        }
        if aggregate != expected:
            raise self._v3_projection_error(f"aggregate mismatch: {aggregate!r} != {expected!r}")
        return projection

    def cases(self, benchmark_id: str, *, outcome: str | None = None, stage: str | None = None,
              tier: str | None = None, termination: str | None = None, replay_only: bool = False,
              controller_rejection: bool = False, offset: int = 0, limit: int = 50,
              search: str | None = None) -> dict[str, Any]:
        self.detail(benchmark_id)
        rows = []
        for row in self._cases[benchmark_id].values():
            evaluation = self._v3_case_evaluations.get(row["case_id"]) if benchmark_id == "frozen-eval-v3" else None
            if evaluation and outcome and evaluation["final_disposition"].casefold() != outcome.casefold():
                continue
            if evaluation and stage and evaluation["resolution_stage"].replace("_", "").casefold() != stage.casefold().replace("_", ""):
                continue
            if evaluation and tier and evaluation["tier"] != tier.upper():
                continue
            if evaluation and termination:
                actual = evaluation.get("termination_reason", "")
                if termination == "provider_failure" and actual != "provider_infrastructure_failure":
                    continue
                if termination != "provider_failure" and actual != termination:
                    continue
            replay_ids = self._replay_ids(benchmark_id, row["case_id"])
            outcomes = {name: self._trajectory_outcome(benchmark_id, name, row["case_id"]) for name in replay_ids}
            values = {value for value in outcomes.values() if value}
            if not evaluation and outcome and outcome not in values:
                continue
            if replay_only and not replay_ids:
                continue
            is_demo = row["case_id"] == CONTROLLER_REJECTION_DEMO
            if controller_rejection and not is_demo:
                continue
            if search and search.casefold() not in row["case_id"].casefold():
                continue
            rows.append({**{key: row[key] for key in ("case_id", "bank_record_id", "narration", "amount_paise", "candidate_count")}, "recorded_outcomes": outcomes, "replay_investigators": replay_ids, "controller_rejection_demo": is_demo, "evaluation": evaluation})
        ordered = sorted(rows, key=lambda item: item["case_id"])
        counts: dict[str, int] = {}
        if benchmark_id == "frozen-eval-v3":
            evaluations = tuple(self._v3_case_evaluations.values())
            counts = {
                "all": len(evaluations),
                "stage2": sum(item["resolution_stage"] == "STAGE_2" for item in evaluations),
                "investigations": sum(item["resolution_stage"] == "STAGE_3" for item in evaluations),
                "t2": sum(item["tier"] == "T2" for item in evaluations),
                "t3": sum(item["tier"] == "T3" for item in evaluations),
                "provider_failure": sum(item.get("termination_reason") == "provider_infrastructure_failure" for item in evaluations),
            }
        return {"benchmark_id": benchmark_id, "total": len(ordered), "offset": offset,
                "limit": limit, "counts": counts, "cases": ordered[offset:offset + limit]}

    def case(self, benchmark_id: str, case_id: str) -> dict[str, Any]:
        self.detail(benchmark_id)
        row = self._cases[benchmark_id].get(case_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "benchmark_case_not_found", "message": f"Case {case_id!r} is not in {benchmark_id}."})
        replay_ids = self._replay_ids(benchmark_id, case_id)
        evaluation = self._v3_case_evaluations.get(case_id) if benchmark_id == "frozen-eval-v3" else None
        trajectory_metadata = self._v3_trajectory_metadata(case_id) if benchmark_id == "frozen-eval-v3" else None
        return {**{key: row[key] for key in ("case_id", "bank_record_id", "narration", "amount_paise", "candidate_count", "candidate_snapshot", "visible_records")}, "recorded_outcomes": {name: self._trajectory_outcome(benchmark_id, name, case_id) for name in replay_ids}, "replay_investigators": replay_ids, "controller_rejection_demo": case_id == CONTROLLER_REJECTION_DEMO, "evaluation": evaluation, "trajectory_metadata": trajectory_metadata, "evaluation_metadata_notice": "Visible inputs, immutable candidate snapshots, and judge-safe post-reconciliation evaluation metadata only. Hidden truth is not included."}

    @cached_property
    def _v3_trajectory_paths(self) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        directory = self.trajectories_root / "frozen-eval-v3-opus5-thinking-final"
        for path in directory.glob("*.json"):
            raw = self._json(path)
            case_id = raw.get("case_id")
            if isinstance(case_id, str):
                paths[case_id] = path
        return paths

    def _v3_trajectory_metadata(self, case_id: str) -> dict[str, Any] | None:
        path = self._v3_trajectory_paths.get(case_id)
        if path is None:
            return None
        trajectory = Trajectory.model_validate_json(path.read_text(encoding="utf-8"))
        return {
            "frozen_replay": True,
            "termination_reason": trajectory.termination_reason,
            "tool_call_count": sum(call.status == "succeeded" for call in trajectory.tool_invocations),
            "requested_models": list(trajectory.models_used),
            "reported_models": list(trajectory.models_reported),
            "provider_chain": list(trajectory.provider_chain),
            "replayed": True,
        }

    def full_replay(self, benchmark_id: str) -> dict[str, Any]:
        if benchmark_id != "frozen-eval-v3":
            raise HTTPException(status_code=404, detail={"code": "benchmark_full_replay_unavailable", "message": "A full offline replay is available only for Frozen Eval v3."})
        try:
            # Resolved only inside this explicit evaluation endpoint. Keeping
            # the evaluator out of the production import graph preserves the
            # one-way dependency: Stage 4 may use FinRecon; reconciliation
            # modules never import Stage 4 or receive its hidden inputs.
            module = importlib.import_module("benchmark.eval.frozen_v3_replay")
            return module.run_frozen_v3_replay(self.project_root)
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail={"code": "benchmark_full_replay_failed", "message": f"Frozen Eval v3 replay failed closed: {exc}"}) from exc

    @cached_property
    def _trajectory_index(self) -> dict[str, dict[str, Path]]:
        indexed: dict[str, dict[str, Path]] = {name: {} for name in TRAJECTORY_DIRECTORIES}
        for investigator, directory in TRAJECTORY_DIRECTORIES.items():
            for path in (self.trajectories_root / directory).glob("*.json"):
                try:
                    raw = self._json(path)
                except HTTPException:
                    continue
                case_id = raw.get("case_id")
                if isinstance(case_id, str):
                    indexed[investigator][case_id] = path
        return indexed

    def _trajectory_path(self, investigator: str, case_id: str) -> Path | None:
        directory = TRAJECTORY_DIRECTORIES.get(investigator)
        if directory is None:
            return None
        return self._trajectory_index[investigator].get(case_id)

    def _replay_ids(self, benchmark_id: str, case_id: str) -> list[str]:
        if benchmark_id == "frozen-eval-v3":
            return ["opus-provider-recovered"] if self._trajectory_path("opus-provider-recovered", case_id) is not None else []
        if benchmark_id == "bounded-search-v1":
            return [name for name in ("openrouter-free", "opus") if self._trajectory_path(name, case_id) is not None]
        return []

    def _trajectory_outcome(self, benchmark_id: str, investigator: str, case_id: str) -> str:
        path = self._trajectory_path(investigator, case_id)
        if path is None:
            return ""
        try:
            trajectory = Trajectory.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            return "malformed"
        if trajectory.had_validation_failure:
            return "tool_validation_failure"
        if trajectory.budget_exhausted:
            return "budget_exhausted"
        return "recorded"

    def replays(self, benchmark_id: str) -> dict[str, Any]:
        self.detail(benchmark_id)
        if benchmark_id == "frozen-eval-v3":
            metadata = [self._v3_trajectory_metadata(case_id) for case_id in self._v3_trajectory_paths]
            reported = sorted({model for item in metadata if item for model in item["reported_models"]})
            return {"benchmark_id": benchmark_id, "replays": [{
                "investigator": "opus-provider-recovered",
                "label": "Provider-recovered frozen corpus",
                "scored_cohort_cases": 240,
                "persisted_trajectory_cases": len(self._v3_trajectory_paths),
                "requested_model": "claude-opus-5-thinking",
                "reported_models": reported,
                "provider": "gorouter",
                "notes": ["Canonical recorded replay — zero provider calls.", "Deterministic validation and policy are re-adjudicated from the immutable snapshot."],
            }]}
        if benchmark_id != "bounded-search-v1":
            return {"benchmark_id": benchmark_id, "replays": []}
        reports = {item["report_id"]: item for item in self.reports(benchmark_id)["reports"]}
        specs = [("openrouter-free", "OpenRouter Free", 45, "openrouter", "openrouter/free", "OpenRouter Free has 50 persisted files; five are excluded from the 45-case valid provider-response scored cohort."), ("opus", "Opus", 50, "gorouter", "claude-opus-5-thinking", "50 persisted trajectories and a complete 50-case scored cohort; requested claude-opus-5-thinking is distinct from provider-reported claude-opus-5.")]
        items = []
        for investigator, label, cohort_cases, provider, requested, note in specs:
            directory = {"openrouter-free": "bounded-search-v1-openrouter-free-final", "opus": "bounded-search-v1-opus5-thinking-final"}[investigator]
            report = reports["openrouter-free" if investigator == "openrouter-free" else "opus"]
            reported = sorted((report.get("telemetry", {}).get("models_reported") or {}).keys())
            items.append({"investigator": investigator, "label": label, "scored_cohort_cases": cohort_cases, "persisted_trajectory_cases": len(list((self.trajectories_root / directory).glob("*.json"))), "requested_model": requested, "reported_models": reported, "provider": provider, "notes": [note, "Recorded replay — zero provider calls."]})
        return {"benchmark_id": benchmark_id, "replays": items}

    def replay(self, benchmark_id: str, investigator: str, case_id: str) -> dict[str, Any]:
        self.detail(benchmark_id)
        allowed = (
            benchmark_id == "bounded-search-v1" and investigator in {"openrouter-free", "opus"}
        ) or (
            benchmark_id == "frozen-eval-v3" and investigator == "opus-provider-recovered"
        )
        if not allowed:
            raise HTTPException(status_code=404, detail={"code": "benchmark_replay_unavailable", "message": "No persisted replay is available for this benchmark/investigator."})
        path = self._trajectory_path(investigator, case_id)
        if path is None:
            raise HTTPException(status_code=404, detail={"code": "benchmark_replay_unavailable", "message": "No persisted replay exists for this case; FinRecon will not fabricate one."})
        try:
            trajectory = Trajectory.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise HTTPException(status_code=503, detail={"code": "benchmark_trajectory_malformed", "message": "The persisted trajectory is malformed and cannot be replayed safely."}) from exc
        snapshot = self._cases[benchmark_id].get(case_id, {}).get("_snapshot")
        if snapshot is None:
            raise HTTPException(status_code=409, detail={"code": "benchmark_replay_snapshot_unavailable", "message": "The persisted trajectory has no immutable candidate snapshot to bind against."})
        if snapshot.content_hash != trajectory.snapshot_hash or not snapshot.verify_integrity():
            raise HTTPException(status_code=409, detail={"code": "benchmark_replay_snapshot_mismatch", "message": "The persisted trajectory does not match the immutable candidate snapshot; replay is refused."})
        if (
            trajectory.validator_version != VALIDATOR_VERSION
            or trajectory.policy_version != POLICY_VERSION
            or trajectory.policy_declaration != DEFAULT_POLICY.describe()
        ):
            raise HTTPException(status_code=409, detail={
                "code": "benchmark_replay_version_incompatible",
                "message": "The recorded trajectory was produced under an incompatible validator or policy contract; current re-adjudication is refused.",
            })
        validator, decision = adjudicate(snapshot=snapshot, trajectory=trajectory, claimed_settlement_ids=frozenset(), policy=DEFAULT_POLICY)
        # The persisted artifact remains untouched. This is an offline
        # deterministic re-adjudication over its recorded raw outputs; it
        # constructs no provider chain and has no ground-truth input.
        trajectory_payload = trajectory.model_dump(mode="json")
        provenance: dict[str, Any] | None = None
        if benchmark_id == "frozen-eval-v3":
            # Assistant prose is not evidence and may contain hidden reasoning.
            # Replay exposes actions/results only, never chain-of-thought.
            for step in trajectory_payload.get("steps", []):
                step.pop("assistant_text", None)
                step.pop("usage", None)
            original_cases = self._v3_original_failure_cases
            recovered = case_id in original_cases
            provenance = {
                "provider_recovered_case": recovered,
                "canonical_trajectory": "resolved through deterministic policy" if decision.outcome == "RESOLVE" else "safely escalated",
                "original_operational_attempt": "provider infrastructure failure" if recovered else None,
                "original_failed_trajectory_preserved": recovered,
            }
        return {"benchmark_id": benchmark_id, "investigator": investigator, "replayed": True, "provider_calls_made": False, "trajectory": trajectory_payload, "deterministic_validation": validator.model_dump(mode="json"), "policy_result": decision.model_dump(mode="json"), "provenance": provenance}

    @cached_property
    def _v3_original_failure_cases(self) -> frozenset[str]:
        case_ids: set[str] = set()
        for path in (self.trajectories_root / ORIGINAL_V3_FAILURE_DIRECTORY).glob("*.json"):
            raw = self._json(path)
            case_id = raw.get("case_id")
            if isinstance(case_id, str):
                case_ids.add(case_id)
        return frozenset(case_ids)
