"""Stage-2 Phase E: SQLite ledger and the structured audit trail."""

from __future__ import annotations

import json
import sqlite3

import pytest

from finrecon.ledger.audit import audit_id_for, audit_payload, audit_row
from finrecon.ledger.store import BatchIdentityError, LedgerStore
from finrecon.matchers.derived_reconciliation import match_derived
from finrecon.matchers.direct_key_matcher import DirectKeyIndex, match_direct_key
from finrecon.matchers.result import DecisionStatus
from finrecon.matchers.rules import (
    RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING,
    RULE_DIRECT_KEY_EXACT_TOKEN,
)
from tests import stage2_factories as f
from tests.test_candidates_and_snapshot import ambiguous_batch, snapshot_of

UTR = "AX1B2C3D4E5F"


@pytest.fixture
def store():
    with LedgerStore(":memory:") as ledger:
        ledger.register_batch(
            batch_id="batch:test",
            split="test",
            content_fingerprint="fingerprint",
            record_count=4,
            case_count=1,
        )
        yield ledger


def resolved_decision():
    batch = f.batch_of(
        orders=[f.order()],
        payments=[f.payment()],
        settlements=[f.settlement(utr=UTR)],
        bank_records=[f.bank(narration=f"NEFT-CR-{UTR}")],
    )
    record = batch.bank_records[0]
    return match_direct_key(record, batch, DirectKeyIndex(batch.settlements), "case:bnk_1"), batch


class TestDecisionPersistence:
    def test_a_resolved_decision_and_its_links_are_recorded(self, store):
        decision, _ = resolved_decision()
        store.record_decision("batch:test", decision, 97_758)

        row = store.case_rows("batch:test")[0]
        assert row["status"] == "resolved"
        assert row["rule_id"] == RULE_DIRECT_KEY_EXACT_TOKEN
        assert row["relationship"] == "one_to_one"
        assert row["amount_paise"] == 97_758

        links = store.link_rows("batch:test")
        assert [link["settlement_id"] for link in links] == ["setl_1"]

    def test_an_unresolved_case_is_recorded_with_no_links(self, store):
        batch = ambiguous_batch()
        decision = match_derived(
            batch.bank_records[0], batch, batch.settlements, "case:bnk_1"
        )
        store.record_decision("batch:test", decision, 97_758)

        row = store.case_rows("batch:test")[0]
        assert row["status"] == "unresolved"
        assert row["relationship"] is None
        assert store.link_rows("batch:test") == []

    def test_every_settlement_of_a_group_gets_its_own_link_row(self, store):
        decision, _ = resolved_decision()
        grouped = decision.model_copy(
            update={"settlement_ids": ("setl_a", "setl_b"), "relationship": "many_to_one"}
        )
        store.record_decision("batch:test", grouped, 80_000)
        links = store.link_rows("batch:test")
        assert [link["settlement_id"] for link in links] == ["setl_a", "setl_b"]
        assert [link["ordinal"] for link in links] == [0, 1]

    def test_a_status_relationship_mismatch_is_refused_by_the_database(self, store):
        with pytest.raises(sqlite3.IntegrityError):
            store.connection.execute(
                "INSERT INTO cases (batch_id, case_id, bank_record_id, status, matcher_id, "
                "rule_id, relationship, amount_paise) VALUES "
                "('batch:test', 'case:x', 'bnk_x', 'resolved', 'm', 'r', NULL, 1)"
            )

    def test_an_unknown_status_is_refused_by_the_database(self, store):
        with pytest.raises(sqlite3.IntegrityError):
            store.connection.execute(
                "INSERT INTO cases (batch_id, case_id, bank_record_id, status, matcher_id, "
                "rule_id, relationship, amount_paise) VALUES "
                "('batch:test', 'case:y', 'bnk_y', 'maybe', 'm', 'r', NULL, 1)"
            )


class TestCandidateAndSnapshotPersistence:
    def test_candidates_are_recorded_in_deterministic_order(self, store):
        snapshot, decision, candidates = snapshot_of(ambiguous_batch())
        store.record_decision("batch:test", decision, 97_758)
        store.record_candidates("batch:test", decision.case_id, candidates)

        rows = store.candidate_rows("batch:test", decision.case_id)
        assert [json.loads(row["settlement_ids"]) for row in rows] == [["setl_a"], ["setl_b"]]
        assert [row["ordinal"] for row in rows] == [0, 1]
        assert {row["unexplained_delta_paise"] for row in rows} == {0}

    def test_snapshot_payload_round_trips_with_its_hash(self, store):
        snapshot, decision, candidates = snapshot_of(ambiguous_batch())
        store.record_decision("batch:test", decision, 97_758)
        store.record_snapshot(snapshot.model_copy(update={"batch_id": "batch:test"}))

        payload = store.snapshot_payload("batch:test", decision.case_id)
        assert len(payload["candidates"]) == 2
        assert payload["base_evidence"]["bank_record"]["narration"] == "NEFT CREDIT - SETTLEMENT"


