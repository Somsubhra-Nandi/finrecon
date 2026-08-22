import pytest

from finrecon.benchmark.generator.narration_library import (
    NARRATION_LIBRARY,
    NarrationProvenance,
    NarrationTemplate,
    get_narration_template,
)


def test_ids_are_unique():
    ids = [t.id for t in NARRATION_LIBRARY]
    assert len(ids) == len(set(ids))


def test_every_template_has_provenance():
    for template in NARRATION_LIBRARY:
        assert isinstance(template.provenance, NarrationProvenance)


def test_every_template_has_a_source_note():
    for template in NARRATION_LIBRARY:
        assert template.source_note.strip()


def test_verbatim_entries_all_carry_a_citation():
    """Every VERBATIM_PUBLIC entry must have a real, non-empty source URL —
    dataclass __post_init__ already enforces this at construction time,
    but this test guards the invariant at the library-content level too."""
    verbatim = [t for t in NARRATION_LIBRARY if t.provenance is NarrationProvenance.VERBATIM_PUBLIC]
    assert verbatim, "expected at least one VERBATIM_PUBLIC entry in the frozen library"
    for template in verbatim:
        assert template.citation
        assert template.citation.startswith("https://")


def test_synthetic_entries_cannot_masquerade_as_verbatim():
    """SOURCE_INFORMED_SYNTHETIC and GENERATED_CORRUPTION entries must never
    carry a citation field — a citation implies verbatim provenance, and an
    entry that isn't VERBATIM_PUBLIC must not look like one."""
    for template in NARRATION_LIBRARY:
        if template.provenance is not NarrationProvenance.VERBATIM_PUBLIC:
            assert template.citation is None, (
                f"{template.id!r} is {template.provenance!r} but carries a citation "
                "as if it were verbatim"
            )


def test_verbatim_entries_are_the_only_ones_sourced_to_razorpay_docs():
    """The two verbatim entries are pinned to the exact Razorpay doc pages
    they were retrieved from, so a future edit can't silently repoint a
    verbatim claim at an unrelated or unverified source."""
    expected = {
        "razorpay_docs_settlement_entity_utr_example": "https://razorpay.com/docs/api/settlements/entity/",
        "razorpay_docs_settlement_webhook_utr_example": "https://razorpay.com/docs/webhooks/settlements/",
    }
    verbatim_by_id = {
        t.id: t for t in NARRATION_LIBRARY if t.provenance is NarrationProvenance.VERBATIM_PUBLIC
    }
    assert set(verbatim_by_id) == set(expected)
    for template_id, citation in expected.items():
        assert verbatim_by_id[template_id].citation == citation


def test_verbatim_provenance_requires_citation():
    with pytest.raises(ValueError):
        NarrationTemplate(
            id="fake_verbatim",
            template="ANYTHING/{ref}",
            provenance=NarrationProvenance.VERBATIM_PUBLIC,
            source_note="claims to be real but has no citation",
        )


def test_verbatim_provenance_with_citation_is_accepted():
    template = NarrationTemplate(
        id="cited_example",
        template="ANYTHING/{ref}",
        provenance=NarrationProvenance.VERBATIM_PUBLIC,
        source_note="captured from a published statement excerpt",
        citation="https://example.invalid/statement-format-doc",
    )
    assert template.citation is not None


def test_template_without_source_note_rejected():
    with pytest.raises(ValueError):
        NarrationTemplate(
            id="no_source",
            template="ANYTHING/{ref}",
            provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
            source_note="   ",
        )


def test_corruption_category_ids_reference_real_categories():
    from finrecon.benchmark.generator.corruptions import get_corruption_category

    for template in NARRATION_LIBRARY:
        for category_id in template.corruption_category_ids:
            get_corruption_category(category_id)  # raises if unknown


def test_generated_corruption_entries_declare_a_category():
    for template in NARRATION_LIBRARY:
        if template.provenance is NarrationProvenance.GENERATED_CORRUPTION:
            assert template.corruption_category_ids


def test_templates_render_with_a_reference():
    for template in NARRATION_LIBRARY:
        if "{ref}" in template.template:
            rendered = template.template.format(ref="928392123456")
            assert "928392123456" in rendered


def test_lookup_by_id():
    template = get_narration_template("design_doc_example_upi")
    assert template.provenance is NarrationProvenance.SOURCE_INFORMED_SYNTHETIC


def test_lookup_unknown_id_raises():
    with pytest.raises(KeyError):
        get_narration_template("does_not_exist")
