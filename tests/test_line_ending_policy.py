"""Guards against the cross-platform line-ending bug fixed by .gitattributes.

Frozen Eval v3's published hash manifest was computed against CRLF bytes,
while a plain Linux checkout of the LF-stored git blobs used to produce LF
bytes instead, so the offline replay's hash verification failed closed on
Railway even though it passed on Windows. Separately, a CRLF
docker-entrypoint.sh breaks the container's shebang on Linux. Both are now
pinned by .gitattributes; these tests fail if that policy regresses,
independent of what OS or local core.autocrlf produced the current
checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

HASH_VERIFIED_CRLF_PATTERNS = (
    "benchmark/reports/frozen-eval-v3-opus5-thinking-operational-raw-240.json",
    "benchmark/reports/frozen-eval-v3-opus5-thinking-provider-recovered-240.json",
    "fixtures/trajectories/frozen-eval-v3-opus5-thinking-final/*.json",
    "fixtures/trajectories/frozen-eval-v3-opus5-thinking-t2-provider-failures-original/*.json",
)

LF_ENTRYPOINTS = (
    "docker-entrypoint.sh",
    "*.sh",
)


def _git_available() -> bool:
    return (PROJECT_ROOT / ".git").exists()


def _check_attr(attribute: str, path: str) -> str:
    result = subprocess.run(
        ["git", "check-attr", attribute, "--", path],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    # Output format: "<path>: <attribute>: <value>"
    return result.stdout.strip().rsplit(":", 1)[-1].strip()


@pytest.mark.skipif(not _git_available(), reason="requires a git checkout")
@pytest.mark.parametrize("pattern", HASH_VERIFIED_CRLF_PATTERNS)
def test_hash_verified_frozen_artifacts_pin_crlf(pattern: str) -> None:
    sample = pattern
    if "*" in pattern:
        directory = PROJECT_ROOT / pattern.rsplit("/", 1)[0]
        matches = sorted(directory.glob(pattern.rsplit("/", 1)[1]))
        assert matches, f"expected at least one file matching {pattern}"
        sample = str(matches[0].relative_to(PROJECT_ROOT)).replace("\\", "/")
    assert _check_attr("text", sample) == "set"
    assert _check_attr("eol", sample) == "crlf"


@pytest.mark.skipif(not _git_available(), reason="requires a git checkout")
@pytest.mark.parametrize("pattern", LF_ENTRYPOINTS)
def test_shell_entrypoints_pin_lf(pattern: str) -> None:
    assert _check_attr("text", pattern) == "set"
    assert _check_attr("eol", pattern) == "lf"


def test_docker_entrypoint_has_no_crlf() -> None:
    content = (PROJECT_ROOT / "docker-entrypoint.sh").read_bytes()
    assert b"\r\n" not in content, "docker-entrypoint.sh must stay LF or its shebang breaks on Linux"
