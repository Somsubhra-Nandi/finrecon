"""SQLite persistence for Stage-2 decisions, candidates, snapshots and audit.

Everything written here is keyed deterministically, and every write is an
``INSERT ... ON CONFLICT DO NOTHING`` against those keys. That is the whole
idempotency mechanism: a rerun of the same batch computes the same keys and
the database refuses the duplicates. There is no "have I seen this before?"
lookup in application code to get wrong.

The store computes no metrics (DESIGN.md §3). It answers only questions of
record: what was decided, what was linked, what candidates a case had, and
what the audit trail says.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from finrecon.candidates.snapshot import CandidateRecord, CaseSnapshot
from finrecon.ledger.audit import audit_row, canonical_json
from finrecon.ledger.schema import SCHEMA_STATEMENTS, SCHEMA_VERSION
from finrecon.matchers.result import ReconciliationDecision


class BatchIdentityError(RuntimeError):
    """Raised when a batch ID is reused for different content.

    Idempotency means *the same batch* reprocessed yields the same result.
    Different records arriving under a batch ID the ledger has already
    recorded is not a rerun, it is a collision, and silently upserting over
    it would corrupt the trail. Loud failure is the correct behaviour.
    """


class LedgerStore:
    """A SQLite-backed Stage-2 ledger.

    Usable as a context manager. ``path=":memory:"`` gives tests a store
    with the same schema and the same constraints as the on-disk one.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def __enter__(self) -> "LedgerStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def _create_schema(self) -> None:
        with self._conn:
            for statement in SCHEMA_STATEMENTS:
                self._conn.execute(statement)
            self._conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT (key) DO NOTHING",
                (str(SCHEMA_VERSION),),
            )

    # --- batches ---------------------------------------------------------

    def register_batch(
        self,
        *,
        batch_id: str,
        split: str,
        content_fingerprint: str,
        record_count: int,
        case_count: int,
    ) -> None:
        existing = self._conn.execute(
            "SELECT content_fingerprint FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if existing is not None:
            if existing["content_fingerprint"] != content_fingerprint:
                raise BatchIdentityError(
                    f"batch {batch_id!r} was already recorded with a different content "
                    "fingerprint; reprocessing requires identical input records"
                )
            return
        with self._conn:
            self._conn.execute(
                "INSERT INTO batches (batch_id, split, content_fingerprint, record_count, "
                "case_count) VALUES (?, ?, ?, ?, ?) ON CONFLICT (batch_id) DO NOTHING",
                (batch_id, split, content_fingerprint, record_count, case_count),
            )

    # --- decisions -------------------------------------------------------

    def record_decision(
        self, batch_id: str, decision: ReconciliationDecision, amount_paise: int
    ) -> None:
        """Persist one decision plus its proven links. Safe to replay."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO cases (batch_id, case_id, bank_record_id, status, matcher_id, "
                "rule_id, relationship, amount_paise) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (batch_id, case_id) DO NOTHING",
                (
                    batch_id,
                    decision.case_id,
                    decision.bank_record_id,
                    decision.status.value,
                    decision.matcher_id,
                    decision.rule_id,
                    decision.relationship,
                    amount_paise,
                ),
            )
            for ordinal, settlement_id in enumerate(decision.settlement_ids):
                self._conn.execute(
                    "INSERT INTO case_links (batch_id, case_id, settlement_id, ordinal) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (batch_id, case_id, settlement_id) DO NOTHING",
                    (batch_id, decision.case_id, settlement_id, ordinal),
                )

    def record_candidates(
        self, batch_id: str, case_id: str, candidates: tuple[CandidateRecord, ...]
    ) -> None:
        with self._conn:
            for ordinal, candidate in enumerate(candidates):
                self._conn.execute(
                    "INSERT INTO case_candidates (batch_id, case_id, candidate_id, ordinal, "
                    "settlement_ids, total_paise, unexplained_delta_paise, blocking_rule) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (batch_id, case_id, candidate_id) DO NOTHING",
                    (
                        batch_id,
                        case_id,
                        candidate.candidate_id,
                        ordinal,
                        canonical_json(list(candidate.settlement_ids)),
                        candidate.total_paise,
                        candidate.unexplained_delta_paise,
                        candidate.blocking_rule,
                    ),
                )

    def record_snapshot(self, snapshot: CaseSnapshot) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO case_snapshots (batch_id, case_id, content_hash, payload_json) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (batch_id, case_id) DO NOTHING",
                (
                    snapshot.batch_id,
                    snapshot.case_id,
                    snapshot.content_hash,
                    canonical_json(snapshot.model_dump(mode="json")),
                ),
            )

    def record_audit(
        self, batch_id: str, decision: ReconciliationDecision, sequence: int
    ) -> None:
        row = audit_row(batch_id, decision, sequence)
        with self._conn:
            self._conn.execute(
                "INSERT INTO audit_log (audit_id, batch_id, case_id, sequence, decision, "
                "matcher_id, rule_id, settlement_ids, evidence_json) "
                "VALUES (:audit_id, :batch_id, :case_id, :sequence, :decision, :matcher_id, "
                ":rule_id, :settlement_ids, :evidence_json) "
                "ON CONFLICT (audit_id) DO NOTHING",
                row,
            )

    # --- reads -----------------------------------------------------------

    def count(self, table: str) -> int:
        if table not in {
            "batches",
            "cases",
            "case_links",
            "case_candidates",
            "case_snapshots",
            "audit_log",
        }:
            raise ValueError(f"unknown ledger table: {table!r}")
        return int(self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

    def case_rows(self, batch_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM cases WHERE batch_id = ? ORDER BY case_id", (batch_id,)
            )
        )

    def link_rows(self, batch_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM case_links WHERE batch_id = ? ORDER BY case_id, ordinal",
                (batch_id,),
            )
        )

    def candidate_rows(self, batch_id: str, case_id: str | None = None) -> list[sqlite3.Row]:
        if case_id is None:
            return list(
                self._conn.execute(
                    "SELECT * FROM case_candidates WHERE batch_id = ? ORDER BY case_id, ordinal",
                    (batch_id,),
                )
            )
        return list(
            self._conn.execute(
                "SELECT * FROM case_candidates WHERE batch_id = ? AND case_id = ? ORDER BY ordinal",
                (batch_id, case_id),
            )
        )

    def audit_rows(self, batch_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM audit_log WHERE batch_id = ? ORDER BY sequence", (batch_id,)
            )
        )

    def snapshot_payload(self, batch_id: str, case_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT payload_json FROM case_snapshots WHERE batch_id = ? AND case_id = ?",
            (batch_id, case_id),
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def status_counts(self, batch_id: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM cases WHERE batch_id = ? GROUP BY status "
            "ORDER BY status",
            (batch_id,),
        )
        return {row["status"]: int(row["n"]) for row in rows}

    def digest(self, batch_id: str) -> str:
        """A stable digest of everything recorded for one batch.

        Used by the idempotency test: two runs of the same batch must
        produce byte-identical ledger content, not merely equal row counts.
        """
        parts: list[str] = []
        for row in self.case_rows(batch_id):
            parts.append(canonical_json({k: row[k] for k in row.keys()}))
        for row in self.link_rows(batch_id):
            parts.append(canonical_json({k: row[k] for k in row.keys()}))
        for row in self.candidate_rows(batch_id):
            parts.append(canonical_json({k: row[k] for k in row.keys()}))
        for row in self.audit_rows(batch_id):
            parts.append(canonical_json({k: row[k] for k in row.keys()}))
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def open_ledger(path: str | Path) -> LedgerStore:
    """Open (creating if needed) a ledger at ``path``."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return LedgerStore(path)


__all__ = ["BatchIdentityError", "LedgerStore", "open_ledger"]
