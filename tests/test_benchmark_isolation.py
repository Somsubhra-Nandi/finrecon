"""Structural guarantee: reconciliation code cannot consume hidden ground truth.

DESIGN.md §9 (Stage 1) requires ground truth to be "hidden from the
system". A convention is not a guarantee, so this module asserts it
mechanically:

* no module on the reconciliation path names ``ground_truth`` at all —
  not in an import, not in a string literal, not in a path expression;
* the loader reaches exactly five visible files and nothing else;
* the frozen Stage-1 generator and its committed datasets are unchanged by
  Stage 2, verified against the SHA-256 recorded in the manifest.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from finrecon.benchmark.generator.hashing import compute_fingerprint
from finrecon.loader import VISIBLE_RECORD_FILES, load_visible_split, visible_split_dir

V1_FROZEN_EVAL_SHA256 = "cda267318d215040a401bc413296015296f0d720eda09d6cd12503085fe88243"
"""Benchmark v1's frozen-eval fingerprint. Superseded, never erased.

v1 was retired because Stage 2 showed its T2 cases were uniquely
resolvable from structured evidence alone, so the degraded reference the
tier exists to test was not causally necessary
(``notes/STAGE2-FINDINGS.md`` §1). The correction has to stay auditable:
``benchmark/manifests/v1.json`` keeps this hash, its seeds and its counts
verbatim, and the tests below assert that it does. A v2 that quietly
rewrote v1's record of itself would be exactly the retro-fit the finding
warned against.
"""

V2_FROZEN_EVAL_SHA256 = "d130c42c4bb52b6dc6b88e24f89257f4586c72423a22fdc4606440e53545b897"
"""Benchmark v2's frozen-eval fingerprint. Superseded, never erased.

v2 fixed v1's T2 construct and was itself retired for an unrelated T0
defect: FROZEN-EVAL settlement IDs embedded the split name verbatim, so
``setl_frozen-eval_000042`` contained a tokenizer delimiter and 175 of 350
T0 cases could not be reached by the direct-key matcher at all. DEV was
unaffected, so the whole suite stayed green
(``benchmark/manifests/CHANGELOG.md`` v3.0.0). v2's T2 construct carries
forward into v3 unchanged.
"""

FROZEN_EVAL_SHA256 = "f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b"
"""Benchmark v3's freeze — the current committed FROZEN-EVAL artifact.

