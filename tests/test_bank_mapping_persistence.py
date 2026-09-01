"""Saved bank mappings: persistence, versioning, and round-trip fidelity.

The properties asserted here are the ones the whole feature rests on. A
mapping that does not survive a restart is not a saved mapping, and a
version that quietly changes meaning after a batch has cited it is worse
than no versioning at all -- it rewrites the meaning of recorded evidence.

Every test opens a real on-disk SQLite ledger rather than ``:memory:``.
That is deliberate: an in-memory store cannot distinguish "persisted" from
"still in this process", which is the exact distinction under test.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from finrecon.adapters.bank.csv_profile import DebitCreditColumns, InactiveSideMarker
from finrecon.ledger.bank_mappings import (
    BankMappingError,
    BankMappingStore,
    profile_id_for,
)
from finrecon.ledger.store import LedgerStore

HEADERS = ("Txn Reference", "Posted On", "Particulars", "Withdrawal Amt", "Deposit Amt")


def mapping_payload(**overrides) -> dict:
    payload = {
        "profile_id": "ignored-by-the-store",
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
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "finrecon.sqlite3"


def open_store(path: Path) -> tuple[LedgerStore, BankMappingStore]:
    ledger = LedgerStore(path)
    return ledger, BankMappingStore(ledger.connection)


def save(store: BankMappingStore, name: str = "HDFC Current Account", **kwargs):
    return store.create_mapping(
        name=name,
        profile_payload=mapping_payload(**kwargs.pop("profile_overrides", {})),
        raw_headers=HEADERS,
        delimiter=",",
        encoding="utf-8",
        **kwargs,
    )


class TestPersistence:
    def test_a_human_confirmed_mapping_is_persisted_and_readable(self, ledger_path: Path):
        ledger, store = open_store(ledger_path)
        entry = save(store)
        assert entry.mapping_version == 1
        assert entry.status == "active"
        assert entry.provenance == "human_confirmed"
        assert entry.source == "user_saved"
        listed = store.active_entries()
        assert [e.name for e in listed] == ["HDFC Current Account"]
        ledger.close()

    def test_a_mapping_survives_the_process_that_created_it(self, ledger_path: Path):
        """The restart test, as literally as a test can express it.

        Two separate ``LedgerStore`` objects over two separate connections,
        the first fully closed before the second opens. Nothing is carried
        across in Python; the only thing that can bridge them is the file.
        """
        ledger, store = open_store(ledger_path)
        original = save(store, "Client XYZ Bank Export")
        ledger.close()

        reopened, reloaded_store = open_store(ledger_path)
        reloaded = reloaded_store.active_version(original.mapping_id)
        assert reloaded is not None
        assert reloaded.name == "Client XYZ Bank Export"
        assert reloaded.profile_id == original.profile_id
        assert reloaded.schema_signature == original.schema_signature
        reopened.close()

    def test_the_schema_signature_and_headers_round_trip_exactly(self, ledger_path: Path):
        ledger, store = open_store(ledger_path)
        entry = save(store)
        assert entry.expected_headers == HEADERS
        # Recomputed from the stored headers, so the digest can never drift
        # from the header row it summarises.
        assert entry.signature.digest == entry.schema_signature
        ledger.close()

    def test_the_profile_payload_round_trips_through_the_database(self, ledger_path: Path):
        ledger, store = open_store(ledger_path)
        entry = save(store)
        ledger.close()

        reopened, reloaded_store = open_store(ledger_path)
        reloaded = reloaded_store.active_version(entry.mapping_id)
        assert reloaded is not None
        profile = reloaded.profile
        assert profile.value_date_column == "Posted On"
        assert profile.value_date_format == "%d/%m/%Y"
        assert profile.narration_column == "Particulars"
        assert profile.reference_id_column == "Txn Reference"
        assert isinstance(profile.money_columns, DebitCreditColumns)
        assert profile.money_columns.debit_column == "Withdrawal Amt"
        assert profile.money_columns.credit_column == "Deposit Amt"
        reopened.close()

    def test_the_inactive_side_marker_round_trips(self, ledger_path: Path):
        """The field whose loss would silently re-read a zero-filled statement.

        Called out separately from the payload round-trip because it is the
        one setting where a lost value does not fail loudly -- it produces a
        different, plausible-looking reading of the same money.
        """
        ledger, store = open_store(ledger_path)
        zero_filled = save(store, "Zero filled")
        empty_only = store.create_mapping(
            name="Empty only",
            profile_payload=mapping_payload(
                money_columns={
                    "kind": "debit_credit",
                    "debit_column": "Withdrawal Amt",
                    "credit_column": "Deposit Amt",
                    "inactive_side_marker": "empty_only",
                }
            ),
            raw_headers=HEADERS,
            delimiter=",",
            encoding="utf-8",
        )
        ledger.close()

        reopened, reloaded_store = open_store(ledger_path)
        first = reloaded_store.active_version(zero_filled.mapping_id)
        second = reloaded_store.active_version(empty_only.mapping_id)
        assert first is not None and second is not None
        assert first.profile.money_columns.inactive_side_marker is InactiveSideMarker.EMPTY_OR_ZERO
        assert second.profile.money_columns.inactive_side_marker is InactiveSideMarker.EMPTY_ONLY
        reopened.close()

    def test_optional_llm_proposal_metadata_round_trips_and_is_optional(
        self, ledger_path: Path
    ):
        ledger, store = open_store(ledger_path)
        with_model = save(
            store,
            "Proposed then confirmed",
            llm_proposal={"provider": "openrouter", "model": "some-model"},
        )
        without_model = save(store, "Typed by hand")
        assert with_model.llm_proposal == {"provider": "openrouter", "model": "some-model"}
        # Absence means nobody consulted a model -- not that nobody confirmed.
        assert without_model.llm_proposal is None
        assert without_model.provenance == "human_confirmed"
        ledger.close()


class TestVersioning:
    def test_editing_creates_a_new_version_and_preserves_the_old_one(
        self, ledger_path: Path
    ):
        ledger, store = open_store(ledger_path)
        first = save(store)
        second = store.add_version(
            mapping_id=first.mapping_id,
            profile_payload=mapping_payload(reference_id_column=None),
            raw_headers=HEADERS,
            delimiter=",",
            encoding="utf-8",
        )
        assert second.mapping_version == 2
        assert second.status == "active"

        versions = store.versions_of(first.mapping_id)
        assert [(v.mapping_version, v.status) for v in versions] == [
            (1, "superseded"),
            (2, "active"),
        ]
        # The old version is not merely present, it is unchanged: its column
        # mapping still says exactly what it said when a batch cited it.
        assert versions[0].profile.reference_id_column == "Txn Reference"
        assert versions[1].profile.reference_id_column is None
        ledger.close()

    def test_each_version_reads_statements_under_its_own_profile_id(
        self, ledger_path: Path
    ):
        """Record identity must distinguish two readings of the same source.

        ``profile_id`` namespaces every ``bank_record_id``. If v1 and v2
        shared one, a record produced under a corrected mapping would be
        indistinguishable from one produced under the mapping it corrected.
        """
        ledger, store = open_store(ledger_path)
        first = save(store)
        second = store.add_version(
            mapping_id=first.mapping_id,
            profile_payload=mapping_payload(reference_id_column=None),
            raw_headers=HEADERS, delimiter=",", encoding="utf-8",
        )
        assert first.profile_id == profile_id_for(first.mapping_id, 1)
        assert second.profile_id == profile_id_for(first.mapping_id, 2)
        assert first.profile_id != second.profile_id
        ledger.close()

    def test_only_the_active_version_is_offered_for_detection(self, ledger_path: Path):
        ledger, store = open_store(ledger_path)
        first = save(store)
        store.add_version(
            mapping_id=first.mapping_id,
            profile_payload=mapping_payload(),
            raw_headers=HEADERS, delimiter=",", encoding="utf-8",
        )
        active = store.active_entries()
        assert [e.mapping_version for e in active] == [2]
        ledger.close()

    def test_a_historical_version_stays_resolvable_by_its_profile_id(
        self, ledger_path: Path
    ):
        """Audit read-back: a batch names a profile_id years later."""
        ledger, store = open_store(ledger_path)
        first = save(store)
        store.add_version(
            mapping_id=first.mapping_id,
            profile_payload=mapping_payload(reference_id_column=None),
            raw_headers=HEADERS, delimiter=",", encoding="utf-8",
        )
        historical = store.entry_by_profile_id(first.profile_id)
        assert historical is not None
        assert historical.mapping_version == 1
        assert historical.status == "superseded"
        assert historical.profile.reference_id_column == "Txn Reference"
        ledger.close()

    def test_the_database_refuses_two_active_versions_of_one_mapping(
        self, ledger_path: Path
    ):
        """The invariant is the schema's, not the store methods'.

        Asserted by going around the store entirely and writing a second
        active row directly. An invariant that only holds while every caller
        remembers it is not an invariant.
        """
        ledger, store = open_store(ledger_path)
        entry = save(store)
        with pytest.raises(sqlite3.IntegrityError):
            ledger.connection.execute(
                "INSERT INTO bank_mapping_versions (mapping_id, version, profile_id, "
                "schema_signature, raw_headers_json, normalized_headers_json, delimiter, "
                "encoding, profile_json, status, provenance, source, llm_proposal_json, "
                "created_at) VALUES (?, 99, 'other:v99', 'sig', '[]', '[]', ',', "
                "'utf-8', '{}', 'active', 'human_confirmed', 'user_saved', NULL, 'now')",
                (entry.mapping_id,),
            )
        ledger.close()

    def test_the_database_refuses_a_provenance_other_than_human_confirmed(
        self, ledger_path: Path
    ):
        """A proposal cannot be persisted as though it were a mapping.

        There is no code path that tries this; the CHECK constraint exists so
        that a future one cannot succeed by accident.
        """
        ledger, store = open_store(ledger_path)
        entry = save(store)
        with pytest.raises(sqlite3.IntegrityError):
            ledger.connection.execute(
                "UPDATE bank_mapping_versions SET provenance = 'llm_proposed' "
                "WHERE mapping_id = ?",
                (entry.mapping_id,),
            )
        ledger.close()


class TestNaming:
    def test_a_duplicate_active_name_is_refused(self, ledger_path: Path):
        ledger, store = open_store(ledger_path)
        save(store, "Finance Team CSV")
        with pytest.raises(BankMappingError) as excinfo:
            save(store, "Finance Team CSV")
        assert excinfo.value.code == "mapping_name_taken"
        ledger.close()

    def test_a_name_need_not_be_a_real_bank(self, ledger_path: Path):
        ledger, store = open_store(ledger_path)
        entry = save(store, "  My   Settlement File  ")
        # Whitespace shape is normalised; the operator's own words are not.
        assert entry.name == "My Settlement File"
        ledger.close()

    def test_an_empty_name_is_refused(self, ledger_path: Path):
        ledger, store = open_store(ledger_path)
        with pytest.raises(BankMappingError) as excinfo:
            save(store, "   ")
        assert excinfo.value.code == "mapping_name_required"
        ledger.close()

    def test_a_mapping_declaring_absent_columns_is_refused(self, ledger_path: Path):
        """The same check the built-in registry makes at load time.

        A mapping whose own declared header row cannot satisfy its own
        columns can never match anything, so it is refused at write time
        rather than discovered at somebody's next upload.
        """
        ledger, store = open_store(ledger_path)
        with pytest.raises(BankMappingError) as excinfo:
            store.create_mapping(
                name="Broken",
                profile_payload=mapping_payload(narration_column="Nope"),
                raw_headers=HEADERS, delimiter=",", encoding="utf-8",
            )
        assert excinfo.value.code == "mapping_columns_absent_from_schema"
        assert store.active_entries() == ()
        ledger.close()

    def test_the_client_supplied_profile_id_is_ignored(self, ledger_path: Path):
        """A caller cannot choose what namespaces canonical record IDs."""
        ledger, store = open_store(ledger_path)
        entry = save(store)
        assert entry.profile_id != "ignored-by-the-store"
        stored = json.loads(
            ledger.connection.execute(
                "SELECT profile_json FROM bank_mapping_versions WHERE profile_id = ?",
                (entry.profile_id,),
            ).fetchone()["profile_json"]
        )
        assert stored["profile_id"] == entry.profile_id
        ledger.close()
