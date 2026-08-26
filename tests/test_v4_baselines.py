"""The deterministic baseline arms: what they measure, and what they may not touch.

Half of this file is structural. An arm that read hidden ground truth while
deciding would produce a number that means nothing, and an arm that called a
provider would spend money in a test suite -- so both are ruled out by parsing
the package rather than by reviewing it, in the same shape
``tests/test_benchmark_isolation.py`` rules them out for the reconciliation
path.

The other half pins the arms' behaviour on the v4 pilot. Those assertions are
deliberately about *structure* -- which archetypes an arm can and cannot reach
-- rather than about a coverage figure. A test that pinned "arm C2 resolves 48"
would have to be edited every time the pilot's composition changed, and would
quietly become a target.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from benchmark.baselines import arms as arms_module
from benchmark.baselines import features as features_module
from benchmark.baselines import report as report_module
from benchmark.baselines.arms import (
    arm_b1_validator_v1_semantics,
    arm_b_single_fragment,
    arm_c1_lexical_composition,
    arm_c2_lexical_and_structural,
    arm_c3_first_subset_that_isolates,
    exhaustive_fragment_trajectory,
    financially_exact_candidates,
)
from benchmark.baselines.features import (
    date_tokens,
    lexical_features,
    money_tokens,
    narration_fragments,
    structural_features,
)
from benchmark.baselines.report import run_baselines, score_prediction
from benchmark.eval.scoring import verdict_for
from finrecon.decide.config import DEFAULT_POLICY
from finrecon.decide.policy import applicable_min_pinned, decide
from finrecon.decide.validator import raw_tool_evidence, validate_case
from finrecon.stage3 import CaseOutcome

BASELINE_PACKAGE = Path(report_module.__file__).parent

PROVIDER_MARKERS = (
    "openrouter",
    "gorouter",
    "groq",
    "gemini",
    "providers",
    "urllib",
    "requests",
    "httpx",
    "socket",
)


def _baseline_sources() -> list[Path]:
    return sorted(BASELINE_PACKAGE.glob("*.py"))


class TestTheBaselinesAreOfflineAndTruthBlind:
    def test_there_are_baseline_modules_to_check(self):
        assert len(_baseline_sources()) >= 5

    @pytest.mark.parametrize("path", _baseline_sources(), ids=lambda p: p.name)
    def test_no_baseline_module_imports_a_provider_or_a_network_library(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            if not module:
                continue
            for marker in PROVIDER_MARKERS:
                assert marker not in module, (path.name, module)

    def test_the_deciding_modules_never_name_ground_truth(self):
        """``arms`` and ``features`` decide. Neither may reach the answer key.

        ``report`` is excluded and is the only module allowed to load truth,
        because it runs strictly after every arm has returned. The separation
        is the point: inference and scoring are different call frames, and only
        one of them can see the answers.
        """
        for module in (arms_module, features_module):
            source = Path(module.__file__).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert "groundtruth" not in (node.module or "")
                    assert "ground_truth" not in (node.module or "")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "groundtruth" not in alias.name
                        assert "ground_truth" not in alias.name

    def test_an_arm_decides_from_the_snapshot_alone(self, v4_stage2):
        """The snapshot is the arm's entire world; nothing else is passed in."""
        batch, _store = v4_stage2
        snapshot = batch.snapshots[0]
        prediction = arm_c2_lexical_and_structural(snapshot)
        assert prediction.case_id == snapshot.case_id
        assert prediction.candidate_id is None or prediction.candidate_id in (
            snapshot.candidate_ids()
        )


