"""The mechanical reference relations: what they hold for, and what they pin.

These are the only lexical predicates anywhere on the Stage-3 decision path,
so they get tested directly rather than only through the validator. The
central property under test is not "does it find the right answer" -- these
functions have no notion of an answer -- but that each relation holds
exactly when its declared transform could have produced the fragment, and
that ``pinned_reference_characters`` is an honest count of what the relation
actually determines.
"""

from __future__ import annotations

import pytest

from finrecon.evidence import reference


class TestRelationsHoldWhenDeclared:
    def test_exact_equality_folds_case_only(self):
        comparison = reference.compare("pf1ceiyfjvq", "PF1CEIYFJVQ", "utr")
        assert reference.RELATION_EXACT in comparison.holding_relation_ids
        assert comparison.max_pinned_reference_characters == 11

    def test_right_truncation_is_a_prefix_of_the_reference(self):
        comparison = reference.compare("8MR7YNFHN", "8MR7YNFHNNLN1FA", "utr")
        assert reference.RELATION_PREFIX in comparison.holding_relation_ids
        assert comparison.max_pinned_reference_characters == 9

    def test_left_truncation_is_a_suffix_of_the_reference(self):
        comparison = reference.compare("V37YVHZ6O", "3GUV37YVHZ6O", "utr")
        assert reference.RELATION_SUFFIX in comparison.holding_relation_ids

    def test_masking_needs_equal_length_and_agreeing_visible_positions(self):
        comparison = reference.compare("PF*******VQ", "PF1CEIYFJVQ", "utr")
        relation = _relation(comparison, reference.RELATION_MASK)
        assert relation.holds
        assert relation.pinned_reference_characters == 4, "two head + two tail characters"

    def test_masking_fails_when_a_visible_position_disagrees(self):
        comparison = reference.compare("PF*******VX", "PF1CEIYFJVQ", "utr")
        assert not _relation(comparison, reference.RELATION_MASK).holds

    def test_masking_fails_on_a_length_mismatch(self):
        comparison = reference.compare("PF****VQ", "PF1CEIYFJVQ", "utr")
        assert not _relation(comparison, reference.RELATION_MASK).holds

    def test_separator_alteration_strips_declared_separators_only(self):
        comparison = reference.compare("PQ4CR8-P46KQH2F", "PQ4CR8P46KQH2F", "utr")
        assert reference.RELATION_SEPARATOR in comparison.holding_relation_ids

    def test_underscore_is_not_a_separator(self):
        """Mirrors the Stage-2 tokenizer: ``_`` lives inside identifiers."""
        assert reference.strip_separators("setl_dev_000123") == "setl_dev_000123"

    def test_reordering_leaves_only_the_character_multiset(self):
        comparison = reference.compare("KPS9T8GXG6YID", "KPSXG69T8YIDG", "utr")
        assert reference.RELATION_MULTISET in comparison.holding_relation_ids

    def test_embedding_means_the_fragment_contains_the_reference(self):
        comparison = reference.compare("SETXKQRLUFL943", "XKQRLUFL943", "utr")
        relation = _relation(comparison, reference.RELATION_CONTAINS)
        assert relation.holds
        assert relation.pinned_reference_characters == 11


class TestRelationsDoNotHoldOtherwise:
    def test_an_unrelated_reference_matches_nothing(self):
        comparison = reference.compare("PF*******VQ", "EQPJ4E94BAD7U4Y", "utr")
        assert comparison.holding_relation_ids == ()
        assert comparison.max_pinned_reference_characters == 0

    def test_every_declared_relation_is_reported_whether_or_not_it_holds(self):
        comparison = reference.compare("ZZZZ", "PF1CEIYFJVQ", "utr")
        assert tuple(r.relation_id for r in comparison.relations) == (
            reference.DECLARED_RELATION_IDS
        )

    def test_an_empty_fragment_proves_nothing(self):
        comparison = reference.compare("", "PF1CEIYFJVQ", "utr")
        assert comparison.holding_relation_ids == ()

    def test_a_mask_with_no_mask_character_is_not_a_mask(self):
        """Equal length and equal content is ``exact``; it must not double-count."""
        comparison = reference.compare("PF1CEIYFJVQ", "PF1CEIYFJVQ", "utr")
        assert not _relation(comparison, reference.RELATION_MASK).holds
        assert reference.RELATION_EXACT in comparison.holding_relation_ids


class TestThereIsNoVerdict:
    """The output must not be able to say which candidate is correct."""

    def test_the_comparison_model_has_no_decision_field(self):
        fields = set(reference.ReferenceComparison.model_fields)
        for forbidden in (
            "is_match",
            "candidate_is_correct",
            "confidence",
            "score",
            "rank",
            "recommended_candidate",
            "candidate_id",
        ):
            assert forbidden not in fields

    def test_a_comparison_never_names_a_candidate(self):
        comparison = reference.compare("PF*******VQ", "PF1CEIYFJVQ", "utr")
        assert "candidate" not in str(comparison.model_dump()).lower()


class TestAdmissibleRelationSelection:
    def test_a_relation_below_the_floor_is_not_admissible(self):
        comparison = reference.compare("PF", "PF1CEIYFJVQ", "utr")
        assert (
            reference.strongest_admissible_relation(
                comparison,
                accepted_relation_ids=frozenset(reference.DECLARED_RELATION_IDS),
                min_pinned_reference_characters=4,
            )
            is None
        )

    def test_a_relation_excluded_by_policy_is_not_admissible(self):
        comparison = reference.compare("8MR7YNFHN", "8MR7YNFHNNLN1FA", "utr")
        assert (
            reference.strongest_admissible_relation(
                comparison,
                accepted_relation_ids=frozenset({reference.RELATION_EXACT}),
                min_pinned_reference_characters=4,
            )
            is None
        )

    def test_selection_prefers_the_relation_that_pins_most(self):
        comparison = reference.compare("PF1CEIYFJVQ", "PF1CEIYFJVQ", "utr")
        best = reference.strongest_admissible_relation(
            comparison,
            accepted_relation_ids=frozenset(reference.DECLARED_RELATION_IDS),
            min_pinned_reference_characters=4,
        )
        assert best is not None
        assert best.pinned_reference_characters == 11


@pytest.mark.parametrize(
    "fragment,ref",
    [
        ("8MR7YNFHN", "8MR7YNFHNNLN1FA"),
        ("V37YVHZ6O", "3GUV37YVHZ6O"),
        ("PF*******VQ", "PF1CEIYFJVQ"),
        ("PQ4CR8-P46KQH2F", "PQ4CR8P46KQH2F"),
        ("KPS9T8GXG6YID", "KPSXG69T8YIDG"),
        ("SETXKQRLUFL943", "XKQRLUFL943"),
    ],
)
def test_every_declared_degradation_shape_is_reachable_by_some_relation(fragment, ref):
    """One case per DESIGN.md §5.2 ladder entry, so a gap fails loudly."""
    comparison = reference.compare(fragment, ref, "utr")
    assert comparison.holding_relation_ids, f"{fragment!r} vs {ref!r} matched nothing"
    assert comparison.max_pinned_reference_characters >= 4


def _relation(comparison, relation_id):
    return next(r for r in comparison.relations if r.relation_id == relation_id)
