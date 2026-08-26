"""Entry point: ``python -m benchmark.eval``."""

from __future__ import annotations

import sys

from benchmark.eval.cli import main

if __name__ == "__main__":
    sys.exit(main())