class TestArmBIsTheShippedArchitecture:
    def test_arm_b_drives_the_real_validator_and_the_real_gate(self, v4_stage2):
        """Not an imitation of the ceiling -- the ceiling itself.

        The same snapshot is pushed through arm B and through a hand-rolled
        call to ``validate_case`` / ``decide`` with the same evidence, and the
        two must agree. If arm B ever grew a private rule, this fails.
        """
        batch, _store = v4_stage2
        for snapshot in batch.snapshots[:8]:
            floor = applicable_min_pinned(
                snapshot.base_evidence.bank_record.amount_paise, DEFAULT_POLICY
            )
            trajectory = exhaustive_fragment_trajectory(
                snapshot, lexical_features(snapshot, floor)
            )
            result = validate_case(
                snapshot=snapshot,
                evidence=raw_tool_evidence(trajectory),
                policy=DEFAULT_POLICY,
                min_pinned_reference_characters=floor,
            )
            decision = decide(
                snapshot=snapshot, trajectory=trajectory, validator_result=result
            )
            prediction = arm_b_single_fragment(snapshot)
            assert prediction.resolved == decision.resolved
            assert prediction.settlement_ids == tuple(sorted(decision.resolved_settlement_ids))

    def test_dropping_fragments_that_reach_nothing_changes_no_decision(self, v4_stage2):
        """The one optimisation arm B makes, asserted rather than argued.

        A fragment reaching no candidate produces an empty
        ``matched_candidate_ids`` and ``is_discriminating=False``, so it cannot
        move ``reference_identified_candidate_ids`` or
        ``surviving_candidate_ids``. That is a reading of the validator; this
        is the measurement.
        """
        from finrecon.agent.tools import (
            TOOL_COMPARE_REFERENCE_FRAGMENT,
            TOOLS_BY_NAME,
            ToolContext,
        )
        from finrecon.decide.validator import RawToolEvidence

        batch, _store = v4_stage2
        definition = TOOLS_BY_NAME[TOOL_COMPARE_REFERENCE_FRAGMENT]
        for snapshot in batch.snapshots[:4]:
            floor = applicable_min_pinned(
                snapshot.base_evidence.bank_record.amount_paise, DEFAULT_POLICY
            )
            context = ToolContext(snapshot=snapshot)
            unfiltered = tuple(
                RawToolEvidence(
                    tool_name=TOOL_COMPARE_REFERENCE_FRAGMENT,
                    arguments={"fragment": fragment},
                    output=definition.handler(
                        context, definition.input_model.model_validate({"fragment": fragment})
                    ).model_dump(mode="json"),
                )
                for fragment in narration_fragments(
                    snapshot.base_evidence.bank_record.narration
                )
            )
            full = validate_case(
                snapshot=snapshot,
                evidence=unfiltered,
                min_pinned_reference_characters=floor,
            )
            filtered = validate_case(
                snapshot=snapshot,
                evidence=raw_tool_evidence(
                    exhaustive_fragment_trajectory(snapshot, lexical_features(snapshot, floor))
                ),
                min_pinned_reference_characters=floor,
            )
            assert (
                full.surviving_candidate_ids == filtered.surviving_candidate_ids
            ), snapshot.case_id
            assert (
                full.reference_identified_candidate_ids
                == filtered.reference_identified_candidate_ids
            )

    def test_arm_b_agrees_with_the_stage_four_correctness_predicate(
        self, v4_stage2, v4_truth
    ):
        """The two definitions of correctness must not drift apart.

        Arm B is the only arm that produces a genuine
        :class:`finrecon.stage3.CaseOutcome`, so it is the one place both
        predicates can be run on the same decision. ``benchmark/eval`` scores
        the outcome; ``benchmark/baselines`` scores the prediction; they must
        return the same verdict on every case.
        """
        batch, _store = v4_stage2
        for snapshot in batch.snapshots:
            floor = applicable_min_pinned(
                snapshot.base_evidence.bank_record.amount_paise, DEFAULT_POLICY
            )
            trajectory = exhaustive_fragment_trajectory(
                snapshot, lexical_features(snapshot, floor)
            )
            result = validate_case(
                snapshot=snapshot,
                evidence=raw_tool_evidence(trajectory),
                min_pinned_reference_characters=floor,
            )
            decision = decide(
                snapshot=snapshot, trajectory=trajectory, validator_result=result
            )
            outcome = CaseOutcome(
                case_id=snapshot.case_id,
                snapshot=snapshot,
                trajectory=trajectory,
                validator_result=result,
                decision=decision,
                cache_key="",
                cache_hit=False,
            )
            entry = v4_truth[snapshot.case_id]
            stage4 = verdict_for(outcome, entry)
            baseline = score_prediction(arm_b_single_fragment(snapshot), entry)
            assert stage4.correct == baseline.correct, snapshot.case_id
            assert stage4.wrong_reason == baseline.wrong_reason
            assert stage4.escalation_correct == baseline.escalation_correct

    def test_the_shared_financial_predicate_agrees_with_the_validator(self, v4_stage2):
        batch, _store = v4_stage2
        for snapshot in batch.snapshots[:10]:
            result = validate_case(snapshot=snapshot, evidence=())
            assert financially_exact_candidates(snapshot) == frozenset(
                result.financially_exact_candidate_ids
            )


