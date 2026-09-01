"""The built-in bank-profile registry: loading, immutability, honesty.

A registry entry is authoritative because a human reviewed and versioned
the column mapping it carries. These tests pin the properties that claim
depends on: an artifact either loads completely or fails loudly, two
artifacts can never claim the same ``profile_id``, and nothing a caller
does can change what the registry hands the next caller.

Every artifact written here is synthetic and authored for this test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finrecon.adapters.bank.csv_profile import InactiveSideMarker
from finrecon.adapters.bank.schema import (
    BUILT_IN_PROFILE_DIR,
    BankProfileRegistry,
    BankProfileRegistryError,
    ProfileVerification,
    built_in_registry,
    load_built_in_profile,
)
from finrecon.adapters.bank.schema.registry import REGISTRY_ARTIFACT_VERSION


def artifact(**overrides) -> dict:
    payload = {
        "registry_artifact_version": REGISTRY_ARTIFACT_VERSION,
        "profile_id": "synthetic_registry_v1",
        "label": "Synthetic registry fixture",
        "version": "v1",
        "verification": "demo_fixture",
        "description": "Authored for tests only.",
        "evidence": "Synthetic; describes no real bank.",
        "expected_headers": ["Value Date", "Narration", "Debit", "Credit"],
        "profile": {
            "profile_id": "synthetic_registry_v1",
            "currency": "INR",
            "value_date_column": "Value Date",
            "value_date_format": "%d/%m/%Y",
            "narration_column": "Narration",
            "money_columns": {
                "kind": "debit_credit",
                "debit_column": "Debit",
                "credit_column": "Credit",
            },
        },
    }
    payload.update(overrides)
    return payload


def write(directory: Path, name: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestDeterministicLoading:
    def test_a_directory_of_artifacts_loads_deterministically(self, tmp_path: Path):
        write(tmp_path, "b", artifact(profile_id="b_v1", profile=artifact()["profile"] | {"profile_id": "b_v1"}))
        write(tmp_path, "a", artifact(profile_id="a_v1", profile=artifact()["profile"] | {"profile_id": "a_v1"}))

        first = BankProfileRegistry.from_directory(tmp_path)
        second = BankProfileRegistry.from_directory(tmp_path)

        assert [entry.profile_id for entry in first] == ["a_v1", "b_v1"]
        assert [entry.profile_id for entry in first] == [entry.profile_id for entry in second]
        assert first.get("a_v1") is not None and first.get("missing") is None

    def test_an_absent_directory_is_an_empty_registry_not_an_error(self, tmp_path: Path):
        """A build shipping no profiles is legitimate -- everything simply
        falls through to the manual-profile path."""
        assert len(BankProfileRegistry.from_directory(tmp_path / "nope")) == 0

    def test_duplicate_profile_ids_are_rejected(self, tmp_path: Path):
        write(tmp_path, "one", artifact())
        write(tmp_path, "two", artifact())
        with pytest.raises(BankProfileRegistryError, match="duplicate built-in profile_id"):
            BankProfileRegistry.from_directory(tmp_path)

    @pytest.mark.parametrize(
        ("mutation", "expected"),
        [
            ({"registry_artifact_version": 99}, "registry_artifact_version"),
            ({"verification": "totally_verified"}, "verification must be one of"),
            ({"expected_headers": []}, "expected_headers"),
            ({"label": "   "}, "label must be a non-empty string"),
            ({"evidence": ""}, "evidence must be a non-empty string"),
            ({"profile_id": "renamed_v1"}, "disagrees with the embedded"),
        ],
    )
    def test_an_invalid_artifact_fails_loading_clearly(self, tmp_path: Path, mutation, expected):
        path = write(tmp_path, "bad", artifact(**mutation))
        with pytest.raises(BankProfileRegistryError, match=expected):
            load_built_in_profile(path)

    def test_a_missing_key_names_the_key(self, tmp_path: Path):
        payload = artifact()
        del payload["description"]
        path = write(tmp_path, "bad", payload)
        with pytest.raises(BankProfileRegistryError, match="missing 'description'"):
            load_built_in_profile(path)

    def test_unparseable_json_fails_loading(self, tmp_path: Path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(BankProfileRegistryError, match="not readable as UTF-8 JSON"):
            load_built_in_profile(path)

    def test_a_profile_declaring_a_column_its_own_header_row_lacks_is_rejected(self, tmp_path: Path):
        """An entry that could never match anything is a build-time mistake,
        caught at load rather than at somebody's first upload."""
        path = write(tmp_path, "bad", artifact(expected_headers=["Value Date", "Narration", "Debit"]))
        with pytest.raises(BankProfileRegistryError, match=r"absent from expected_headers"):
            load_built_in_profile(path)

    def test_a_malformed_embedded_profile_is_reported_as_such(self, tmp_path: Path):
        payload = artifact()
        payload["profile"]["money_columns"]["inactive_side_marker"] = "zero_is_blank"
        path = write(tmp_path, "bad", payload)
        with pytest.raises(BankProfileRegistryError, match="inactive_side_marker"):
            load_built_in_profile(path)