class TestAuditTrail:
    def test_a_resolution_records_rule_matcher_and_matched_ids(self, store):
        decision, _ = resolved_decision()
        store.record_decision("batch:test", decision, 97_758)
        store.record_audit("batch:test", decision, 0)

        row = store.audit_rows("batch:test")[0]
        assert row["decision"] == "resolved"
        assert row["rule_id"] == RULE_DIRECT_KEY_EXACT_TOKEN
        assert row["matcher_id"] == "direct_key.v1"
        assert json.loads(row["settlement_ids"]) == ["setl_1"]

    def test_the_audit_body_carries_the_exact_paise_derivation(self, store):
        decision, _ = resolved_decision()
        store.record_decision("batch:test", decision, 97_758)
        store.record_audit("batch:test", decision, 0)

        evidence = json.loads(store.audit_rows("batch:test")[0]["evidence_json"])
        money = evidence["money"]
        assert money["unexplained_delta_paise"] == 0
        assert money["per_settlement"][0]["breakup_by_type"] == [
            ["fee", -1_900],
            ["payment", 100_000],
            ["tax", -342],
        ]

    def test_the_audit_body_records_the_reference_token_that_matched(self):
        decision, _ = resolved_decision()
        payload = audit_payload(decision)
        assert payload["evidence"]["references"][0]["matched_token"] == UTR

    def test_a_refusal_is_audited_as_carefully_as_a_resolution(self, store):
        batch = ambiguous_batch()
        decision = match_derived(batch.bank_records[0], batch, batch.settlements, "case:bnk_1")
        store.record_decision("batch:test", decision, 97_758)
        store.record_audit("batch:test", decision, 0)

        row = store.audit_rows("batch:test")[0]
        assert row["decision"] == "unresolved"
        evidence = json.loads(row["evidence_json"])
        assert evidence["competing_solution_ids"] == [["setl_a"], ["setl_b"]]
        assert evidence["considered_settlement_ids"] == ["setl_a", "setl_b"]

    def test_audit_records_contain_no_generated_prose(self, store):
        decision, _ = resolved_decision()
        payload = audit_payload(decision)
        flat = json.dumps(payload)
        # Every string in the body is an identifier, enum value or rule ID —
        # nothing sentence-shaped, which would be a narrative about evidence.
        assert ". " not in flat
        assert "because" not in flat.lower()

    def test_audit_ids_are_content_derived_and_stable(self):
        decision, _ = resolved_decision()
        assert audit_id_for("batch:test", decision, 0) == audit_id_for("batch:test", decision, 0)
        assert audit_id_for("batch:test", decision, 0) != audit_id_for("batch:test", decision, 1)
        assert audit_id_for("batch:other", decision, 0) != audit_id_for("batch:test", decision, 0)

    def test_audit_id_changes_when_the_decision_changes(self):
        decision, _ = resolved_decision()
        altered = decision.model_copy(update={"rule_id": "something.else"})
        assert audit_id_for("b", decision, 0) != audit_id_for("b", altered, 0)

    def test_audit_row_is_json_serializable_end_to_end(self):
        decision, _ = resolved_decision()
        row = audit_row("batch:test", decision, 3)
        assert json.loads(row["evidence_json"])["money"]["bank_amount_paise"] == 97_758
        assert row["sequence"] == 3


class TestBatchIdentity:
    def test_reregistering_the_same_batch_is_a_no_op(self, store):
        store.register_batch(
            batch_id="batch:test",
            split="test",
            content_fingerprint="fingerprint",
            record_count=4,
            case_count=1,
        )
        assert store.count("batches") == 1

    def test_reusing_a_batch_id_for_different_content_fails_loudly(self, store):
        with pytest.raises(BatchIdentityError):
            store.register_batch(
                batch_id="batch:test",
                split="test",
                content_fingerprint="different",
                record_count=4,
                case_count=1,
            )


class TestNoDuplicateLogicalRows:
    def test_replaying_the_same_writes_adds_nothing(self, store):
        snapshot, decision, candidates = snapshot_of(ambiguous_batch())
        snapshot = snapshot.model_copy(update={"batch_id": "batch:test"})

        for _ in range(3):
            store.record_decision("batch:test", decision, 97_758)
            store.record_candidates("batch:test", decision.case_id, candidates)
            store.record_snapshot(snapshot)
            store.record_audit("batch:test", decision, 0)

        assert store.count("cases") == 1
        assert store.count("case_candidates") == 2
        assert store.count("case_snapshots") == 1
        assert store.count("audit_log") == 1

    def test_duplicate_links_are_refused_by_the_primary_key(self, store):
        decision, _ = resolved_decision()
        store.record_decision("batch:test", decision, 97_758)
        store.record_decision("batch:test", decision, 97_758)
        assert store.count("case_links") == 1

    def test_the_store_computes_no_metrics(self, store):
        assert not [
            name
            for name in dir(store)
            if any(word in name for word in ("precision", "recall", "accuracy", "match_rate"))
        ]


class TestDerivedRuleIsRecorded:
    def test_derived_resolutions_name_the_derived_rule(self, store):
        batch = f.simple_batch()
        decision = match_derived(batch.bank_records[0], batch, batch.settlements, "case:bnk_1")
        assert decision.status is DecisionStatus.RESOLVED
        store.record_decision("batch:test", decision, 97_758)
        assert store.case_rows("batch:test")[0]["rule_id"] == (
            RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING
        )