class TestWhatEachArmCanAndCannotReach:
    """Structure, not scores. Which archetypes are in reach of which strategy."""

    def test_single_fragment_matching_solves_the_control_family(self, v4_stage2, v4_truth):
        batch, _store = v4_stage2
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.archetype != "single_fragment_control":
                continue
            prediction = arm_b_single_fragment(snapshot)
            assert prediction.resolved, snapshot.case_id
            assert prediction.settlement_ids == entry.expected_settlement_ids

    def test_validator_v1_semantics_reach_no_conjunction_case(
        self, v4_stage2, v4_truth
    ):
        """The pilot's headline structural claim, and the before-column of v2.

        Asserted against arm B1 -- the restated ``validator.v1`` rule -- rather
        than against the shipped gate, because the shipped gate now composes.
        The claim itself is unchanged and is what justified the change: no
        single-fragment rule reaches a conjunction case, however exhaustively
        it is fed.
        """
        batch, _store = v4_stage2
        checked = 0
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if not entry.archetype.startswith("conjunction_"):
                continue
            assert not arm_b1_validator_v1_semantics(snapshot).resolved, snapshot.case_id
            checked += 1
        assert checked == 26

    def test_the_shipped_gate_now_reaches_every_conjunction_case(
        self, v4_stage2, v4_truth
    ):
        """What validator.v2 bought, measured through the real validator and gate.

        The same 26 cases arm B1 cannot touch, resolved correctly by the
        shipped decision layer given the same exhaustive lexical evidence.
        """
        batch, _store = v4_stage2
        checked = 0
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if not entry.archetype.startswith("conjunction_"):
                continue
            prediction = arm_b_single_fragment(snapshot)
            assert prediction.resolved, snapshot.case_id
            assert prediction.settlement_ids == entry.expected_settlement_ids
            checked += 1
        assert checked == 26

    def test_lexical_composition_reaches_the_conjunctions_and_stops_there(
        self, v4_stage2, v4_truth
    ):
        batch, _store = v4_stage2
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            prediction = arm_c1_lexical_composition(snapshot)
            if entry.archetype.startswith("conjunction_"):
                assert prediction.resolved, snapshot.case_id
                assert prediction.settlement_ids == entry.expected_settlement_ids
            elif entry.archetype in (
                "amount_reference_hop",
                "conflict_context_resolves",
            ):
                assert not prediction.resolved, snapshot.case_id

    def test_only_structural_composition_reaches_the_cross_modal_archetypes(
        self, v4_stage2, v4_truth
    ):
        batch, _store = v4_stage2
        checked = 0
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.archetype not in (
                "amount_reference_hop",
                "conflict_context_resolves",
            ):
                continue
            prediction = arm_c2_lexical_and_structural(snapshot)
            assert prediction.resolved, snapshot.case_id
            assert prediction.settlement_ids == entry.expected_settlement_ids
            checked += 1
        assert checked == 14

    def test_no_arm_resolves_a_case_that_has_no_correct_answer_except_the_stale_probe(
        self, v4_stage2, v4_truth
    ):
        """Every wrong auto-resolution in the pilot comes from one archetype.

        That is the pilot's soundness claim: cases are not accidentally
        misleading. Where a strategy errs, it errs on the archetype built to
        make it err.
        """
        batch, _store = v4_stage2
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.correct_relationship is not None:
                continue
            for runner in (
                arm_b_single_fragment,
                arm_c1_lexical_composition,
                arm_c2_lexical_and_structural,
                arm_c3_first_subset_that_isolates,
            ):
                prediction = runner(snapshot)
                if prediction.resolved:
                    assert entry.archetype == "conflict_stale_reference", (
                        snapshot.case_id,
                        prediction.arm,
                    )

    def test_the_consistent_with_everything_rule_survives_the_stale_reference_probe(
        self, v4_stage2, v4_truth
    ):
        """C2 escalates it; C3 does not. That gap is the safety result."""
        batch, _store = v4_stage2
        checked = 0
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.archetype != "conflict_stale_reference":
                continue
            assert not arm_c2_lexical_and_structural(snapshot).resolved, snapshot.case_id
            assert arm_c3_first_subset_that_isolates(snapshot).resolved, snapshot.case_id
            checked += 1
        assert checked == 4


