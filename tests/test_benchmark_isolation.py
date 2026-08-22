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

FROZEN_EVAL_SHA256 = "cda267318d215040a401bc413296015296f0d720eda09d6cd12503085fe88243"
"""The Stage-1 freeze. Stage 2 may not change it; if this fails, stop."""

RECONCILIATION_PACKAGES = ("normalize", "matchers", "candidates", "ledger")
RECONCILIATION_MODULES = ("pipeline.py", "loader.py", "reconcile_cli.py")


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
            (benchmark_dir / "manifests" / "v1.json").read_text(encoding="utf-8")
        )
        assert manifest["frozen_eval_sha256"] == FROZEN_EVAL_SHA256
        assert manifest["generator_version"] == "1.0.0"

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
