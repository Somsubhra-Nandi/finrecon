"""Stage-3 ground-truth isolation, asserted structurally rather than promised.

Stage 2 established the rule (``test_benchmark_isolation.py``): nothing on
the path that decides whether money moves may read the hidden answers.
Stage 3 is now on that path, and it is the stage where the temptation is
real -- a validator that peeked at ``true_reference`` would look
spectacular and measure nothing.

Four independent guarantees, so no single mistake defeats all of them:

1. **No import.** No Stage-3 module imports the benchmark package at all,
   generator or ground truth.
2. **No path.** No Stage-3 module names ``ground_truth`` in any string.
3. **No read.** A full Stage-3 run over DEV opens no file under
   ``ground_truth/``, verified by watching every ``Path.open``.
4. **No leaked field.** Nothing a Stage-3 decision or trajectory carries
   mentions a tier, an archetype, a required outcome or a true reference.

Plus the freeze check: the FROZEN-EVAL fingerprint is identical before and
after Stage 3 exists and runs.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from finrecon.agent.cache import TrajectoryCache
from finrecon.agent.providers.chain import ProviderChain
from finrecon.benchmark.generator.hashing import compute_fingerprint
from finrecon.ledger.store import LedgerStore
from finrecon.pipeline import process_batch
from finrecon.stage3 import run_stage3
from tests.stage3_fakes import MechanicalInvestigator
from tests.test_benchmark_isolation import FROZEN_EVAL_SHA256

STAGE3_PACKAGES = ("agent", "decide", "evidence")
STAGE3_MODULES = ("stage3.py", "investigate_cli.py")

GROUND_TRUTH_FIELDS = (
    "tier",
    "archetype",
    "correct_relationship",
    "required_outcome",
    "true_reference",
    "distractor_settlement_ids",
    "surviving_evidence",
    "value_at_stake_paise",
)


def stage3_sources() -> list[Path]:
    import finrecon

    root = Path(finrecon.__file__).resolve().parent
    files = [root / name for name in STAGE3_MODULES]
    for package in STAGE3_PACKAGES:
        files.extend(sorted((root / package).rglob("*.py")))
    return [path for path in files if path.exists()]


def module_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    return found


class TestNoImport:
    def test_there_are_stage_three_files_to_check(self):
        assert len(stage3_sources()) >= 15

    @pytest.mark.parametrize("path", stage3_sources(), ids=lambda p: p.name)
    def test_no_stage_three_module_imports_the_benchmark_package(self, path):
        for module in module_imports(path):
            assert not module.startswith("finrecon.benchmark"), f"{path.name} -> {module}"

    def test_the_reference_relations_are_not_imported_from_the_generator(self):
        """``t2_evidence`` has lookalike predicates and is keyed by a hidden label.

        Importing it would put a generator-side answer key on the production
        path. The relations in ``finrecon.evidence.reference`` are re-derived
        from the public DESIGN.md vocabulary instead, and applied without any
        knowledge of which degradation category a case used.
        """
        import finrecon.evidence.reference as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "t2_evidence" in source, "the relationship is documented"
        for imported in module_imports(Path(module.__file__)):
            assert "t2_evidence" not in imported
            assert "benchmark" not in imported


class TestNoPath:
    @pytest.mark.parametrize("path", stage3_sources(), ids=lambda p: p.name)
    def test_no_stage_three_module_embeds_a_ground_truth_string(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.strip().startswith(("Ground truth", "**Test-only")):
                    continue
                if "ground_truth" in node.value and len(node.value) < 200:
                    pytest.fail(f"{path.name} embeds a ground_truth string literal")

    @pytest.mark.parametrize("path", stage3_sources(), ids=lambda p: p.name)
    def test_no_stage_three_module_names_a_hidden_field(self, path):
        """A field name is how a leak would look before it became a path."""
        source = path.read_text(encoding="utf-8")
        for field in ("required_outcome", "true_reference", "distractor_settlement_ids"):
            assert field not in source, f"{path.name} mentions {field}"


@pytest.fixture(scope="module")
def stage3_run(benchmark_dir, tmp_path_factory):
    store = LedgerStore(":memory:")
    batch = process_batch(store=store, benchmark_dir=benchmark_dir, split="dev")
    yield store, batch, tmp_path_factory.mktemp("iso-trajectories")
    store.close()


class TestNoRead:
    def test_a_stage_three_run_opens_no_ground_truth_file(
        self, stage3_run, monkeypatch, benchmark_dir
    ):
        store, batch, cache_dir = stage3_run
        opened: list[str] = []
        real_open = Path.open

        def spy(self, *args, **kwargs):
            opened.append(str(self))
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", spy)
        chosen = frozenset(s.case_id for s in sorted(batch.snapshots, key=lambda s: s.case_id)[:8])
        run_stage3(
            store=store,
            batch_result=batch,
            chain=ProviderChain((MechanicalInvestigator(),)),
            cache=TrajectoryCache(cache_dir),
            case_ids=chosen,
        )
        assert opened, "the spy is wired up"
        assert not any("ground_truth" in path for path in opened)

    def test_the_snapshot_a_tool_sees_carries_no_hidden_field(self, stage3_run):
        _, batch, _ = stage3_run
        serialized = json.dumps(batch.snapshots[0].model_dump(mode="json"))
        for field in GROUND_TRUTH_FIELDS:
            assert field not in serialized


class TestNoLeakedField:
    def test_no_stage_three_decision_or_trajectory_mentions_a_hidden_field(
        self, dev_stage3_result
    ):
        result, _, _ = dev_stage3_result
        for outcome in result.outcomes[:40]:
            payload = json.dumps(
                {
                    "decision": outcome.decision.model_dump(mode="json"),
                    "validator": outcome.validator_result.model_dump(mode="json"),
                    "trajectory": outcome.trajectory.model_dump(mode="json"),
                }
            )
            for field in GROUND_TRUTH_FIELDS:
                assert field not in payload, f"{outcome.case_id} leaked {field}"

    def test_the_case_briefing_handed_to_a_model_carries_no_hidden_field(
        self, dev_stage3_result
    ):
        from finrecon.agent.prompt import case_briefing

        result, _, _ = dev_stage3_result
        for outcome in result.outcomes[:40]:
            briefing = case_briefing(outcome.snapshot)
            for field in GROUND_TRUTH_FIELDS:
                assert field not in briefing


class TestFrozenBenchmarkUnchangedByStageThree:
    def test_the_frozen_eval_fingerprint_is_untouched(self, benchmark_dir):
        assert compute_fingerprint(benchmark_dir, "frozen-eval") == FROZEN_EVAL_SHA256

    def test_a_full_stage_three_dev_run_does_not_disturb_it(
        self, dev_stage3_result, benchmark_dir
    ):
        assert dev_stage3_result[0].outcomes
        assert compute_fingerprint(benchmark_dir, "frozen-eval") == FROZEN_EVAL_SHA256

    def test_no_frozen_eval_outcome_fixture_exists(self):
        """Stage-3 tuning must not be able to consult held-out answers.

        ``conftest`` exposes DEV truth in full and FROZEN-EVAL **tier labels
        only**. If a fixture serving FROZEN-EVAL outcomes ever appears, this
        fails -- which is the point: the freeze protocol is only as strong as
        the absence of a convenient way around it.
        """
        source = Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fixture_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        assert "frozen_eval_ground_truth" not in fixture_names
        assert "frozen_eval_outcomes" not in fixture_names
        assert "frozen_eval_tier_labels" in fixture_names

    def test_the_frozen_eval_tier_fixture_is_still_labels_only(
        self, frozen_eval_tier_labels
    ):
        entry = next(iter(frozen_eval_tier_labels.values()))
        assert set(entry) == {"tier", "archetype"}