class TestStructuralFeatureExtraction:
    def test_money_fields_are_read_as_exact_paise(self):
        assert money_tokens("NEFT CR RFND 47.38 MUM") == (("47.38", 4738),)
        assert money_tokens("no amounts here") == ()

    def test_date_fields_are_read_as_dates(self):
        from datetime import date

        assert date_tokens("VALDT 14MAR26 MUM") == (("14MAR26", date(2026, 3, 14)),)

    def test_an_impossible_date_field_is_skipped_rather_than_guessed_at(self):
        assert date_tokens("VALDT 31FEB26") == ()

    def test_a_reference_digit_run_is_not_mistaken_for_a_money_field(self):
        """Reference tails are bare digit runs; money fields have two decimals."""
        assert money_tokens("UPI/AXISCN11/BATCH47/386372/RAZORPAY") == ()

    def test_structural_features_are_absent_when_the_narration_has_no_such_field(
        self, v4_stage2, v4_truth
    ):
        batch, _store = v4_stage2
        for snapshot in batch.snapshots:
            if v4_truth[snapshot.case_id].archetype != "conjunction_pair":
                continue
            assert structural_features(snapshot) == (), snapshot.case_id


@pytest.fixture(scope="module")
def baseline_report(benchmark_dir):
    """One full baseline run, shared. Five arms over 64 cases is not cheap."""
    return run_baselines(benchmark_dir, "v4-pilot")


class TestTheReportIsHonest:
    def test_the_report_states_that_no_provider_was_called(self, baseline_report):
        report = baseline_report
        assert report["provider_calls_made"] is False
        assert report["reads_ground_truth_during_inference"] is False

    def test_the_report_carries_a_slice_for_every_arm_and_every_composition(
        self, baseline_report
    ):
        report = baseline_report
        for arm, block in report["arms"].items():
            assert set(block["by_required_composition"]) == {
                "single_fragment",
                "fragment_pair",
                "fragment_triple",
                "fragment_and_breakup_amount",
                "fragment_and_value_date",
                "none",
            }, arm

    def test_the_conservative_arms_make_no_wrong_resolution_on_a_resolvable_case(
        self, baseline_report
    ):
        """Section 13.6, read as it is meant: a careful arm must not err.

        Arms C1 and B do produce wrong answers on this pilot, and every one of
        them is on ``conflict_stale_reference`` -- a case with no correct
        answer, built to expose exactly that. What must never happen is a wrong
        answer on a case that *had* one, which would mean the benchmark
        misleads rather than tests.
        """
        for arm, block in baseline_report["arms"].items():
            for wrong in block["wrong_resolutions"]:
                assert wrong["truth_settlement_ids"] == [], (arm, wrong["case_id"])
                assert wrong["archetype"] == "conflict_stale_reference", (arm, wrong)
