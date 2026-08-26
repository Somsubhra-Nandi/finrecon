"""Deterministic diagnostic baselines -- how much of a benchmark needs no model.

This package answers one question and refuses to answer any other: **what
does a purely mechanical strategy get, and at what risk?** It makes no
provider call, has no prompt, and reads no hidden ground truth while
deciding anything. Truth is loaded only afterwards, to score decisions that
were already made.

Why it lives outside ``src/``
-----------------------------

Same reason ``benchmark/eval/`` does. These arms are measurement apparatus,
not part of the shipped controller: ``pyproject.toml`` declares
``packages.find where = ["src"]``, so nothing here is installed with
``finrecon``, and the dependency arrow runs one way -- the baselines import
``finrecon``, and nothing under ``src/finrecon`` imports the baselines.

The five arms
-------------

``A`` -- rules only
    The unmodified Stage-2 deterministic core. What resolves before any
    investigation exists at all.

``B1`` -- validator.v1 semantics
    Every admissible narration substring under the rule shipped *before*
    ``validator.v2``: resolve when some one fragment reaches exactly one
    candidate, and treat several disagreeing as none. Kept after v2 landed
    because it is the before-column of the comparison, and a before-column
    that silently tracks the after-column measures nothing.

``B`` -- the shipped gate, exhaustively fed
    Every admissible narration substring, fed through the **real** Stage-3
    validator and the **real** policy gate. Not an approximation of the shipped
    architecture's ceiling; it *is* the shipped architecture, handed more
    lexical evidence than any bounded agent could gather. It is the arm that
    saturated benchmark v3 T2 at 200/200
    (``notes/STAGE3-FINDINGS.md`` section 1) -- and under ``validator.v2`` its
    ceiling now includes conjunctive evidence, so the gap between it and ``B1``
    is exactly what v2 bought.

``C1`` -- lexical composition, consistent-with-everything
    Intersects the reach sets of every admissible fragment. Resolves only
    when exactly one candidate is consistent with all of them.

``C2`` -- lexical and structural composition, consistent-with-everything
    ``C1`` plus two features read mechanically out of the narration: money
    amounts matched against settlement break-up lines, and dates matched
    against settlement dates.

``C3`` -- lexical and structural composition, first-subset-that-isolates
    The same features as ``C2`` under the *aggressive* rule: resolve if any
    subset of features isolates a candidate. Included because it is the rule
    a reasonable person writes first, and because measuring how it differs
    from ``C2`` is the point.

``C1`` and ``C2`` are conservative in a specific, declared way: a feature
that contradicts the rest empties the intersection and the case escalates.
``C3`` is not, and the difference between them is a safety result rather
than a coverage one.

A warning that belongs at the top, not in a footnote
-----------------------------------------------------

``C2`` composes exactly the feature vocabulary the v4 pilot generator uses
to *define* its cases. A benchmark whose difficulty is built from a declared,
finite set of mechanical features is solvable by exhaustively composing that
same set -- so ``C2`` scoring well on v4 is close to a tautology, and must be
reported as one. See ``benchmark/V4-PILOT.md``.
"""

from __future__ import annotations

BASELINE_SUITE_VERSION = "baselines.v1"

__all__ = ["BASELINE_SUITE_VERSION"]
