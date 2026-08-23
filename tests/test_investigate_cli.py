"""The Stage-3 diagnostic CLI: what it prints, and what it refuses to do.

Small surface, three claims worth pinning:

* it never prints a credential, only whether one is set;
* it reports operational facts and **no accuracy**, matching
  ``reconcile_cli`` and DESIGN.md §7's "a production system has no ground
  truth";
* both of its failure modes -- no credential, empty fixture corpus -- are
  loud and non-zero rather than a run that quietly did nothing and looked
  like a result.
"""

from __future__ import annotations

import pytest

from finrecon import investigate_cli

SECRET = "sk-cli-must-not-print-0123456789"


class TestArguments:
    def test_the_defaults_are_dev_and_the_committed_fixture_directory(self):
        args = investigate_cli.build_parser().parse_args([])
        assert args.split == "dev"
        assert args.replay_only is False
        assert "trajectories" in args.fixtures

    def test_a_live_run_can_be_limited_to_a_handful_of_cases(self):
        args = investigate_cli.build_parser().parse_args(["--limit", "4"])
        assert args.limit == 4

    def test_individual_cases_can_be_named(self):
        args = investigate_cli.build_parser().parse_args(
            ["--case", "case:bnk_dev_000003", "--case", "case:bnk_dev_000005"]
        )
        assert len(args.case) == 2


class TestFailsLoudly:
    def test_a_live_run_without_a_credential_exits_non_zero(self, monkeypatch, capsys):
        for name in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        code = investigate_cli.main(["--split", "dev", "--limit", "1"])
        out = capsys.readouterr().out
        assert code == 2
        assert "cannot start a live run" in out
        assert "OPENROUTER_API_KEY" in out

    def test_a_replay_miss_exits_non_zero_without_reaching_a_provider(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
        code = investigate_cli.main(
            [
                "--split",
                "dev",
                "--limit",
                "1",
                "--replay-only",
                "--fixtures",
                str(tmp_path / "empty"),
            ]
        )
        out = capsys.readouterr().out
        assert code == 2
        assert "replay miss" in out
        assert "zero provider calls" in out


class TestNoSecretsAndNoAccuracy:
    def test_the_printed_configuration_never_contains_a_credential(
        self, monkeypatch, capsys, tmp_path
    ):
        monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
        investigate_cli.main(
            ["--split", "dev", "--limit", "1", "--replay-only", "--fixtures", str(tmp_path)]
        )
        out = capsys.readouterr().out
        assert SECRET not in out
        assert '"credential_present": true' in out.lower()

    def test_the_provider_and_model_choice_is_printed_for_any_run(
        self, monkeypatch, capsys, tmp_path
    ):
        monkeypatch.setenv("GROQ_API_KEY", SECRET)
        investigate_cli.main(
            ["--split", "dev", "--limit", "1", "--replay-only", "--fixtures", str(tmp_path)]
        )
        out = capsys.readouterr().out
        assert "cache identity:" in out
        assert "provider configuration:" in out

    @pytest.mark.parametrize(
        "forbidden", ["match rate", "precision", "accuracy", "correct", "wrong"]
    )
    def test_the_cli_source_reports_no_accuracy_metric(self, forbidden):
        """Stage 4 owns metrics. Stage 3 must not grow a quiet scoreboard."""
        import inspect

        source = inspect.getsource(investigate_cli).lower()
        printed = [
            line
            for line in source.splitlines()
            if line.strip().startswith("print(") and forbidden in line
        ]
        assert printed == []
