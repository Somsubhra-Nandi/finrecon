"""Source ingestion adapters: real-world provider shapes -> canonical FinRecon records.

Nothing under this package is on the reconciliation path
(:mod:`finrecon.normalize`, :mod:`finrecon.matchers`, :mod:`finrecon.candidates`,
:mod:`finrecon.ledger`, :mod:`finrecon.agent`, :mod:`finrecon.decide`,
:mod:`finrecon.evidence`, or the CLIs). Adapters produce canonical records
that ``loader.py``-shaped consumers already understand; they do not widen
the loader's five-file input contract and the decision engine never reads
adapter-only output (e.g. the ingestion manifest).

See ``notes/RAZORPAY-INPUT-GAP.md`` for the design this package implements.
"""

from __future__ import annotations
