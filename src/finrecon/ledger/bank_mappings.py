"""Persistence for user-confirmed bank column mappings.

Lives in the ledger because the ledger is already this product's durable
store, already the thing Docker mounts a volume for, and already the place a
reviewer looks for a record of what a batch did. A second persistence
mechanism -- a JSON directory, a separate database file -- would double the
number of things that must survive a container recreation to make one
feature work, for no gain.

Two invariants, both enforced by the schema rather than by the functions
below, because an invariant that depends on every caller remembering it is
not an invariant:

* **A version row is immutable.** Editing a mapping inserts the next version
  and flips the previous row's ``status`` to ``superseded``. No column of a
  version row's mapping is ever rewritten. A batch recorded last quarter
  names ``(mapping_id, version)``, and changing what that pair means would
  rewrite the meaning of evidence already recorded -- exactly the rule
  :mod:`finrecon.adapters.bank.schema.registry` states for built-ins.
* **At most one version is active.** A partial unique index, so two active
  versions of one mapping cannot exist even transiently, and reuse never has
  to choose between them.

**Provenance is always ``human_confirmed``.** The column has a CHECK
constraint permitting exactly that value, so a future code path that tried
to persist an unconfirmed proposal would be refused by the database. A
model's involvement, when there was one, is recorded separately in
``llm_proposal_json`` as metadata beside the mapping, never as its authority.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from finrecon.adapters.bank.profile_json import (
    BankProfileFormatError,
    profile_from_payload,
)
from finrecon.adapters.bank.schema.saved import (
    SAVED_MAPPING_PROVENANCE,
    SAVED_MAPPING_SOURCE,
    SavedMappingEntry,
)
from finrecon.adapters.bank.schema.signature import signature_from_headers
from finrecon.ledger.audit import canonical_json

MAX_MAPPING_NAME_CHARS = 120


class BankMappingError(RuntimeError):
    """A saved-mapping operation was refused.

    Carries ``code`` so an HTTP boundary can map it to a response without
    parsing prose, matching the convention
    :class:`finrecon.adapters.bank.schema.detect.BuiltInProfileVerificationError`
    already sets.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def normalize_mapping_name(raw: str) -> str:
    """Collapse a user-supplied mapping name to its stored form.

    Whitespace shape only -- the operator's own words, casing and
    punctuation are kept verbatim, because this is a label they chose and
    will look for. Rejects an empty name: a mapping nobody named is a
    mapping nobody can recognise in a list.
    """
    name = " ".join(raw.split())
    if not name:
        raise BankMappingError(
            "mapping_name_required",
            "Give this mapping a name so it can be recognised next time.",
        )
    if len(name) > MAX_MAPPING_NAME_CHARS:
        raise BankMappingError(
            "mapping_name_too_long",
            f"A mapping name must be {MAX_MAPPING_NAME_CHARS} characters or fewer.",
        )
    return name


def mapping_id_for(name: str, created_at: str) -> str:
    """A stable internal id, derived and opaque.

    Derived from the name and creation instant so it is deterministic for a
    given creation, and opaque so nothing downstream is tempted to parse a
    bank out of it. The *name* remains editable in principle without the id
    moving, which is why versions and batches key on the id.
    """
    digest = hashlib.sha256(
        canonical_json({"name": name, "created_at": created_at}).encode("utf-8")
    ).hexdigest()
    return f"bankmap_{digest[:16]}"


def profile_id_for(mapping_id: str, version: int) -> str:
    """The ``profile_id`` one version of one mapping reads statements under.

    Version-bearing on purpose. ``profile_id`` namespaces every
    ``bank_record_id`` the mapping produces, so a v2 that renamed the
    reference column must not produce record identities indistinguishable
    from v1's -- they are different readings of the source, and the identity
    should say so.
    """
    return f"{mapping_id}:v{version}"


