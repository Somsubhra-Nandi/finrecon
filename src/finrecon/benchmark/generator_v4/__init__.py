"""Benchmark **v4 pilot** generator — compositional-evidence reconciliation cases.

Additive by construction. Nothing in this package imports, mutates or
regenerates anything belonging to benchmark v3: v3's generator lives in
:mod:`finrecon.benchmark.generator`, writes ``benchmark/manifests/v3.json``
and the ``dev`` / ``frozen-eval`` splits, and this package touches none of
them. The frozen FROZEN-EVAL fingerprint is unchanged by everything here,
and ``tests/test_benchmark_isolation.py`` still asserts it.

Why a separate package rather than a v3 option
----------------------------------------------

Two hard constraints made sharing impossible:

* :class:`finrecon.benchmark.generator.ground_truth.GroundTruthCase` is a
  frozen, ``extra="forbid"`` model whose ``model_dump`` feeds the v3
  fingerprint. Adding a ``families`` field to it would add a key to every
  v3 ground-truth line and change the frozen hash. v4 therefore has its own
  ground-truth model.
* v3's generator writes ``v3.json`` from module-level constants. A v4 case
  count reaching that code would rewrite v3's record of itself.

What *is* shared is the frozen Stage-0 vocabulary — the record factory, the
tokenization contract, the corruption taxonomy, the UTR degradation ladder
and the seeding discipline. Those are imported, never copied, so v4 speaks
the same language v3 froze.

Status: **pilot, not frozen.** No manifest here claims a freeze, no
fingerprint here is presented as a reporting artifact, and the split name
says ``pilot`` so a reader cannot mistake it for a held-out set.
"""

from __future__ import annotations