class TestDeserializedSemantics:
    def test_inactive_side_marker_survives_registry_deserialization(self, tmp_path: Path):
        payload = artifact()
        payload["profile"]["money_columns"]["inactive_side_marker"] = "empty_or_zero"
        entry = load_built_in_profile(write(tmp_path, "zero", payload))
        assert entry.profile.money_columns.inactive_side_marker is InactiveSideMarker.EMPTY_OR_ZERO

    def test_omitting_the_marker_still_means_empty_only(self, tmp_path: Path):
        entry = load_built_in_profile(write(tmp_path, "plain", artifact()))
        assert entry.profile.money_columns.inactive_side_marker is InactiveSideMarker.EMPTY_ONLY


class TestReadOnlySemantics:
    def test_entries_are_frozen_and_the_collection_is_a_tuple(self, tmp_path: Path):
        write(tmp_path, "one", artifact())
        registry = BankProfileRegistry.from_directory(tmp_path)
        (entry,) = registry.entries
        assert isinstance(registry.entries, tuple)
        with pytest.raises(Exception):
            entry.label = "rewritten"  # type: ignore[misc]
        with pytest.raises(Exception):
            entry.profile.profile_id = "rewritten"  # type: ignore[misc]

    def test_mutating_a_returned_metadata_dict_cannot_affect_the_registry(self, tmp_path: Path):
        write(tmp_path, "one", artifact())
        registry = BankProfileRegistry.from_directory(tmp_path)
        entry = registry.require("synthetic_registry_v1")
        metadata = entry.metadata()
        metadata["label"] = "tampered"
        metadata["expected_headers"].append("Injected")
        assert registry.require("synthetic_registry_v1").label == "Synthetic registry fixture"
        assert "Injected" not in registry.require("synthetic_registry_v1").expected_headers

    def test_require_raises_for_an_unknown_id(self, tmp_path: Path):
        with pytest.raises(KeyError):
            BankProfileRegistry.from_directory(tmp_path).require("nope")


class TestWhatActuallyShips:
    def test_the_shipped_registry_loads(self):
        registry = built_in_registry()
        assert len(registry) == len(list(BUILT_IN_PROFILE_DIR.glob("*.json")))

    def test_no_shipped_profile_claims_to_be_a_verified_real_bank(self):
        """The load-bearing honesty check.

        This repository contains no trustworthy documentation or export
        sample for any real bank's CSV schema (see the adapter README,
        "ICICI: not shipped, and why"). Until one is checked in, every
        shipped entry must be classified ``demo_fixture``; promoting one
        requires adding the evidence and changing this test deliberately.
        """
        for entry in built_in_registry():
            assert entry.verification is ProfileVerification.DEMO_FIXTURE, entry.profile_id
            assert entry.evidence.strip()
