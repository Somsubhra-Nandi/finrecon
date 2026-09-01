"""Saved mappings inside schema detection: what may and may not be reused.

The safety argument for reusing a saved mapping is identical to the one for
a built-in: FinRecon recognises a schema a human already reviewed, by
comparing header rows, through two tiers and no weaker. These tests exist to
hold that line specifically where a saved mapping might tempt someone to
relax it -- because it is the operator's own mapping, because it has a
friendly name, because there is only one of them.

None of those is a reason. A saved mapping is exactly as capable of
mis-stating money as any other, and the fact that nobody outside this
deployment reviewed it argues for more caution, not less.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finrecon.adapters.bank.schema import (
    BankProfileRegistry,
    CombinedMappingRegistry,
    MatchStatus,
    MatchTier,
    SavedMappingEntry,
    inspect_bank_csv,
)
from finrecon.adapters.bank.schema.registry import REGISTRY_ARTIFACT_VERSION
from finrecon.ledger.bank_mappings import BankMappingStore
from finrecon.ledger.store import LedgerStore

HEADERS = ("Txn Reference", "Posted On", "Particulars", "Withdrawal Amt", "Deposit Amt")
HEADER_LINE = ",".join(HEADERS)
BODY = "UTR1,07/08/2024,NEFT RZP,0.00,125000.00\n"


def csv_bytes(header_line: str = HEADER_LINE, body: str = BODY) -> bytes:
    return f"{header_line}\n{body}".encode("utf-8")


def mapping_payload(**overrides) -> dict:
    payload = {
        "profile_id": "assigned-by-store",
        "currency": "INR",
        "value_date_column": "Posted On",
        "value_date_format": "%d/%m/%Y",
        "narration_column": "Particulars",
        "reference_id_column": "Txn Reference",
        "money_columns": {
            "kind": "debit_credit",
            "debit_column": "Withdrawal Amt",
            "credit_column": "Deposit Amt",
            "inactive_side_marker": "empty_or_zero",
        },
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def store(tmp_path: Path):
    ledger = LedgerStore(tmp_path / "l.sqlite3")
    yield BankMappingStore(ledger.connection)
    ledger.close()


def save(store: BankMappingStore, name: str, headers=HEADERS, **overrides):
    return store.create_mapping(
        name=name,
        profile_payload=mapping_payload(**overrides),
        raw_headers=headers,
        delimiter=",",
        encoding="utf-8",
    )


def corpus(store: BankMappingStore, built_ins: BankProfileRegistry | None = None):
    return CombinedMappingRegistry(
        built_ins if built_ins is not None else BankProfileRegistry(()),
        store.active_entries(),
    )


def built_in_registry_with(profile_id: str, headers: tuple[str, ...], tmp_path: Path):
    """A one-entry built-in registry, authored for this test only."""
    import json

    directory = tmp_path / f"profiles-{profile_id}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "a.json").write_text(
        json.dumps(
            {
                "registry_artifact_version": REGISTRY_ARTIFACT_VERSION,
                "profile_id": profile_id,
                "label": f"Synthetic {profile_id}",
                "version": "v1",
                "verification": "demo_fixture",
                "description": "Authored for tests only.",
                "evidence": "Synthetic; describes no real bank.",
                "expected_headers": list(headers),
                "profile": mapping_payload(profile_id=profile_id),
            }
        ),
        encoding="utf-8",
    )
    return BankProfileRegistry.from_directory(directory)


class TestReuse:
    def test_an_exact_schema_match_selects_the_saved_mapping(self, store):
        entry = save(store, "HDFC Current Account")
        inspection = inspect_bank_csv(csv_bytes(), corpus(store))
        assert inspection.status is MatchStatus.MATCHED
        assert inspection.match_tier is MatchTier.EXACT
        assert isinstance(inspection.profile, SavedMappingEntry)
        assert inspection.profile.mapping_id == entry.mapping_id
        assert inspection.profile.mapping_version == 1

    def test_a_safe_normalized_match_still_selects(self, store):
        """Representation-only differences: BOM, spacing, case.

        None of these change which column a bank named, which is the whole
        reason this tier is allowed to exist at all.
        """
        save(store, "HDFC Current Account")
        wobbly = "  txn   REFERENCE ,Posted  On,  particulars,Withdrawal Amt , DEPOSIT AMT"
        inspection = inspect_bank_csv(csv_bytes(wobbly), corpus(store))
        assert inspection.status is MatchStatus.MATCHED
        assert inspection.match_tier is MatchTier.SAFE_NORMALIZED

    def test_the_active_version_is_the_one_reused(self, store):
        entry = save(store, "HDFC Current Account")
        store.add_version(
            mapping_id=entry.mapping_id,
            profile_payload=mapping_payload(reference_id_column=None),
            raw_headers=HEADERS, delimiter=",", encoding="utf-8",
        )
        inspection = inspect_bank_csv(csv_bytes(), corpus(store))
        assert inspection.profile.mapping_version == 2
        assert inspection.profile.profile.reference_id_column is None

    def test_a_superseded_version_never_competes_with_its_successor(self, store):
        """Otherwise editing a mapping would manufacture an ambiguity."""
        entry = save(store, "HDFC Current Account")
        store.add_version(
            mapping_id=entry.mapping_id,
            profile_payload=mapping_payload(),
            raw_headers=HEADERS, delimiter=",", encoding="utf-8",
        )
        inspection = inspect_bank_csv(csv_bytes(), corpus(store))
        assert inspection.status is MatchStatus.MATCHED


class TestFailClosed:
    def test_a_renamed_column_makes_the_file_unknown_again(self, store):
        """The central safety property of reuse.

        A statement whose header row changed is not the statement the mapping
        was confirmed against. Forcing the old mapping onto it would read
        somebody's money under a declaration that no longer describes the
        file.
        """
        save(store, "HDFC Current Account")
        renamed = HEADER_LINE.replace("Particulars", "Description")
        inspection = inspect_bank_csv(csv_bytes(renamed), corpus(store))
        assert inspection.status is MatchStatus.UNKNOWN
        assert inspection.profile is None
        # And pointedly no "closest match" consolation prize.
        assert inspection.candidates == ()

    def test_reordered_columns_are_a_different_schema(self, store):
        save(store, "HDFC Current Account")
        reordered = ",".join(
            ("Posted On", "Txn Reference", "Particulars", "Withdrawal Amt", "Deposit Amt")
        )
        inspection = inspect_bank_csv(csv_bytes(reordered), corpus(store))
        assert inspection.status is MatchStatus.UNKNOWN

    def test_an_extra_column_is_a_different_schema(self, store):
        save(store, "HDFC Current Account")
        wider = HEADER_LINE + ",Running Balance"
        inspection = inspect_bank_csv(csv_bytes(wider), corpus(store))
        assert inspection.status is MatchStatus.UNKNOWN

    def test_an_unknown_schema_with_no_saved_mappings_stays_unknown(self, store):
        inspection = inspect_bank_csv(csv_bytes(), corpus(store))
        assert inspection.status is MatchStatus.UNKNOWN
        assert inspection.match_tier is None

    def test_two_saved_mappings_for_one_schema_are_ambiguous(self, store):
        """No tie-break exists, and none is invented for saved mappings.

        Not newest, not highest version, not alphabetical, not insertion
        order. Ambiguity is a human decision.
        """
        save(store, "Mapping A")
        save(store, "Mapping B")
        inspection = inspect_bank_csv(csv_bytes(), corpus(store))
        assert inspection.status is MatchStatus.AMBIGUOUS
        assert inspection.profile is None
        assert {e.name for e in inspection.candidates} == {"Mapping A", "Mapping B"}

    def test_a_saved_mapping_tying_with_a_built_in_is_ambiguous(self, store, tmp_path):
        """The tie is not resolved by preferring either kind.

        A deployment's own mapping does not outrank a shipped profile, and a
        shipped profile does not outrank the operator's. Both readings are
        plausible and only a person can say which was intended.
        """
        save(store, "My own mapping")
        built_ins = built_in_registry_with("shipped_v1", HEADERS, tmp_path)
        inspection = inspect_bank_csv(csv_bytes(), corpus(store, built_ins))
        assert inspection.status is MatchStatus.AMBIGUOUS
        kinds = {type(entry).__name__ for entry in inspection.candidates}
        assert kinds == {"SavedMappingEntry", "BuiltInProfile"}


class TestNameIsNotMatching:
    def test_the_mapping_name_does_not_influence_matching(self, store):
        """Two mappings whose names differ wildly, over one identical schema.

        If the name played any part in matching, these would resolve
        differently. They do not: both match, which is why the result is an
        ambiguity rather than a preference for whichever name looked more
        like a bank.
        """
        save(store, "HDFC Bank Current Account 2024")
        save(store, "zzz random label")
        inspection = inspect_bank_csv(csv_bytes(), corpus(store))
        assert inspection.status is MatchStatus.AMBIGUOUS

    def test_a_bank_sounding_name_does_not_match_an_unrelated_schema(self, store):
        """The complement: a plausible bank name buys no recognition at all."""
        save(store, "HDFC Bank Current Account")
        unrelated = "Date,Description,Amount,Type"
        inspection = inspect_bank_csv(csv_bytes(unrelated, "01/01/2024,X,1,CR\n"), corpus(store))
        assert inspection.status is MatchStatus.UNKNOWN

    def test_renaming_a_mapping_changes_nothing_about_what_it_recognises(self, store):
        entry = save(store, "First name")
        before = inspect_bank_csv(csv_bytes(), corpus(store))
        store.add_version(
            mapping_id=entry.mapping_id,
            profile_payload=mapping_payload(),
            raw_headers=HEADERS, delimiter=",", encoding="utf-8",
            name="Completely different name",
        )
        after = inspect_bank_csv(csv_bytes(), corpus(store))
        assert before.status is after.status is MatchStatus.MATCHED
        assert before.profile.schema_signature == after.profile.schema_signature
        assert after.profile.name == "Completely different name"


class TestBuiltInsUnaffected:
    def test_a_built_in_still_matches_when_saved_mappings_exist(self, store, tmp_path):
        save(store, "Unrelated mapping", headers=("A", "B", "C", "D", "E"),
             value_date_column="B", narration_column="C",
             reference_id_column="A",
             money_columns={"kind": "debit_credit", "debit_column": "D", "credit_column": "E"})
        built_ins = built_in_registry_with("shipped_v1", HEADERS, tmp_path)
        inspection = inspect_bank_csv(csv_bytes(), corpus(store, built_ins))
        assert inspection.status is MatchStatus.MATCHED
        assert inspection.profile.profile_id == "shipped_v1"
