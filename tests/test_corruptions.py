from finrecon.benchmark.generator.corruptions import (
    CORRUPTION_TAXONOMY,
    get_corruption_category,
)


def test_taxonomy_ids_are_unique():
    ids = [c.id for c in CORRUPTION_TAXONOMY]
    assert len(ids) == len(set(ids))


def test_taxonomy_names_are_unique():
    names = [c.name for c in CORRUPTION_TAXONOMY]
    assert len(names) == len(set(names))


def test_every_entry_has_a_description():
    for category in CORRUPTION_TAXONOMY:
        assert category.description.strip()


def test_lookup_by_id():
    category = get_corruption_category("field_truncation")
    assert category.name == "Field truncation"


def test_lookup_unknown_id_raises():
    try:
        get_corruption_category("does_not_exist")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for unknown category id")
