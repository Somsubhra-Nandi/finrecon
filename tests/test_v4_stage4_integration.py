"""Stage 4 over the v4 pilot, and Stage 4 over benchmark v3, in one file.

Both halves matter and they are deliberately adjacent. Extending the
evaluator to slice by v4 family is only useful if the same evaluator still
reports v3 exactly as it did, so the v3 regression sits next to the v4
feature rather than in a distant file where it can be forgotten.

**No provider is constructed anywhere here.** The trajectories are produced by
:class:`tests.stage3_fakes.MechanicalInvestigator`, a deterministic
non-linguistic stand-in that brute-forces narration fragments because it
cannot read one. Nothing it produces is a model result, and no coverage figure
below is presented as one -- the assertions are about whether the *reporting*
works, not about whether anything is good at reconciliation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finrecon.agent.cache import TrajectoryCache
from finrecon.agent.providers.chain import ProviderChain
from finrecon.ledger.store import LedgerStore
from finrecon.pipeline import process_batch
from finrecon.stage3 import run_stage3

from benchmark.eval.evaluate import EvaluationConfig, evaluate
from tests.stage3_fakes import MechanicalInvestigator

FAKE_PROVIDER = "mechanical"
FAKE_MODEL = "mechanical-investigator-v1"


def _record_trajectories(
    benchmark_dir: Path, split: str, directory: Path, case_ids=None
):
    """Run Stage 3 with the deterministic fake and leave its trajectories on disk."""
    store = LedgerStore(":memory:")
    try:
        batch = process_batch(store=store, benchmark_dir=benchmark_dir, split=split)
        result = run_stage3(
            store=store,
            batch_result=batch,
            chain=ProviderChain((MechanicalInvestigator(),)),
            cache=TrajectoryCache(directory),
            case_ids=case_ids,
        )
        return result
    finally:
        store.close()


@pytest.fixture(scope="module")
def v4_evaluation(benchmark_dir, tmp_path_factory):
    directory = tmp_path_factory.mktemp("v4-trajectories")
    _record_trajectories(benchmark_dir, "v4-pilot", directory)
    config = EvaluationConfig(
        benchmark_dir=benchmark_dir,
        split="v4-pilot",
        trajectory_dirs=(directory,),
        provider_id=FAKE_PROVIDER,
        model=FAKE_MODEL,
        label="v4-pilot-mechanical",
    )
    staging = tmp_path_factory.mktemp("v4-staging")
    return evaluate(config, staging_dir=staging / "trajectories")


@pytest.fixture(scope="module")
def v3_evaluation(benchmark_dir, tmp_path_factory):
    """A ten-case DEV cohort, so the v3 path is exercised without a full run."""
    directory = tmp_path_factory.mktemp("v3-trajectories")
    probe = LedgerStore(":memory:")
    try:
        batch = process_batch(store=probe, benchmark_dir=benchmark_dir, split="dev")
        chosen = frozenset(s.case_id for s in batch.snapshots[:10])
    finally:
        probe.close()

    _record_trajectories(benchmark_dir, "dev", directory, case_ids=chosen)
    config = EvaluationConfig(
        benchmark_dir=benchmark_dir,
        split="dev",
        trajectory_dirs=(directory,),
        cohort_ids=tuple(sorted(chosen)),
        provider_id=FAKE_PROVIDER,
        model=FAKE_MODEL,
        label="dev-regression",
    )
    staging = tmp_path_factory.mktemp("v3-staging")
    return evaluate(config, staging_dir=staging / "trajectories")


class TestTheEvaluatorReportsV4Families:
    def test_the_cohort_is_the_whole_pilot(self, v4_evaluation):
        cohort = v4_evaluation.report["cohort"]
        assert cohort["found_count"] == 64
        assert cohort["complete"] is True
        assert cohort["tier_counts"] == {"V4": 64}

    def test_family_metrics_are_reported_for_every_declared_family(self, v4_evaluation):
        from finrecon.benchmark.generator_v4.families import FAMILIES

        block = v4_evaluation.report["metrics_by_family"]
        assert set(block) == set(FAMILIES)
        for family, metrics in block.items():
            assert metrics["cases"] > 0, family
            assert set(metrics) >= {
                "cases",
                "uniquely_resolvable",
                "truly_ambiguous",
                "auto_resolved",
                "correct_auto_resolutions",
                "wrong_auto_resolutions",
                "escalated",
                "match_rate",
                "value_at_risk_paise",
            }

    def test_families_overlap_rather_than_partition(self, v4_evaluation):
        """A case in three families is counted three times, on purpose.

        Stated as a test because the alternative reading -- that the family
        counts should sum to the cohort -- would make a reader think the block
        was broken when it is behaving exactly as designed.
        """
        block = v4_evaluation.report["metrics_by_family"]
        assert sum(metrics["cases"] for metrics in block.values()) > 64

    def test_composition_metrics_partition_the_cohort(self, v4_evaluation):
        """Exactly one required composition per case, so these do sum."""
        block = v4_evaluation.report["metrics_by_required_composition"]
        assert sum(metrics["cases"] for metrics in block.values()) == 64
        assert set(block) == {
            "single_fragment",
            "fragment_pair",
            "fragment_triple",
            "fragment_and_breakup_amount",
            "fragment_and_value_date",
            "none",
        }

    def test_candidate_count_metrics_partition_the_cohort(self, v4_evaluation):
        block = v4_evaluation.report["metrics_by_candidate_count"]
        assert set(block) == {"3", "4", "5"}
        assert sum(metrics["cases"] for metrics in block.values()) == 64

    def test_archetype_metrics_partition_the_cohort(self, v4_evaluation):
        block = v4_evaluation.report["metrics_by_archetype"]
        assert sum(metrics["cases"] for metrics in block.values()) == 64

    def test_the_ambiguous_composition_slice_has_no_resolvable_cases(self, v4_evaluation):
        none_slice = v4_evaluation.report["metrics_by_required_composition"]["none"]
        assert none_slice["cases"] == 16
        assert none_slice["uniquely_resolvable"] == 0
        assert none_slice["match_rate"] is None, "0/0 is not a score"

    def test_the_evaluation_made_no_provider_call(self, v4_evaluation):
        guarantee = v4_evaluation.report["offline_guarantee"]
        assert guarantee["provider_calls_made"] is False
        assert guarantee["replay_only"] is True
        assert guarantee["chain"] is None

    def test_the_soundness_checks_ran_and_found_nothing(self, v4_evaluation):
        soundness = v4_evaluation.report["soundness"]
        assert soundness["checks_available"] is True
        assert soundness["total_violations"] == 0, soundness["violations"][:5]

    def test_every_wrong_resolution_names_a_case_with_no_correct_answer(
        self, v4_evaluation
    ):
        """The fake investigator is not a model, but the gate that judges it is.

        Whatever this arm resolves wrongly must be a case that had no right
        answer -- which on this pilot means the stale-reference archetype. A
        wrong resolution of a *resolvable* case would mean the benchmark
        misleads, and that is a benchmark defect rather than an arm's result.
        """
        for wrong in v4_evaluation.report["wrong_resolutions"]:
            assert wrong["truth_settlement_ids"] == [], wrong["case_id"]
            assert wrong["archetype"] == "conflict_stale_reference", wrong


class TestBenchmarkV3EvaluationIsUnchanged:
    def test_a_dev_cohort_still_evaluates(self, v3_evaluation):
        cohort = v3_evaluation.report["cohort"]
        assert cohort["found_count"] == 10
        assert cohort["complete"] is True

    def test_dev_tier_metrics_are_still_reported(self, v3_evaluation):
        block = v3_evaluation.report["metrics_by_tier"]
        assert block
        assert set(block) <= {"T0", "T1", "T2", "T3"}
        assert sum(metrics["cases"] for metrics in block.values()) == 10

    def test_a_v3_cohort_reports_no_families_rather_than_empty_families(
        self, v3_evaluation
    ):
        """Empty, not zero-filled. v1-v3 have no families; they do not have zero."""
        assert v3_evaluation.report["metrics_by_family"] == {}
        assert v3_evaluation.report["metrics_by_required_composition"] == {}

    def test_a_v3_cohort_reports_an_unknown_candidate_count_bucket(self, v3_evaluation):
        block = v3_evaluation.report["metrics_by_candidate_count"]
        assert set(block) == {"unknown"}
        assert block["unknown"]["cases"] == 10

    def test_the_headline_metric_block_is_unchanged_in_shape(self, v3_evaluation):
        metrics = v3_evaluation.report["metrics"]
        for key in (
            "investigated",
            "auto_resolved",
            "correct_auto_resolutions",
            "wrong_auto_resolutions",
            "escalated",
            "auto_resolution_accuracy",
            "overall_match_rate",
            "auto_resolution_coverage",
            "unsafe_auto_match_rate",
            "escalation_recall",
            "value_at_risk_paise",
        ):
            assert key in metrics, key

    def test_no_dev_case_is_resolved_incorrectly(self, v3_evaluation):
        assert v3_evaluation.report["metrics"]["wrong_auto_resolutions"] == 0


class TestFrozenBenchmarkV3IsUntouched:
    def test_the_frozen_eval_fingerprint_is_unchanged(self, benchmark_dir):
        """The one assertion that would stop everything if it failed."""
        from finrecon.benchmark.generator.hashing import compute_fingerprint

        assert (
            compute_fingerprint(benchmark_dir, "frozen-eval")
            == "f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b"
        )

    def test_the_v3_manifest_is_unchanged(self, benchmark_dir):
        import json

        manifest = json.loads(
            (benchmark_dir / "manifests" / "v3.json").read_text(encoding="utf-8")
        )
        assert manifest["generator_version"] == "3.0.0"
        assert manifest["dev_seed"] == 42
        assert manifest["frozen_eval_seed"] == 1337
        assert manifest["target_tier_counts"] == {
            "T0": 350,
            "T1": 300,
            "T2": 200,
            "T3": 40,
        }

    def test_the_v4_pilot_has_its_own_manifest_file(self, benchmark_dir):
        """Additive means additive: four manifests, none of them rewritten."""
        names = sorted(p.name for p in (benchmark_dir / "manifests").glob("*.json"))
        assert names == ["v1.json", "v2.json", "v3.json", "v4-pilot.json"]


class TestHistoricalValidatorV1ArtifactsFailClosed:
    """A v1 trajectory must never be silently reinterpreted as a v2 result.

    ``validator_version`` is part of the trajectory cache key, so the bump to
    ``validator.v2`` changes every key. That is the mechanism, and these tests
    are its two halves: the key really does change, and a stored artifact whose
    key no longer matches stops the run rather than being scored.
    """

    def test_the_cache_key_changes_with_the_validator_version(self, v4_stage2):
        """The claim the whole replay guarantee rests on."""
        from dataclasses import replace

        from finrecon.agent.cache import cache_key_inputs
        from finrecon.decide.config import DEFAULT_POLICY

        batch, _store = v4_stage2
        inputs = cache_key_inputs(
            batch.snapshots[0],
            provider=FAKE_PROVIDER,
            model=FAKE_MODEL,
            policy=DEFAULT_POLICY,
        )
        assert inputs.validator_version == "validator.v2"
        as_v1 = replace(inputs, validator_version="validator.v1")
        assert as_v1.key() != inputs.key()

    def test_the_policy_version_is_not_part_of_this_change(self, v4_stage2):
        """Only the validator moved, so only the validator's identity moved."""
        from finrecon.agent.cache import cache_key_inputs
        from finrecon.decide.config import DEFAULT_POLICY

        batch, _store = v4_stage2
        inputs = cache_key_inputs(
            batch.snapshots[0],
            provider=FAKE_PROVIDER,
            model=FAKE_MODEL,
            policy=DEFAULT_POLICY,
        )
        assert inputs.policy_version == "policy.v1"
        assert inputs.prompt_version == "investigator.v4"
        assert inputs.tool_schema_version == "tools.v3"
        assert inputs.agent_loop_version == "loop.v2"
        assert inputs.cache_schema_version == "trajectory-cache.v3"

    def test_a_trajectory_recorded_under_another_contract_aborts_the_evaluation(
        self, benchmark_dir, tmp_path_factory
    ):
        """Fail closed, loudly, with the versions named -- never a silent rescore."""
        import json

        from benchmark.eval.errors import EvaluationError

        directory = tmp_path_factory.mktemp("stale-trajectories")
        _record_trajectories(benchmark_dir, "v4-pilot", directory)

        # Rewrite each artifact as though it had been produced under the
        # superseded contract: the key it was filed under no longer matches what
        # today's tree computes for the same case.
        for path in sorted(directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["validator_version"] = "validator.v1"
            payload["cache_key"] = "stale" + payload["cache_key"][5:]
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.rename(path.with_name(f"{payload['cache_key']}.json"))

        config = EvaluationConfig(
            benchmark_dir=benchmark_dir,
            split="v4-pilot",
            trajectory_dirs=(directory,),
            provider_id=FAKE_PROVIDER,
            model=FAKE_MODEL,
            label="stale-contract",
        )
        staging = tmp_path_factory.mktemp("stale-staging")
        with pytest.raises(EvaluationError) as raised:
            evaluate(config, staging_dir=staging / "trajectories")
        assert "validator" in str(raised.value).lower() or "replay" in str(
            raised.value
        ).lower()

    def test_comparison_mode_treats_the_validator_version_as_a_dimension(self):
        """A v1-vs-v2 comparison must be attributed to the validator, not the model."""
        from benchmark.eval.compare import CONFIGURATION_DIMENSIONS

        assert "validator_version" in CONFIGURATION_DIMENSIONS

    def test_a_v2_report_records_the_validator_version_it_was_produced_under(
        self, v4_evaluation
    ):
        recorded = v4_evaluation.report["recorded_versions"]
        assert recorded["validator_version"] == ["validator.v2"]
        assert recorded["policy_version"] == ["policy.v1"]


class TestConjunctiveProvenanceIsReported:
    """validator.v2's evidence shape, in the offline report.

    A version bump makes numbers incomparable across runs unless the report
    says what shape of evidence they rest on, so the shape is reported rather
    than inferred.
    """

    def test_the_report_carries_a_conjunction_block(self, v4_evaluation):
        block = v4_evaluation.report["conjunction"]
        assert block["closure_is_the_decision_input"] is True
        assert set(block) >= {
            "resolutions_total",
            "resolutions_needing_conjunction",
            "resolutions_from_a_single_claim",
            "reference_evidence_states",
            "informative_atoms_per_case",
            "independent_narration_spans_per_case",
            "final_intersection_size",
            "agent_atom_coverage",
        }

    def test_the_pilot_s_conjunctive_resolutions_are_counted(self, v4_evaluation):
        """26 of the pilot's 38 resolutions need composition; 12 do not.

        The twelve split two ways, and the split is worth reading. Eight are the
        ``single_fragment_control`` archetype, which exists so that "everything
        needed composition" cannot be an artefact of a broken single-claim path.
        The other four are ``conflict_stale_reference`` -- resolved, wrongly, on
        a single claim that no reference evidence contradicts. That those four
        are single-claim is the whole shape of the remaining gap: composition
        did not cause them and composition cannot fix them, because what
        refutes them is a value date rather than a reference.
        """
        block = v4_evaluation.report["conjunction"]
        assert block["resolutions_needing_conjunction"] == 26
        assert block["resolutions_from_a_single_claim"] == 12
        assert block["resolutions_total"] == 38

    def test_every_reference_state_reported_is_a_declared_one(self, v4_evaluation):
        from finrecon.decide.validator import REFERENCE_STATES

        states = v4_evaluation.report["conjunction"]["reference_evidence_states"]
        assert set(states) <= set(REFERENCE_STATES)
        assert sum(states.values()) == 64

    def test_agent_atom_coverage_is_reported_without_gating_anything(
        self, v4_evaluation
    ):
        block = v4_evaluation.report["conjunction"]
        assert 0.0 < block["agent_atom_coverage"] <= 1.0
        assert block["informative_atoms_surfaced_by_agent"] <= (
            block["informative_atoms_total"]
        )

    def test_a_v3_cohort_reports_no_conjunctive_resolutions(self, v3_evaluation):
        """benchmark v3 T2 turns on one recovered reference, and still does."""
        block = v3_evaluation.report["conjunction"]
        assert block["resolutions_needing_conjunction"] == 0
        assert block["resolutions_from_a_single_claim"] == block["resolutions_total"]

    def test_wrong_resolutions_still_come_only_from_the_stale_archetype(
        self, v4_evaluation
    ):
        """v2 added no new unsafe resolution, which is the bar that mattered."""
        wrong = v4_evaluation.report["wrong_resolutions"]
        assert len(wrong) == 4
        assert {w["archetype"] for w in wrong} == {"conflict_stale_reference"}