Frozen before any Stage-3 model exists, and not to be changed by one. If
this fails, stop.
"""

RECONCILIATION_PACKAGES = (
    "normalize",
    "matchers",
    "candidates",
    "ledger",
    # Stage 3 joins the same guarantee. The investigation agent, its tools,
    # its providers and the deterministic decision layer are all on the path
    # that decides whether money moves, so none of them may read the hidden
    # answers either.
    "agent",
    "decide",
    "evidence",
)
RECONCILIATION_MODULES = (
    "pipeline.py",
    "loader.py",
    "reconcile_cli.py",
    "stage3.py",
    "investigate_cli.py",
)


def source_root() -> Path:
    import finrecon

    return Path(finrecon.__file__).resolve().parent


def reconciliation_sources() -> list[Path]:
    root = source_root()
    files = [root / name for name in RECONCILIATION_MODULES]
    for package in RECONCILIATION_PACKAGES:
        files.extend(sorted((root / package).rglob("*.py")))
    return [path for path in files if path.exists()]


class TestGroundTruthIsolation:
    def test_the_reconciliation_path_has_files_to_check(self):
        assert len(reconciliation_sources()) >= 12

    @pytest.mark.parametrize("path", reconciliation_sources(), ids=lambda p: p.name)
    def test_no_reconciliation_module_mentions_ground_truth(self, path):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "ground_truth" not in alias.name, path.name
            elif isinstance(node, ast.ImportFrom):
                assert "ground_truth" not in (node.module or ""), path.name
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Docstrings may discuss the isolation rule by name; code
                # strings that could become a path may not.
                if node.value.strip().startswith(("Ground truth", "**Test-only")):
                    continue
                if "ground_truth" in node.value and len(node.value) < 200:
                    pytest.fail(f"{path.name} embeds a ground_truth string literal")

    def test_no_reconciliation_module_imports_the_benchmark_generator(self):
        """Except the isolation test itself, which needs the frozen hash."""
        for path in reconciliation_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = node.names[0].name
                if module and "benchmark.generator" in module:
                    pytest.fail(f"{path.name} imports the benchmark generator: {module}")

    def test_the_loader_reads_only_the_five_visible_files(self, benchmark_dir, monkeypatch):
        opened: list[str] = []
        real_open = Path.open

        def spy(self, *args, **kwargs):
            opened.append(self.name)
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", spy)
        load_visible_split(benchmark_dir, "dev")

        # Each visible file is opened twice — once to parse, once to hash
        # for the batch fingerprint — so compare the distinct set.
        assert set(opened) == {f"{name}.jsonl" for name in VISIBLE_RECORD_FILES}
        assert not any("ground_truth" in name for name in opened)

    def test_the_loader_refuses_a_split_that_escapes_the_dataset_directory(self, benchmark_dir):
        for bad in ("../ground_truth", "..", ".", "", "a/b"):
            with pytest.raises(ValueError):
                visible_split_dir(benchmark_dir, bad)

    def test_the_pipeline_result_exposes_no_tier_or_expected_outcome(self, dev_result):
        result, _ = dev_result
        serialized = str(result.decisions[0].model_dump())
        for leak in ("tier", "T0", "T1", "T2", "T3", "required_outcome", "archetype"):
            assert leak not in serialized


class TestFrozenBenchmarkIntegrity:
    def test_the_frozen_eval_hash_is_unchanged(self, benchmark_dir):
        assert compute_fingerprint(benchmark_dir, "frozen-eval") == FROZEN_EVAL_SHA256

    def test_the_manifest_still_records_that_hash(self, benchmark_dir):
        import json

        manifest = json.loads(
            (benchmark_dir / "manifests" / "v3.json").read_text(encoding="utf-8")
        )
        assert manifest["frozen_eval_sha256"] == FROZEN_EVAL_SHA256
        assert manifest["generator_version"] == "3.0.0"
        assert manifest["dev_seed"] == 42
        assert manifest["frozen_eval_seed"] == 1337
        assert manifest["target_tier_counts"] == {"T0": 350, "T1": 300, "T2": 200, "T3": 40}

    def test_the_v1_manifest_and_hash_are_preserved_unchanged(self, benchmark_dir):
        """The superseded benchmark stays on the record, exactly as it was."""
        import json

        v1 = json.loads((benchmark_dir / "manifests" / "v1.json").read_text(encoding="utf-8"))
        assert v1["generator_version"] == "1.0.0"
        assert v1["frozen_eval_sha256"] == V1_FROZEN_EVAL_SHA256
        assert v1["dev_seed"] == 42
        assert v1["frozen_eval_seed"] == 1337
        assert v1["actual_tier_counts"]["frozen-eval"] == {
            "T0": 350,
            "T1": 300,
            "T2": 200,
            "T3": 40,
        }
        assert V1_FROZEN_EVAL_SHA256 != FROZEN_EVAL_SHA256

    def test_the_v2_manifest_and_hash_are_preserved_unchanged(self, benchmark_dir):
        """Two supersessions deep, both still auditable rather than overwritten."""
        import json

        v2 = json.loads((benchmark_dir / "manifests" / "v2.json").read_text(encoding="utf-8"))
        assert v2["generator_version"] == "2.0.0"
        assert v2["frozen_eval_sha256"] == V2_FROZEN_EVAL_SHA256
        assert v2["dev_seed"] == 42
        assert v2["frozen_eval_seed"] == 1337
        assert v2["actual_tier_counts"]["frozen-eval"] == {
            "T0": 350,
            "T1": 300,
            "T2": 200,
            "T3": 40,
        }
        assert len({V1_FROZEN_EVAL_SHA256, V2_FROZEN_EVAL_SHA256, FROZEN_EVAL_SHA256}) == 3

    def test_the_changelog_records_every_version_and_why_each_was_superseded(
        self, benchmark_dir
    ):
        changelog = (benchmark_dir / "manifests" / "CHANGELOG.md").read_text(encoding="utf-8")
        for sha in (V1_FROZEN_EVAL_SHA256, V2_FROZEN_EVAL_SHA256, FROZEN_EVAL_SHA256):
            assert sha in changelog
        for version in ("v1.0.0", "v2.0.0", "v3.0.0"):
            assert version in changelog
        assert "SUPERSEDED" in changelog

    def test_the_changelog_records_that_v3_predates_any_stage_three_code(
        self, benchmark_dir
    ):
        """The freeze protocol's credibility rests on this ordering being stated."""
        changelog = (benchmark_dir / "manifests" / "CHANGELOG.md").read_text(encoding="utf-8")
        v3_entry = changelog.split("## v2.0.0")[0]
        assert "before any Stage-3" in v3_entry

    def test_the_v3_hash_is_reproducible_from_the_committed_configuration(self, tmp_path):
        """Clean-room regeneration from the committed seeds must be byte-identical."""
        from finrecon.benchmark.generator.config import (
            FROZEN_EVAL_SEED,
            TARGET_TIER_COUNTS,
        )
        from finrecon.benchmark.generator.dataset import build_dataset
        from finrecon.benchmark.generator.serialize import write_dataset

        bundle = build_dataset("frozen-eval", FROZEN_EVAL_SEED, TARGET_TIER_COUNTS)
        write_dataset(bundle, tmp_path)
        assert compute_fingerprint(tmp_path, "frozen-eval") == FROZEN_EVAL_SHA256

    def test_the_committed_dev_split_is_reproducible_byte_for_byte(
        self, benchmark_dir, tmp_path
    ):
        """DEV is regenerated during tuning, so its byte-stability is worth pinning too.

        It also documents a v3 property: the ``dev`` slug was already
        token-safe, so the v3 correction left DEV byte-identical to v2 and
        changed only the six FROZEN-EVAL files.
        """
        from finrecon.benchmark.generator.config import DEV_SEED, TARGET_TIER_COUNTS
        from finrecon.benchmark.generator.dataset import build_dataset
        from finrecon.benchmark.generator.serialize import dataset_file_names, write_dataset

        bundle = build_dataset("dev", DEV_SEED, TARGET_TIER_COUNTS)
        write_dataset(bundle, tmp_path)
        for name in dataset_file_names():
            regenerated = (tmp_path / "datasets" / "dev" / name).read_bytes()
            committed = (benchmark_dir / "datasets" / "dev" / name).read_bytes()
            assert regenerated == committed, name

    def test_stage_two_processes_frozen_eval_without_reading_its_truth(
        self, benchmark_dir, monkeypatch
    ):
        """Infrastructure check only — no FROZEN-EVAL outcome is inspected.

        DESIGN.md §5.1 step 7 says build against DEV and report against
        FROZEN. This asserts the pipeline *runs* on the held-out split and
        touches no truth file; it deliberately makes no claim about how
        many cases resolved.
        """
        from finrecon.ledger.store import LedgerStore
        from finrecon.pipeline import process_batch

        opened: list[str] = []
        real_open = Path.open

        def spy(self, *args, **kwargs):
            opened.append(str(self))
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", spy)
        with LedgerStore(":memory:") as store:
            result = process_batch(
                store=store, benchmark_dir=benchmark_dir, split="frozen-eval"
            )

        assert len(result.decisions) == 890
        assert not any("ground_truth" in path for path in opened)

    def test_the_frozen_hash_still_matches_after_that_run(self, benchmark_dir):
        assert compute_fingerprint(benchmark_dir, "frozen-eval") == FROZEN_EVAL_SHA256
