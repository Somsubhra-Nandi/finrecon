"""SHA-256 fingerprint of the frozen-eval benchmark contents.

DESIGN.md §5.1 step 5: "SHA-256 the frozen set -> committed to README."
Stage 1 asks for the hash to represent the benchmark contents
deterministically, hashing file bytes directly rather than relying on ZIP
metadata or filesystem timestamps.

**Exactly what is hashed** (documented here because the requirement asks
for it to be documented, not just implemented):

1. The five system-visible dataset files under ``datasets/frozen-eval/``
   — bank_records, orders, payments, refunds, settlements — in
   alphabetical filename order.
2. The hidden ground-truth file ``ground_truth/frozen-eval.jsonl``.
3. For each file, in that fixed order, a line ``"<relative_path>\\t<sha256
   of that file's raw bytes as hex>\\n"`` is appended to a manifest
   string. Paths are the forward-slash labels from
   :func:`hashed_file_list`, so the digest is identical on Windows and
   POSIX.
4. The final fingerprint is ``sha256`` of that manifest string, encoded
   UTF-8.

So the fingerprint covers the *complete* FROZEN-EVAL artifact a future
evaluation run consumes, hidden ground truth included — not just the
system-visible half. DEV files are excluded (a different split, and one
that is expected to be regenerated during tuning).

This is a git-tree-hash-style construction: it depends only on file
content and a fixed relative path label, never on mtimes, filesystem
iteration order, or archive metadata. The generator's own manifest file is
deliberately excluded from the hash input to avoid a circular
self-reference (the manifest is where the resulting hash gets recorded).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from finrecon.benchmark.generator.serialize import dataset_file_names


def _file_sha256(path: Path) -> str:
    """SHA-256 of ``path`` with line endings normalised to LF.

    The published fingerprint was computed from LF bytes.  Whether a given
    machine materialises the same committed blob as LF or CRLF depends on how
    the repository was delivered -- a checkout under ``core.autocrlf=true`` and
    ``git archive`` on Windows both produce CRLF -- which is a delivery detail
    rather than a change of benchmark content.  Normalising before hashing
    keeps the published value valid everywhere while still detecting any real
    edit to the data.
    """

    return hashlib.sha256(
        path.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()


def hashed_file_list(split: str = "frozen-eval") -> tuple[str, ...]:
    """Relative paths included in the fingerprint, in the fixed hashed order.

    These are path *labels* relative to ``benchmark/`` (forward-slash
    separated so the digest is identical on Windows and POSIX), not
    filesystem lookups — the order is fixed by this function alone and
    never by directory iteration.
    """
    dataset_paths = tuple(f"datasets/{split}/{name}" for name in dataset_file_names())
    return dataset_paths + (f"ground_truth/{split}.jsonl",)


def compute_fingerprint(benchmark_dir: Path, split: str = "frozen-eval") -> str:
    manifest_lines = []
    for relative_path in hashed_file_list(split):
        file_path = benchmark_dir / relative_path
        manifest_lines.append(f"{relative_path}\t{_file_sha256(file_path)}\n")
    manifest_string = "".join(manifest_lines)
    return hashlib.sha256(manifest_string.encode("utf-8")).hexdigest()
