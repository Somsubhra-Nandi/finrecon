"""SQLite schema for the Stage-2 ledger.

DESIGN.md §7 picks SQLite so a reviewer needs zero setup, and §3 gives the
store a narrow remit: "Resolutions, audit trail, idempotency" — never
metrics. The schema below is exactly that and nothing more. There is still
no human-resolution table; that is Stage 5, and adding it now would be
scaffolding for work that does not exist.

**Schema v2 added the four Stage-3 tables. Schema v3 adds distinct ingestion
audit and snapshot-bound human-resolution tables, and changes none of the Stage-2
ones.** That is deliberate. Stage-2 rows are the record of what the
deterministic core decided, and a later stage overwriting `cases.status`
would destroy the ability to say *which* layer settled a case — the exact
per-rule mechanism claim the benchmark v3 correction was about. So a
Stage-3 resolution is a new row in `stage3_decisions` and new links in
`stage3_links`, joined to the Stage-2 case by `(batch_id, case_id)`, and
the Stage-2 view of the batch remains readable as it was written.

The chain a reader can walk, entirely from keys:

    cases -> case_snapshots -> stage3_investigations -> stage3_tool_calls
                                                     -> stage3_decisions
                                                     -> stage3_links

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

SCHEMA_VERSION = 4

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
    # A durable read model for the product surface.  Unresolved cases already
    # carry these facts in their immutable snapshot; deterministically resolved
    # cases do not.  Keeping the normalized bank record and the complete set of
    # settlements considered by Stage 2 makes every case inspectable after a
    # process restart without changing a matcher or decision table.
    """
    CREATE TABLE IF NOT EXISTS case_contexts (
        batch_id     TEXT NOT NULL,
        case_id      TEXT NOT NULL,
        payload_json TEXT NOT NULL,
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
    # --- Stage 3 -------------------------------------------------------
    # One row per investigated case. `(batch_id, case_id)` again, so
    # re-investigating a case the ledger already holds is a no-op rather
    # than a second copy — the same idempotency mechanism Stage 2 uses, for
    # the same reason.
    """
    CREATE TABLE IF NOT EXISTS stage3_investigations (
        batch_id            TEXT NOT NULL,
        case_id             TEXT NOT NULL,
        snapshot_hash       TEXT NOT NULL,
        cache_key           TEXT NOT NULL,
        replayed            INTEGER NOT NULL CHECK (replayed IN (0, 1)),
        prompt_version      TEXT NOT NULL,
        tool_schema_version TEXT NOT NULL,
        agent_loop_version  TEXT NOT NULL,
        max_steps           INTEGER NOT NULL,
        step_count          INTEGER NOT NULL,
        providers_used      TEXT NOT NULL,
        models_used         TEXT NOT NULL,
        fallback_used       INTEGER NOT NULL CHECK (fallback_used IN (0, 1)),
        fallback_reasons    TEXT NOT NULL,
        termination_reason  TEXT NOT NULL,
        total_tokens        INTEGER,
        trajectory_json     TEXT NOT NULL,
        PRIMARY KEY (batch_id, case_id),
        FOREIGN KEY (batch_id, case_id) REFERENCES cases (batch_id, case_id)
    )
    """,
    # Every tool call, including the refused ones — a refusal is part of the
    # audit trail, not an absence in it. `output_json` is the RAW tool
    # output the validator consumed; no summary is stored in its place.
    """
    CREATE TABLE IF NOT EXISTS stage3_tool_calls (
        batch_id                 TEXT NOT NULL,
        case_id                  TEXT NOT NULL,
        step_index               INTEGER NOT NULL,
        call_index               INTEGER NOT NULL,
        tool_name                TEXT NOT NULL,
        validated_arguments_json TEXT,
        validation_error_reason  TEXT,
        output_json              TEXT,
        PRIMARY KEY (batch_id, case_id, step_index, call_index),
        FOREIGN KEY (batch_id, case_id)
            REFERENCES stage3_investigations (batch_id, case_id)
    )
    """,
    # The adjudication. `decision_hash` is a content hash of the whole
    # decision, so a rerun that produced a *different* decision under the
    # same key is detectable rather than silently absorbed by the upsert.
    """
    CREATE TABLE IF NOT EXISTS stage3_decisions (
        batch_id              TEXT NOT NULL,
        case_id               TEXT NOT NULL,
        decision_hash         TEXT NOT NULL,
        snapshot_hash         TEXT NOT NULL,
        cache_key             TEXT NOT NULL,
        outcome               TEXT NOT NULL CHECK (outcome IN ('RESOLVE', 'ESCALATE')),
        rule_id               TEXT NOT NULL,
        policy_version        TEXT NOT NULL,
        validator_version     TEXT NOT NULL,
        resolved_candidate_id TEXT,
        settlement_ids        TEXT NOT NULL,
        relationship          TEXT CHECK (relationship IN ('one_to_one', 'many_to_one')),
        blockers              TEXT NOT NULL,
        value_paise           INTEGER NOT NULL,
        validator_json        TEXT NOT NULL,
        policy_json           TEXT NOT NULL,
        PRIMARY KEY (batch_id, case_id),
        FOREIGN KEY (batch_id, case_id) REFERENCES cases (batch_id, case_id),
        -- A resolution names a candidate and a relationship; an escalation
        -- names neither. The database refuses the halfway state.
        CHECK ((outcome = 'RESOLVE') = (resolved_candidate_id IS NOT NULL)),
        CHECK ((outcome = 'RESOLVE') = (relationship IS NOT NULL))
    )
    """,
    # Stage-3 links live apart from `case_links` so the Stage-2 record of
    # what the deterministic core proved stays exactly as it was written.
    """
    CREATE TABLE IF NOT EXISTS stage3_links (
        batch_id      TEXT NOT NULL,
        case_id       TEXT NOT NULL,
        settlement_id TEXT NOT NULL,
        ordinal       INTEGER NOT NULL,
        PRIMARY KEY (batch_id, case_id, settlement_id),
        FOREIGN KEY (batch_id, case_id) REFERENCES stage3_decisions (batch_id, case_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cases_status ON cases (batch_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_links_settlement ON case_links (batch_id, settlement_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log (batch_id, case_id)",
    "CREATE INDEX IF NOT EXISTS idx_stage3_outcome ON stage3_decisions (batch_id, outcome)",
    "CREATE INDEX IF NOT EXISTS idx_stage3_links_settlement "
    "ON stage3_links (batch_id, settlement_id)",
    # Ingestion findings are deliberately a separate audit channel.  They
    # are never joined by the decision engine and cannot become evidence.
    """
    CREATE TABLE IF NOT EXISTS ingestion_audit_events (
        event_id     TEXT PRIMARY KEY,
        batch_id     TEXT NOT NULL,
        source_kind  TEXT NOT NULL CHECK (source_kind IN ('razorpay', 'bank')),
        source_id    TEXT NOT NULL,
        event_type   TEXT NOT NULL,
        subject_id   TEXT,
        fingerprint  TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        FOREIGN KEY (batch_id) REFERENCES batches (batch_id)
    )
    """,
    # This is an append-only decision journal.  `active` identifies the
    # current decision for one exact immutable snapshot; superseding an
    # action retires it rather than overwriting history.
    """
    CREATE TABLE IF NOT EXISTS human_resolution_events (
        resolution_id TEXT PRIMARY KEY,
        batch_id      TEXT NOT NULL,
        case_id       TEXT NOT NULL,
        bank_record_id TEXT NOT NULL,
        snapshot_hash TEXT NOT NULL,
        revision      INTEGER NOT NULL,
        resolution_type TEXT NOT NULL CHECK (resolution_type IN ('select_candidate', 'keep_escalated')),
        selected_candidate_id TEXT,
        reason        TEXT NOT NULL,
        actor         TEXT,
        recorded_at   TEXT NOT NULL,
        active        INTEGER NOT NULL CHECK (active IN (0, 1)),
        FOREIGN KEY (batch_id, case_id) REFERENCES cases (batch_id, case_id),
        CHECK ((resolution_type = 'select_candidate') = (selected_candidate_id IS NOT NULL)),
        UNIQUE (batch_id, case_id, snapshot_hash, revision)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_active_human_resolution
    ON human_resolution_events (batch_id, case_id, snapshot_hash) WHERE active = 1
    """,
    """
    CREATE TABLE IF NOT EXISTS human_resolution_links (
        resolution_id TEXT NOT NULL,
        settlement_id TEXT NOT NULL,
        ordinal       INTEGER NOT NULL,
        PRIMARY KEY (resolution_id, settlement_id),
        FOREIGN KEY (resolution_id) REFERENCES human_resolution_events (resolution_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ingestion_audit_batch ON ingestion_audit_events (batch_id, event_type)",
)
