"""Mechanical, factual evidence primitives shared by the tools and the validator.

Nothing in this package decides anything. Every function here answers a
*lexical* or *arithmetic* question about two strings and returns the answer
as data. It is deliberately shared between the Stage-3 read-only tools
(which show the facts to the model) and the deterministic validator (which
recomputes them over the complete candidate set), because the two must
agree on what a relation *means* while differing entirely on who is allowed
to act on it.
"""
