"""The portable, API-key-free submission command's contract."""

from __future__ import annotations

import json

from benchmark.final_eval import run


def test_submission_evaluation_covers_all_intended_cohorts(benchmark_dir, tmp_path):
    report = run(
        benchmark_dir,
        tmp_path / "final-eval.json",
        tmp_path / "final-eval.md",
    )

    assert report["integrity"]["frozen_hash_verified"] is True
    assert report["integrity"]["frozen_eval_sha256"] == (
        "f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b"
    )
    assert report["mode"]["provider_calls_made"] is False
    assert report["replay_coverage"]["frozen-eval"] == {
        "all_cases": 890,
        "stage3_residual_cases": 240,
        "covered": 240,
        "uncovered": 0,
        "reason": "Stage-2 resolved the remaining cases.",
    }
    assert report["replay_coverage"]["v4-pilot"]["covered"] == 64
    assert report["frozen_core"]["metrics"]["wrong_auto_resolutions"] == 0
    assert report["frozen_core"]["metrics"]["value_at_risk_paise"] == 0
    assert json.loads((tmp_path / "final-eval.json").read_text(encoding="utf-8")) == report
    assert "Experimental adversarial pilot — NON-PRODUCTION" in (
        tmp_path / "final-eval.md"
    ).read_text(encoding="utf-8")
