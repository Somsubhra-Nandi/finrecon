import pytest

from finrecon.benchmark.generator.utr_degradation import (
    DEGRADATION_LADDER,
    degrade,
    get_degradation_category,
)

SAMPLE_UTR = "928392123456"


def test_ladder_ids_are_unique():
    ids = [c.id for c in DEGRADATION_LADDER]
    assert len(ids) == len(set(ids))


def test_ladder_names_are_unique():
    names = [c.name for c in DEGRADATION_LADDER]
    assert len(names) == len(set(names))


def test_ladder_covers_design_doc_vocabulary():
    ids = {c.id for c in DEGRADATION_LADDER}
    expected = {
        "intact",
        "truncated_left",
        "truncated_right",
        "masked",
        "separator_altered",
        "reordered",
        "embedded_in_narration",
        "omitted",
        "ambiguous",
    }
    assert expected <= ids


def test_lookup_unknown_category_raises():
    with pytest.raises(KeyError):
        get_degradation_category("not_a_category")


@pytest.mark.parametrize(
    "category_id",
    [
        "intact",
        "truncated_left",
        "truncated_right",
        "masked",
        "separator_altered",
        "reordered",
        "embedded_in_narration",
        "omitted",
    ],
)
def test_degrade_is_deterministic_for_fixed_seed(category_id):
    first = degrade(SAMPLE_UTR, category_id, seed=42)
    second = degrade(SAMPLE_UTR, category_id, seed=42)
    assert first == second


def test_degrade_can_vary_with_seed():
    results = {degrade(SAMPLE_UTR, "truncated_left", seed=s).value for s in range(20)}
    assert len(results) > 1


def test_degrade_ambiguous_has_no_operator():
    with pytest.raises(KeyError):
        degrade(SAMPLE_UTR, "ambiguous", seed=1)


def test_intact_returns_unchanged_value():
    result = degrade(SAMPLE_UTR, "intact", seed=1)
    assert result.value == SAMPLE_UTR


def test_omitted_returns_none():
    result = degrade(SAMPLE_UTR, "omitted", seed=1)
    assert result.value is None


def test_truncate_left_keeps_a_suffix():
    result = degrade(SAMPLE_UTR, "truncated_left", seed=7)
    assert SAMPLE_UTR.endswith(result.value)
    assert result.value != SAMPLE_UTR


def test_truncate_right_keeps_a_prefix():
    result = degrade(SAMPLE_UTR, "truncated_right", seed=7)
    assert SAMPLE_UTR.startswith(result.value)
    assert result.value != SAMPLE_UTR


def test_masked_preserves_length():
    result = degrade(SAMPLE_UTR, "masked", seed=3)
    assert len(result.value) == len(SAMPLE_UTR)
    assert "*" in result.value


def test_reordered_is_a_permutation_of_chunks():
    result = degrade(SAMPLE_UTR, "reordered", seed=5)
    assert sorted(result.value) == sorted(SAMPLE_UTR)


def test_embedded_in_narration_contains_reference():
    result = degrade(SAMPLE_UTR, "embedded_in_narration", seed=1)
    assert SAMPLE_UTR in result.value


def test_no_hidden_global_random_state_mutation():
    import random

    random.seed(1234)
    before = random.random()

    random.seed(1234)
    degrade(SAMPLE_UTR, "reordered", seed=99)
    after = random.random()

    assert before == after
