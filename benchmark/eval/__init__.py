"""Stage 4 — **offline benchmark evaluation**.

This package is the *only* place in the repository where a reconciliation
outcome is compared against hidden ground truth, and it is deliberately not
part of the shipped system.

Why it lives outside ``src/finrecon``
-------------------------------------

DESIGN.md §9 requires ground truth to stay hidden from the reconciliation
path, and ``tests/test_benchmark_isolation.py`` enforces that mechanically
over ``normalize``, ``matchers``, ``candidates``, ``ledger``, ``agent``,
``decide``, ``evidence`` and the top-level pipeline modules. Putting a
scorer inside any of those would either break that contract or force it to
be weakened, so the scorer sits here instead:

* ``benchmark/eval/`` is outside ``src/``, and ``pyproject.toml`` declares
  ``packages.find where = ["src"]`` — so this package is **never installed**
  as part of the ``finrecon`` distribution;
* the dependency arrow points one way only. The evaluator imports
  ``finrecon``; nothing under ``src/finrecon`` imports the evaluator, and
  ``tests/test_stage4_evaluator.py`` asserts it.

The layering, stated once
-------------------------

===========================  ==============================================
Stage 2 / 3 (production)     decide; report **no accuracy**; cannot read
                             ground truth, structurally
Stage 4 (this package)       read ground truth; report accuracy; **cannot
                             decide anything and cannot call a provider**
===========================  ==============================================

Evaluation can never influence a reconciliation decision, because the
evaluator runs after the fact over recorded artifacts and returns numbers to
a human. It has no write path into the ledger's decision columns and no way
to reach a model.

Offline by construction
-----------------------

The evaluator never constructs a provider — not OpenRouter, GoRouter, Groq,
Gemini, nor any other. It replays recorded trajectories through the real
Stage-3 validator and policy with ``replay_only=True`` and ``chain=None``.
A missing trajectory is a hard failure (:class:`~benchmark.eval.errors.
EvaluationInputError`), never a silent live run. ``tests/
test_stage4_evaluator.py`` proves both the structural property (no provider
module is imported anywhere in this package) and the runtime one
(``provider_calls_made()`` stays false).
"""

from __future__ import annotations

EVALUATOR_VERSION = "stage4-eval.v1"
"""Identity of the evaluation contract: metric definitions and report shape.

Bump it when a reported number changes meaning. It is recorded in every JSON
report so a stored result can be read back with the semantics it was
produced under.
"""

__all__ = ["EVALUATOR_VERSION"]
