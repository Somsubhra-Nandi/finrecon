"""Complete product replay contract for the immutable Frozen Eval v3 corpus."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

from benchmark.eval import frozen_v3_replay
from finrecon.api.app import create_app
from finrecon.api.schemas import BenchmarkFullReplayResponse


def _tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file():
            digest.update(item.relative_to(path).as_posix().encode())
            digest.update(item.read_bytes())
    return digest.hexdigest()


def test_complete_frozen_replay_is_offline_ordered_and_read_only(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    protected = [
        root / "fixtures" / "trajectories" / "frozen-eval-v3-opus5-thinking-final",
        root / "fixtures" / "trajectories" / "frozen-eval-v3-opus5-thinking-t2-provider-failures-original",
        root / "benchmark" / "reports",
        root / "benchmark" / "datasets" / "frozen-eval",
    ]
    before = {path: _tree_digest(path) for path in protected}
    monkeypatch.delenv("GOROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def provider_must_not_be_constructed(*_args, **_kwargs):
        raise AssertionError("offline benchmark replay must not construct a provider")

    monkeypatch.setattr("finrecon.agent.providers.config.build_chain", provider_must_not_be_constructed)
    events: list[str] = []
    real_replay = frozen_v3_replay.replay_cohort
    real_truth = __import__("benchmark.eval.groundtruth", fromlist=["load_ground_truth"]).load_ground_truth

    def replay_spy(*args, **kwargs):
        events.append("reconciliation_complete")
        return real_replay(*args, **kwargs)

    def truth_spy(*args, **kwargs):
        events.append("truth_loaded")
        return real_truth(*args, **kwargs)

    monkeypatch.setattr(frozen_v3_replay, "replay_cohort", replay_spy)
    monkeypatch.setattr("benchmark.eval.groundtruth.load_ground_truth", truth_spy)

    with TestClient(create_app(ledger_path=tmp_path / "ledger.sqlite3")) as client:
        response = client.post("/api/benchmarks/frozen-eval-v3/replay")

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["mode"] == "offline_replay"
    assert result["total_cases"] == 890
    assert result["total_correct_auto_resolutions"] == 850
    assert result["stage2"] == {"cases": 650, "resolved": 650}
    assert result["stage3"]["residual_cases"] == 240
    assert result["stage3"]["trajectory_cache_hits"] == 240
    assert result["stage3"]["t2"] == {"cases": 200, "correctly_resolved": 200}
    assert result["stage3"]["t3"]["cases"] == 40
    assert result["stage3"]["t3"]["safely_escalated"] == 40
    assert result["stage3"]["t3"]["termination_reasons"] == {
        "investigation_complete": 33,
        "provider_infrastructure_failure": 7,
    }
    assert result["evaluation"]["resolvable_cases"] == 850
    assert result["evaluation"]["correct_resolutions"] == 850
    assert result["evaluation"]["wrong_auto_resolutions"] == 0
    assert result["evaluation"]["value_at_risk_paise"] == 0
    assert result["evaluation"]["soundness_violations"] == 0
    assert result["evaluation"]["tool_validation_failures"] == 0
    assert result["evaluation"]["validation_rejections"] == 0
    assert result["evaluation"]["budget_exhausted"] == 0
    assert result["provider_calls"] == 0
    assert result["provider_calls_made"] is False
    assert result["total_cases"] == (
        result["stage2"]["resolved"]
        + result["stage3"]["t2"]["correctly_resolved"]
        + result["stage3"]["t3"]["safely_escalated"]
    )
    assert result["total_correct_auto_resolutions"] == (
        result["stage2"]["resolved"]
        + result["stage3"]["t2"]["correctly_resolved"]
    )
    serialized = response.text.casefold()
    for forbidden in (
        "truth_reference",
        "truth_settlement_ids",
        "expected_candidate",
        "correct_relationship",
    ):
        assert forbidden not in serialized
    assert result["provenance"]["provider_recovered"]["t2_correctly_resolved"] == 200
    assert result["provenance"]["operational"]["t2_correctly_resolved"] == 187
    assert result["provenance"]["operational"]["t2_provider_infrastructure_failures"] == 13
    assert result["provenance"]["original_failed_trajectories_preserved"] == 13
    assert events == ["reconciliation_complete", "truth_loaded"]
    assert {path: _tree_digest(path) for path in protected} == before

    incompatible = deepcopy(result)
    incompatible["stage3"]["t2"]["correctly_resolved"] = 173
    incompatible["stage3"]["t3"]["safely_escalated"] = 67
    try:
        BenchmarkFullReplayResponse.model_validate(incompatible)
    except ValidationError as error:
        assert "full-suite" in str(error)
        assert "do not reconcile" in str(error)
    else:
        raise AssertionError("incompatible benchmark cohorts must fail validation")