class BankMappingStore:
    """Saved-mapping reads and writes over an existing ledger connection.

    Takes a connection rather than a path so it shares the request-scoped
    :class:`~finrecon.ledger.store.LedgerStore` connection, its schema
    creation and its transaction boundaries. It does not open, close or own
    a database.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    # --- reads -----------------------------------------------------------

    def _entry_from_row(self, row: sqlite3.Row, name: str) -> SavedMappingEntry:
        payload = json.loads(row["profile_json"])
        try:
            profile = profile_from_payload(payload)
        except BankProfileFormatError as exc:
            # A stored mapping that no longer decodes is a corrupted record,
            # not a mapping to use on a best-effort basis. Loud, because the
            # alternative is reading somebody's statement under a partially
            # understood declaration.
            raise BankMappingError(
                "corrupt_saved_mapping",
                f"Saved mapping {row['mapping_id']} version {row['version']} could "
                f"not be read back: {exc}",
            ) from exc
        return SavedMappingEntry(
            mapping_id=str(row["mapping_id"]),
            name=name,
            mapping_version=int(row["version"]),
            profile_id=str(row["profile_id"]),
            schema_signature=str(row["schema_signature"]),
            expected_headers=tuple(json.loads(row["raw_headers_json"])),
            normalized_headers=tuple(json.loads(row["normalized_headers_json"])),
            delimiter=str(row["delimiter"]),
            encoding=str(row["encoding"]),
            profile=profile,
            profile_payload=payload,
            status=str(row["status"]),
            provenance=str(row["provenance"]),
            source=str(row["source"]),
            created_at=str(row["created_at"]),
            llm_proposal=(
                json.loads(row["llm_proposal_json"])
                if row["llm_proposal_json"]
                else None
            ),
        )

    def _versions(self, where: str, args: tuple) -> list[SavedMappingEntry]:
        rows = self._conn.execute(
            "SELECT v.*, m.name AS mapping_name FROM bank_mapping_versions v "
            "JOIN bank_mappings m ON m.mapping_id = v.mapping_id "
            f"{where}",
            args,
        ).fetchall()
        return [self._entry_from_row(row, str(row["mapping_name"])) for row in rows]

    def active_entries(self) -> tuple[SavedMappingEntry, ...]:
        """Every currently active mapping version, ordered by name.

        This is the detection corpus. Superseded and disabled versions are
        excluded here and only here, so no caller has to remember to filter.
        """
        return tuple(
            self._versions(
                "WHERE v.status = 'active' ORDER BY m.name, v.mapping_id", ()
            )
        )

    def versions_of(self, mapping_id: str) -> tuple[SavedMappingEntry, ...]:
        """Every version of one mapping, oldest first. Includes superseded."""
        return tuple(
            self._versions(
                "WHERE v.mapping_id = ? ORDER BY v.version", (mapping_id,)
            )
        )

    def active_version(self, mapping_id: str) -> SavedMappingEntry | None:
        entries = self._versions(
            "WHERE v.mapping_id = ? AND v.status = 'active'", (mapping_id,)
        )
        return entries[0] if entries else None

    def entry_by_profile_id(self, profile_id: str) -> SavedMappingEntry | None:
        """One exact version, by the ``profile_id`` it reads statements under.

        Used by audit read-back: a batch recorded a ``profile_id``, and this
        resolves it to the exact version -- active or long superseded -- that
        produced its records.
        """
        entries = self._versions("WHERE v.profile_id = ?", (profile_id,))
        return entries[0] if entries else None

    def names(self) -> dict[str, str]:
        return {
            str(row["mapping_id"]): str(row["name"])
            for row in self._conn.execute("SELECT mapping_id, name FROM bank_mappings")
        }

    # --- writes ----------------------------------------------------------

    def create_mapping(
        self,
        *,
        name: str,
        profile_payload: dict,
        raw_headers: tuple[str, ...],
        delimiter: str,
        encoding: str,
        llm_proposal: dict | None = None,
        created_at: str | None = None,
    ) -> SavedMappingEntry:
        """Persist version 1 of a newly named, human-confirmed mapping."""
        clean_name = normalize_mapping_name(name)
        existing = self._conn.execute(
            "SELECT mapping_id FROM bank_mappings WHERE name = ?", (clean_name,)
        ).fetchone()
        if existing is not None:
            raise BankMappingError(
                "mapping_name_taken",
                f"A saved mapping named {clean_name!r} already exists. Choose a "
                "different name, or edit that mapping to create a new version.",
            )
        stamp = created_at or datetime.now(timezone.utc).isoformat()
        mapping_id = mapping_id_for(clean_name, stamp)
        with self._conn:
            self._conn.execute(
                "INSERT INTO bank_mappings (mapping_id, name, created_at) VALUES (?, ?, ?)",
                (mapping_id, clean_name, stamp),
            )
            self._insert_version(
                mapping_id=mapping_id,
                version=1,
                profile_payload=profile_payload,
                raw_headers=raw_headers,
                delimiter=delimiter,
                encoding=encoding,
                llm_proposal=llm_proposal,
                created_at=stamp,
            )
        entry = self.active_version(mapping_id)
        assert entry is not None  # just inserted, inside the same connection
        return entry

    def add_version(
        self,
        *,
        mapping_id: str,
        profile_payload: dict,
        raw_headers: tuple[str, ...],
        delimiter: str,
        encoding: str,
        llm_proposal: dict | None = None,
        created_at: str | None = None,
        name: str | None = None,
    ) -> SavedMappingEntry:
        """Supersede the active version with a new, immutable one.

        The previous row is retired, never overwritten, so a batch that
        already names it keeps naming exactly what it used. ``name`` may be
        supplied to relabel the logical mapping at the same time; the
        mapping id, and therefore every historical reference to it, is
        unaffected.
        """
        current = self._conn.execute(
            "SELECT mapping_id, name FROM bank_mappings WHERE mapping_id = ?",
            (mapping_id,),
        ).fetchone()
        if current is None:
            raise BankMappingError(
                "unknown_bank_mapping", f"No saved mapping {mapping_id!r} exists."
            )
        active = self.active_version(mapping_id)
        row = self._conn.execute(
            "SELECT MAX(version) AS top FROM bank_mapping_versions WHERE mapping_id = ?",
            (mapping_id,),
        ).fetchone()
        next_version = int(row["top"] or 0) + 1
        stamp = created_at or datetime.now(timezone.utc).isoformat()
        clean_name = normalize_mapping_name(name) if name is not None else None
        if clean_name is not None and clean_name != str(current["name"]):
            taken = self._conn.execute(
                "SELECT mapping_id FROM bank_mappings WHERE name = ? AND mapping_id != ?",
                (clean_name, mapping_id),
            ).fetchone()
            if taken is not None:
                raise BankMappingError(
                    "mapping_name_taken",
                    f"A saved mapping named {clean_name!r} already exists.",
                )
        with self._conn:
            if active is not None:
                self._conn.execute(
                    "UPDATE bank_mapping_versions SET status = 'superseded' "
                    "WHERE mapping_id = ? AND version = ?",
                    (mapping_id, active.mapping_version),
                )
            if clean_name is not None:
                self._conn.execute(
                    "UPDATE bank_mappings SET name = ? WHERE mapping_id = ?",
                    (clean_name, mapping_id),
                )
            self._insert_version(
                mapping_id=mapping_id,
                version=next_version,
                profile_payload=profile_payload,
                raw_headers=raw_headers,
                delimiter=delimiter,
                encoding=encoding,
                llm_proposal=llm_proposal,
                created_at=stamp,
            )
        entry = self.active_version(mapping_id)
        assert entry is not None
        return entry

    def _insert_version(
        self,
        *,
        mapping_id: str,
        version: int,
        profile_payload: dict,
        raw_headers: tuple[str, ...],
        delimiter: str,
        encoding: str,
        llm_proposal: dict | None,
        created_at: str,
    ) -> None:
        """Write one immutable version row. Caller owns the transaction.

        The stored ``profile_id`` is assigned here from
        ``(mapping_id, version)`` and the incoming payload's own
        ``profile_id``, whatever it said, is overwritten. A client cannot
        choose the identifier that namespaces canonical record IDs.
        """
        payload = dict(profile_payload)
        payload["profile_id"] = profile_id_for(mapping_id, version)
        try:
            profile = profile_from_payload(payload)
        except BankProfileFormatError as exc:
            raise BankMappingError(
                "invalid_bank_mapping", f"The mapping is not a valid bank profile: {exc}"
            ) from exc
        missing = sorted(profile.declared_columns() - set(raw_headers))
        if missing:
            # The same check the built-in registry makes at load time, for
            # the same reason: a mapping whose own declared header row cannot
            # satisfy its own columns can never match anything.
            raise BankMappingError(
                "mapping_columns_absent_from_schema",
                f"The mapping declares column(s) {missing} that are not in the "
                f"statement's header row {list(raw_headers)}.",
            )
        signature = signature_from_headers(
            raw_headers, delimiter=profile.delimiter, encoding=profile.encoding
        )
        self._conn.execute(
            "INSERT INTO bank_mapping_versions (mapping_id, version, profile_id, "
            "schema_signature, raw_headers_json, normalized_headers_json, delimiter, "
            "encoding, profile_json, status, provenance, source, llm_proposal_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
            (
                mapping_id,
                version,
                payload["profile_id"],
                signature.digest,
                canonical_json(list(raw_headers)),
                canonical_json(list(signature.normalized_headers)),
                delimiter,
                encoding,
                canonical_json(payload),
                SAVED_MAPPING_PROVENANCE,
                SAVED_MAPPING_SOURCE,
                canonical_json(llm_proposal) if llm_proposal is not None else None,
                created_at,
            ),
        )


__all__ = [
    "MAX_MAPPING_NAME_CHARS",
    "BankMappingError",
    "BankMappingStore",
    "mapping_id_for",
    "normalize_mapping_name",
    "profile_id_for",
]
