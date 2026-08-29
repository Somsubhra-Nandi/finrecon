"""Read-only benchmark catalogue for the product UI.

This module intentionally has no dependency on ``benchmark.eval`` or any
provider implementation.  It reads only committed manifests, aggregate
reports, system-visible record files, and persisted trajectory JSON.  That
keeps hidden truth outside of both browsing and replay payloads.
"""

from __future__ import annotations

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
        for benchmark_id in ("frozen-eval-v3", "bounded-search-v1", "v4-pilot"):
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
                "benchmark_id": benchmark_id, "title": "Frozen Evaluation v3", "status": "FROZEN",
                "case_count": int(manifest["case_counts"]["frozen-eval"]),
                "description": "Full pipeline safety and regression benchmark.",
                "replay_available": False, "report_available": True, "investigators": [],
                "integrity": {"sha256": manifest["frozen_eval_sha256"], "hash_verified_in_report": True},
                "constraints": {"replay": "No persisted per-case trajectories are exposed.", "ground_truth": "Evaluation-only; never returned by case or replay endpoints."},
                "notices": ["Report and visible-input case exploration only. No replay is fabricated."],
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
                "notices": ["OpenRouter has 50 persisted records but its authoritative scored cohort contains 45 valid provider-response cases.", "Opus has 40 persisted records and a 40-case scored cohort. Mechanical is report/comparison-only."],
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
            paths = [("final-eval", self.benchmark_root / "reports" / "final-eval.json", "Authoritative frozen full-pipeline report")]
        elif benchmark_id == "bounded-search-v1":
            paths = [
                ("mechanical", self.benchmark_root / "reports" / "bounded-search-v1-mechanical.json", "Mechanical baseline; report/comparison only"),
                ("openrouter-free", self.benchmark_root / "reports" / "bounded-search-v1-openrouter-free-valid-45.json", "45-case valid provider-response scored cohort"),
                ("opus", self.benchmark_root / "reports" / "bounded-search-v1-opus5-thinking-valid-40.json", "40-case successfully answered scored cohort"),
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
        return {key: cohort[key] for key in ("requested_count", "found_count", "tier_counts", "sources_contributing") if key in cohort}

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
        _decisions, snapshots, _candidates = reconcile_batch(batch, batch_id)
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
                "visible_records": {"bank_record": bank.model_dump(mode="json"), "settlements": [settlements[item] for item in sorted(settlement_ids)]},
            }
        return result

    def cases(self, benchmark_id: str, *, outcome: str | None = None, replay_only: bool = False, controller_rejection: bool = False) -> dict[str, Any]:
        self.detail(benchmark_id)
        rows = []
        for row in self._cases[benchmark_id].values():
            replay_ids = self._replay_ids(benchmark_id, row["case_id"])
            outcomes = {name: self._trajectory_outcome(benchmark_id, name, row["case_id"]) for name in replay_ids}
            values = {value for value in outcomes.values() if value}
            if outcome and outcome not in values:
                continue
            if replay_only and not replay_ids:
                continue
            is_demo = row["case_id"] == CONTROLLER_REJECTION_DEMO
            if controller_rejection and not is_demo:
                continue
            rows.append({**{key: row[key] for key in ("case_id", "bank_record_id", "narration", "amount_paise", "candidate_count")}, "recorded_outcomes": outcomes, "replay_investigators": replay_ids, "controller_rejection_demo": is_demo})
        return {"benchmark_id": benchmark_id, "total": len(rows), "cases": sorted(rows, key=lambda item: item["case_id"])}

    def case(self, benchmark_id: str, case_id: str) -> dict[str, Any]:
        self.detail(benchmark_id)
        row = self._cases[benchmark_id].get(case_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "benchmark_case_not_found", "message": f"Case {case_id!r} is not in {benchmark_id}."})
        replay_ids = self._replay_ids(benchmark_id, case_id)
        return {**{key: row[key] for key in ("case_id", "bank_record_id", "narration", "amount_paise", "candidate_count", "candidate_snapshot", "visible_records")}, "recorded_outcomes": {name: self._trajectory_outcome(benchmark_id, name, case_id) for name in replay_ids}, "replay_investigators": replay_ids, "controller_rejection_demo": case_id == CONTROLLER_REJECTION_DEMO, "evaluation_metadata_notice": "This endpoint contains visible benchmark inputs and recorded controller artifacts only. Hidden ground truth and Stage-4 verdicts are not included."}

    @cached_property
    def _trajectory_index(self) -> dict[str, dict[str, Path]]:
        indexed: dict[str, dict[str, Path]] = {"openrouter-free": {}, "opus": {}}
        directories = {"openrouter-free": "bounded-search-v1-openrouter-free-final", "opus": "bounded-search-v1-opus5-thinking-final"}
        for investigator, directory in directories.items():
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
        directory = {"openrouter-free": "bounded-search-v1-openrouter-free-final", "opus": "bounded-search-v1-opus5-thinking-final"}.get(investigator)
        if directory is None:
            return None
        return self._trajectory_index[investigator].get(case_id)

    def _replay_ids(self, benchmark_id: str, case_id: str) -> list[str]:
        if benchmark_id != "bounded-search-v1":
            return []
        return [name for name in ("openrouter-free", "opus") if self._trajectory_path(name, case_id) is not None]

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
        if benchmark_id != "bounded-search-v1":
            return {"benchmark_id": benchmark_id, "replays": []}
        reports = {item["report_id"]: item for item in self.reports(benchmark_id)["reports"]}
        specs = [("openrouter-free", "OpenRouter Free", 45, "openrouter", "openrouter/free", "OpenRouter Free has 50 persisted files; five are excluded from the 45-case valid provider-response scored cohort."), ("opus", "Opus", 40, "gorouter", "claude-opus-5-thinking", "40 persisted trajectories; the report distinguishes requested claude-opus-5-thinking from provider-reported claude-opus-5.")]
        items = []
        for investigator, label, cohort_cases, provider, requested, note in specs:
            directory = {"openrouter-free": "bounded-search-v1-openrouter-free-final", "opus": "bounded-search-v1-opus5-thinking-final"}[investigator]
            report = reports["openrouter-free" if investigator == "openrouter-free" else "opus"]
            reported = sorted((report.get("telemetry", {}).get("models_reported") or {}).keys())
            items.append({"investigator": investigator, "label": label, "scored_cohort_cases": cohort_cases, "persisted_trajectory_cases": len(list((self.trajectories_root / directory).glob("*.json"))), "requested_model": requested, "reported_models": reported, "provider": provider, "notes": [note, "Recorded replay — zero provider calls."]})
        return {"benchmark_id": benchmark_id, "replays": items}

    def replay(self, benchmark_id: str, investigator: str, case_id: str) -> dict[str, Any]:
        self.detail(benchmark_id)
        if benchmark_id != "bounded-search-v1" or investigator not in {"openrouter-free", "opus"}:
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
        return {"benchmark_id": benchmark_id, "investigator": investigator, "replayed": True, "provider_calls_made": False, "trajectory": trajectory.model_dump(mode="json"), "deterministic_validation": validator.model_dump(mode="json"), "policy_result": decision.model_dump(mode="json")}
