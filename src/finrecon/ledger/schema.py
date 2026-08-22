"""SQLite schema for the Stage-2 ledger.

DESIGN.md §7 picks SQLite so a reviewer needs zero setup, and §3 gives the
store a narrow remit: "Resolutions, audit trail, idempotency" — never
metrics. The schema below is exactly that and nothing more. There is no
human-resolution table (Stage 5) and no agent-evidence table (Stage 3);
adding either now would be scaffolding for work that does not exist.

**Idempotency is enforced by the database, not by application logic.**
Every table's primary key or unique constraint is derived from content
that is itself deterministic — batch ID, case ID, candidate ID, and for
audit rows a content hash. Reprocessing the same batch therefore replays
identical keys, and the writes are ``INSERT ... ON CONFLICT DO NOTHING``.
A duplicate row is refused by the engine even if a future caller forgets
to check first, which is the point: an invariant that depends on every
caller remembering it is not an invariant.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS batches (
        batch_id            TEXT PRIMARY KEY,
        split               TEXT NOT NULL,
        content_fingerprint TEXT NOT NULL,
        record_count        INTEGER NOT NULL,
        case_count          INTEGER NOT NULL
    )
    """,
    # One row per reconciliation decision. The (batch, case) primary key is
    # what makes a rerun an upsert rather than an append.
    """
    CREATE TABLE IF NOT EXISTS cases (
        batch_id       TEXT NOT NULL,
        case_id        TEXT NOT NULL,
        bank_record_id TEXT NOT NULL,
        status         TEXT NOT NULL CHECK (status IN ('resolved', 'unresolved')),
        matcher_id     TEXT NOT NULL,
        rule_id        TEXT NOT NULL,
        relationship   TEXT CHECK (relationship IN ('one_to_one', 'many_to_one')),
        amount_paise   INTEGER NOT NULL,
        PRIMARY KEY (batch_id, case_id),
        FOREIGN KEY (batch_id) REFERENCES batches (batch_id),
        -- A resolved case must name a relationship; an unresolved one must not.
        CHECK ((status = 'resolved') = (relationship IS NOT NULL))
    )
    """,
    # Proven links. The unique constraint is the "no duplicate links"
    # guarantee the idempotency test asserts.
    """
    CREATE TABLE IF NOT EXISTS case_links (
        batch_id      TEXT NOT NULL,
        case_id       TEXT NOT NULL,
        settlement_id TEXT NOT NULL,
        ordinal       INTEGER NOT NULL,
        PRIMARY KEY (batch_id, case_id, settlement_id),
        FOREIGN KEY (batch_id, case_id) REFERENCES cases (batch_id, case_id)
    )
    """,
    # The complete candidate set for an unresolved case. Rows are only ever
    # inserted, never deleted: Stage 3 must not be able to shrink a set.
    """
    CREATE TABLE IF NOT EXISTS case_candidates (
        batch_id                 TEXT NOT NULL,
        case_id                  TEXT NOT NULL,
        candidate_id             TEXT NOT NULL,
        ordinal                  INTEGER NOT NULL,
        settlement_ids           TEXT NOT NULL,
        total_paise              INTEGER NOT NULL,
        unexplained_delta_paise  INTEGER NOT NULL,
        blocking_rule            TEXT NOT NULL,
        PRIMARY KEY (batch_id, case_id, candidate_id),
        FOREIGN KEY (batch_id, case_id) REFERENCES cases (batch_id, case_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS case_snapshots (
        batch_id      TEXT NOT NULL,
        case_id       TEXT NOT NULL,
        content_hash  TEXT NOT NULL,
        payload_json  TEXT NOT NULL,
        PRIMARY KEY (batch_id, case_id),
        FOREIGN KEY (batch_id, case_id) REFERENCES cases (batch_id, case_id)
    )
    """,
    # Audit rows are keyed by a content hash of the decision they record, so
    # a logically identical decision written twice collapses to one row.
    # `sequence` orders the trail within a batch deterministically.
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        audit_id      TEXT PRIMARY KEY,
        batch_id      TEXT NOT NULL,
        case_id       TEXT NOT NULL,
        sequence      INTEGER NOT NULL,
        decision      TEXT NOT NULL,
        matcher_id    TEXT NOT NULL,
        rule_id       TEXT NOT NULL,
        settlement_ids TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        UNIQUE (batch_id, case_id, sequence),
        FOREIGN KEY (batch_id, case_id) REFERENCES cases (batch_id, case_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cases_status ON cases (batch_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_links_settlement ON case_links (batch_id, settlement_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log (batch_id, case_id)",
)
