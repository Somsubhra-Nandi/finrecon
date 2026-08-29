"""Read-only product projections over the existing FinRecon ledger."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException, status

from finrecon.candidates.snapshot import CaseSnapshot
from finrecon.ledger import HumanResolutionError, LedgerStore

from .schemas import (
    AgentStep,
    AgentTrajectoryView,
    AuditEvent,
    AuditResponse,
    CandidateView,
    CaseDetailResponse,
    CaseListResponse,
    CaseSummary,
    EvidenceSection,
    HumanResolutionView,
    IngestionIssue,
    IngestionIssuesResponse,
    OutcomeMetrics,
    OverviewResponse,
    ResolutionRequest,
    ResolutionResponse,
    RunSummary,
    TimelineEvent,
    ValidationView,
)


ISSUE_EVENT_TYPES = frozenset({"quarantined_settlement", "rejected_bank_row"})


def _loads(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _row_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def latest_batch_id(store: LedgerStore) -> str | None:
    row = store.connection.execute("SELECT batch_id FROM batches ORDER BY rowid DESC LIMIT 1").fetchone()
    return str(row["batch_id"]) if row else None


def resolve_batch_id(store: LedgerStore, batch_id: str | None) -> str | None:
    if batch_id is None:
        return latest_batch_id(store)
    row = store.connection.execute("SELECT 1 FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={
            "code": "batch_not_found", "message": f"Batch {batch_id!r} does not exist."
        })
    return batch_id


def _active_human_by_case(store: LedgerStore, batch_id: str) -> dict[str, Any]:
    rows = store.connection.execute(
        "SELECT * FROM human_resolution_events WHERE batch_id = ? AND active = 1 ORDER BY case_id",
        (batch_id,),
    )
    return {str(row["case_id"]): row for row in rows}


def _stage3_by_case(store: LedgerStore, batch_id: str) -> dict[str, Any]:
    return {str(row["case_id"]): row for row in store.stage3_decision_rows(batch_id)}


def _investigation_by_case(store: LedgerStore, batch_id: str) -> dict[str, Any]:
    return {str(row["case_id"]): row for row in store.investigation_rows(batch_id)}


def _case_source(case_row: Any, human: Any | None, stage3: Any | None) -> tuple[str, str]:
    if human is not None and human["resolution_type"] == "select_candidate":
        return "human", "resolved"
    if case_row["status"] == "resolved":
        return "deterministic", "resolved"
    if stage3 is not None and stage3["outcome"] == "RESOLVE":
        return "ai_assisted", "resolved"
    return "escalated", "needs_review"


def _evidence_state(source: str, stage3: Any | None) -> str:
    if source == "deterministic":
        return "Deterministic proof"
    if source == "ai_assisted":
        return "AI evidence validated"
    if source == "human":
        return "Human authority recorded"
    blockers = _loads(stage3["blockers"], []) if stage3 is not None else []
    return "Insufficient evidence" if not blockers else f"{len(blockers)} policy blocker(s)"


def _provider_calls(investigations: Iterable[Any]) -> int:
    calls = 0
    for row in investigations:
        if bool(row["replayed"]):
            continue
        trajectory = _loads(row["trajectory_json"], {})
        for step in trajectory.get("model_steps", []):
            attempts = step.get("attempts") or []
            calls += len(attempts) if attempts else 1
    return calls


def metrics_for_batch(store: LedgerStore, batch_id: str | None) -> OutcomeMetrics:
    if batch_id is None:
        return OutcomeMetrics(
            total_cases=0, deterministic_resolved=0, ai_assisted_resolved=0,
            human_resolved=0, needs_review=0, ingestion_issues=0,
            total_amount_paise=0, provider_calls=0, model_tokens=None,
        )
    cases = store.case_rows(batch_id)
    humans = _active_human_by_case(store, batch_id)
    stage3 = _stage3_by_case(store, batch_id)
    counts = {"deterministic": 0, "ai_assisted": 0, "human": 0, "escalated": 0}
    for row in cases:
        source, _ = _case_source(row, humans.get(row["case_id"]), stage3.get(row["case_id"]))
        counts[source] += 1
    investigations = store.investigation_rows(batch_id)
    tokens = [int(row["total_tokens"]) for row in investigations if row["total_tokens"] is not None]
    issue_count = sum(
        1 for row in store.ingestion_audit_rows(batch_id) if row["event_type"] in ISSUE_EVENT_TYPES
    )
    return OutcomeMetrics(
        total_cases=len(cases),
        deterministic_resolved=counts["deterministic"],
        ai_assisted_resolved=counts["ai_assisted"],
        human_resolved=counts["human"],
        needs_review=counts["escalated"],
        ingestion_issues=issue_count,
        total_amount_paise=sum(int(row["amount_paise"]) for row in cases),
        provider_calls=_provider_calls(investigations),
        model_tokens=sum(tokens) if tokens else None,
    )


def run_summaries(store: LedgerStore) -> list[RunSummary]:
    rows = store.connection.execute("SELECT * FROM batches ORDER BY rowid DESC")
    return [
        RunSummary(
            batch_id=row["batch_id"], split=row["split"],
            content_fingerprint=row["content_fingerprint"], record_count=int(row["record_count"]),
            metrics=metrics_for_batch(store, row["batch_id"]),
        )
        for row in rows
    ]


def overview(store: LedgerStore, batch_id: str | None = None) -> OverviewResponse:
    resolved = resolve_batch_id(store, batch_id)
    return OverviewResponse(
        selected_batch_id=resolved,
        metrics=metrics_for_batch(store, resolved),
        recent_runs=run_summaries(store)[:8],
    )


def _case_summaries(store: LedgerStore, batch_id: str) -> list[CaseSummary]:
    humans = _active_human_by_case(store, batch_id)
    stage3 = _stage3_by_case(store, batch_id)
    candidate_counts = {
        row["case_id"]: int(row["n"])
        for row in store.connection.execute(
            "SELECT case_id, COUNT(*) AS n FROM case_candidates WHERE batch_id = ? GROUP BY case_id",
            (batch_id,),
        )
    }
    summaries: list[CaseSummary] = []
    for row in store.case_rows(batch_id):
        context = store.case_context_payload(batch_id, row["case_id"]) or {}
        bank = context.get("bank_record") or {}
        human = humans.get(row["case_id"])
        stage = stage3.get(row["case_id"])
        source, case_status = _case_source(row, human, stage)
        summaries.append(CaseSummary(
            batch_id=batch_id,
            case_id=row["case_id"],
            bank_record_id=row["bank_record_id"],
            narration=bank.get("narration"),
            amount_paise=int(row["amount_paise"]),
            status=case_status,
            resolution_source=source,
            candidate_count=candidate_counts.get(row["case_id"], 0),
            evidence_state=_evidence_state(source, stage),
            last_updated=human["recorded_at"] if human is not None else None,
        ))
    return summaries


def list_cases(
    store: LedgerStore, *, batch_id: str | None = None, search: str | None = None,
    status_filter: str | None = None, source_filter: str | None = None,
    escalated_only: bool = False,
) -> CaseListResponse:
    resolved = resolve_batch_id(store, batch_id)
    if resolved is None:
        return CaseListResponse(batch_id=None, total=0, cases=[])
    cases = _case_summaries(store, resolved)
    if search:
        needle = search.casefold()
        cases = [case for case in cases if needle in " ".join(filter(None, [
            case.case_id, case.bank_record_id, case.narration or ""
        ])).casefold()]
    if status_filter:
        cases = [case for case in cases if case.status == status_filter]
    if source_filter:
        cases = [case for case in cases if case.resolution_source == source_filter]
    if escalated_only:
        cases = [case for case in cases if case.resolution_source == "escalated"]
    return CaseListResponse(batch_id=resolved, total=len(cases), cases=cases)


def _candidate_views(
    store: LedgerStore, batch_id: str, case_id: str, snapshot: dict[str, Any] | None,
    accepted_candidate_id: str | None,
) -> list[CandidateView]:
    facts = {}
    if snapshot:
        facts = {
            item["settlement_id"]: item
            for item in snapshot.get("base_evidence", {}).get("settlement_facts", [])
        }
    views = []
    for row in store.candidate_rows(batch_id, case_id):
        settlement_ids = _loads(row["settlement_ids"], [])
        candidate_snapshot = next((item for item in (snapshot or {}).get("candidates", [])
                                   if item.get("candidate_id") == row["candidate_id"]), {})
        state = "available" if accepted_candidate_id is None else (
            "accepted" if row["candidate_id"] == accepted_candidate_id else "rejected"
        )
        views.append(CandidateView(
            candidate_id=row["candidate_id"], settlement_ids=settlement_ids,
            total_paise=int(row["total_paise"]),
            unexplained_delta_paise=int(row["unexplained_delta_paise"]),
            blocking_rule=row["blocking_rule"],
            settlement_dates=[str(value) for value in candidate_snapshot.get("settlement_dates", [])],
            state=state,
            settlements=[facts[sid] for sid in settlement_ids if sid in facts],
        ))
    return views


def _human_views(rows: Iterable[Any]) -> list[HumanResolutionView]:
    return [HumanResolutionView(**{
        key: bool(row[key]) if key == "active" else row[key]
        for key in ("resolution_id", "revision", "resolution_type", "selected_candidate_id",
                    "reason", "actor", "recorded_at", "active")
    }) for row in rows]


def _trajectory_view(store: LedgerStore, batch_id: str, case_id: str) -> AgentTrajectoryView:
    row = _investigation_by_case(store, batch_id).get(case_id)
    if row is None:
        return AgentTrajectoryView(available=False)
    trajectory = _loads(row["trajectory_json"], {})
    tool_rows = store.stage3_tool_call_rows(batch_id, case_id)
    return AgentTrajectoryView(
        available=True,
        replayed=bool(row["replayed"]),
        provider=_loads(row["providers_used"], []),
        models=_loads(row["models_used"], []),
        termination_reason=row["termination_reason"],
        step_count=int(row["step_count"]),
        total_tokens=row["total_tokens"],
        assistant_notes=[step.get("assistant_text", "") for step in trajectory.get("model_steps", [])
                         if step.get("assistant_text")],
        tools=[AgentStep(
            step_index=int(tool["step_index"]), tool_name=tool["tool_name"],
            status="refused" if tool["validation_error_reason"] else "completed",
            arguments=_loads(tool["validated_arguments_json"], None),
            validation_error=tool["validation_error_reason"],
            output=_loads(tool["output_json"], None),
        ) for tool in tool_rows],
    )


def _validation_view(case_row: Any, stage3: Any | None, context: dict[str, Any]) -> ValidationView:
    if stage3 is None:
        evidence = context.get("decision", {}).get("evidence", {})
        passed = []
        if evidence.get("references"):
            passed.append("Exact reference equality")
        money = evidence.get("money")
        if money and money.get("unexplained_delta_paise") == 0:
            passed.append("Exact-paise accounting")
        if evidence.get("date_window"):
            passed.append("Declared value-date window")
        if case_row["status"] == "resolved":
            passed.append("Unique deterministic candidate")
        return ValidationView(
            validator_version=None, policy_version=None,
            outcome="RESOLVE" if case_row["status"] == "resolved" else "UNRESOLVED",
            rule_id=case_row["rule_id"], passed=passed, failed=[], blockers=[],
            resolved_candidate_id=None, raw_validator=None, policy_declaration=None,
        )
    validator = _loads(stage3["validator_json"], {})
    blockers = _loads(stage3["blockers"], [])
    passed = []
    if validator.get("snapshot_integrity_ok"):
        passed.append("Immutable snapshot integrity")
    if validator.get("admissible_fragments"):
        passed.append("Narration evidence was source-bound")
    if validator.get("financially_exact_candidate_ids"):
        passed.append("Exact-paise accounting")
    if validator.get("surviving_candidate_ids"):
        passed.append("Deterministic candidate survived validation")
    failed = [str(item.get("reason")) for item in validator.get("inadmissible_fragments", [])]
    failed.extend(str(item) for item in blockers)
    return ValidationView(
        validator_version=stage3["validator_version"], policy_version=stage3["policy_version"],
        outcome=stage3["outcome"], rule_id=stage3["rule_id"], passed=passed,
        failed=list(dict.fromkeys(failed)), blockers=blockers,
        resolved_candidate_id=stage3["resolved_candidate_id"], raw_validator=validator,
        policy_declaration=_loads(stage3["policy_json"], {}),
    )


def case_detail(store: LedgerStore, case_id: str, *, batch_id: str | None = None) -> CaseDetailResponse:
    resolved = resolve_batch_id(store, batch_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail={"code": "case_not_found", "message": "No cases exist yet."})
    row = store.connection.execute(
        "SELECT * FROM cases WHERE batch_id = ? AND case_id = ?", (resolved, case_id)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={
            "code": "case_not_found", "message": f"Case {case_id!r} does not exist in {resolved!r}."
        })
    summary = next(case for case in _case_summaries(store, resolved) if case.case_id == case_id)
    context = store.case_context_payload(resolved, case_id) or {}
    snapshot = store.snapshot_payload(resolved, case_id)
    human_rows = store.human_resolution_rows(resolved, case_id)
    active_human = next((item for item in human_rows if bool(item["active"])), None)
    stage3 = _stage3_by_case(store, resolved).get(case_id)
    accepted_id = active_human["selected_candidate_id"] if active_human is not None and active_human["resolution_type"] == "select_candidate" else (
        stage3["resolved_candidate_id"] if stage3 is not None and stage3["outcome"] == "RESOLVE" else None
    )
    bank = (snapshot or {}).get("base_evidence", {}).get("bank_record") or context.get("bank_record") or {}
    deterministic = context.get("decision", {}).get("evidence", {})
    tool_outputs = [
        {"tool_name": item["tool_name"], "arguments": _loads(item["validated_arguments_json"], None),
         "output": _loads(item["output_json"], None)}
        for item in store.stage3_tool_call_rows(resolved, case_id)
        if item["output_json"] is not None
    ]
    validation = _validation_view(row, stage3, context)
    timeline = [TimelineEvent(sequence=1, kind="ingestion", title="Source records accepted",
                              detail="Canonical source facts were persisted separately from reconciliation findings.")]
    timeline.append(TimelineEvent(sequence=2, kind="deterministic", title="Deterministic pass",
                                  detail=f"{row['matcher_id']} recorded {row['rule_id']}: {row['status']}."))
    sequence = 3
    if stage3 is not None:
        timeline.append(TimelineEvent(sequence=sequence, kind="investigation", title="Bounded AI investigation",
                                      detail="Read-only tools gathered evidence from the complete immutable candidate snapshot."))
        sequence += 1
        timeline.append(TimelineEvent(sequence=sequence, kind="validation", title="Deterministic validation and policy",
                                      detail=f"{stage3['validator_version']} + {stage3['policy_version']} → {stage3['outcome']}"))
        sequence += 1
    for human in human_rows:
        timeline.append(TimelineEvent(sequence=sequence, kind="human", title=f"Human review revision {human['revision']}",
                                      detail=human["reason"], recorded_at=human["recorded_at"]))
        sequence += 1
    return CaseDetailResponse(
        summary=summary,
        snapshot_hash=(snapshot or {}).get("content_hash"),
        bank_transaction=bank,
        candidates=_candidate_views(store, resolved, case_id, snapshot, accepted_id),
        evidence=EvidenceSection(
            deterministic=deterministic,
            ai_found=tool_outputs,
            structured_bank_facts={key: value for key, value in bank.items() if key != "narration"},
            raw_narration=bank.get("narration"),
        ),
        validation=validation,
        trajectory=_trajectory_view(store, resolved, case_id),
        audit_timeline=timeline,
        human_resolutions=_human_views(human_rows),
        can_resolve=summary.resolution_source == "escalated" and snapshot is not None,
    )


def resolve_case(store: LedgerStore, case_id: str, request: ResolutionRequest) -> ResolutionResponse:
    snapshot_payload = store.snapshot_payload(request.batch_id, case_id)
    if snapshot_payload is None:
        raise HTTPException(status_code=409, detail={
            "code": "resolution_not_allowed", "message": "Only snapshot-backed reconciliation exceptions can be reviewed."
        })
    if snapshot_payload.get("content_hash") != request.snapshot_hash:
        raise HTTPException(status_code=409, detail={
            "code": "stale_snapshot", "message": "This case changed after it was opened. Refresh before saving a resolution."
        })
    # Snapshot models are strict and tuple-backed; JSON mode is the same
    # lossless wire path used by trajectory replay.
    snapshot = CaseSnapshot.model_validate_json(json.dumps(snapshot_payload))
    try:
        resolution = store.record_human_resolution(
            snapshot, selected_candidate_id=request.selected_candidate_id,
            reason=request.reason, actor=request.actor,
        )
    except HumanResolutionError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "invalid_human_resolution", "message": str(exc)
        }) from exc
    store.connection.commit()
    return ResolutionResponse(
        resolution=_human_views([{
            "resolution_id": resolution.resolution_id, "revision": resolution.revision,
            "resolution_type": resolution.resolution_type,
            "selected_candidate_id": resolution.selected_candidate_id,
            "reason": resolution.reason, "actor": resolution.actor,
            "recorded_at": resolution.recorded_at, "active": resolution.active,
        }])[0],
        case=case_detail(store, case_id, batch_id=request.batch_id),
    )


def ingestion_issues(store: LedgerStore, batch_id: str | None = None) -> IngestionIssuesResponse:
    resolved = resolve_batch_id(store, batch_id)
    if resolved is None:
        return IngestionIssuesResponse(batch_id=None, total=0, issues=[])
    issues = []
    for row in store.ingestion_audit_rows(resolved):
        if row["event_type"] not in ISSUE_EVENT_TYPES:
            continue
        payload = _loads(row["payload_json"], {})
        conflict_kinds = [item.get("kind") for item in payload.get("conflicts", [])]
        problem = payload.get("reason") or ", ".join(filter(None, conflict_kinds)) or row["event_type"]
        issues.append(IngestionIssue(
            event_id=row["event_id"], batch_id=resolved, source_kind=row["source_kind"],
            source_id=row["source_id"], event_type=row["event_type"], subject_id=row["subject_id"],
            fingerprint=row["fingerprint"], problem=problem,
            detail=payload.get("detail"), payload=payload,
        ))
    return IngestionIssuesResponse(batch_id=resolved, total=len(issues), issues=issues)


def audit_events(store: LedgerStore, batch_id: str | None = None, case_id: str | None = None) -> AuditResponse:
    resolved = resolve_batch_id(store, batch_id)
    if resolved is None:
        return AuditResponse(batch_id=None, events=[])
    events: list[AuditEvent] = []
    for row in store.audit_rows(resolved):
        if case_id and row["case_id"] != case_id:
            continue
        events.append(AuditEvent(channel="reconciliation", batch_id=resolved, case_id=row["case_id"],
                                 event_type=row["decision"], payload={
                                     "matcher_id": row["matcher_id"], "rule_id": row["rule_id"],
                                     "settlement_ids": _loads(row["settlement_ids"], []),
                                     "evidence": _loads(row["evidence_json"], {}),
                                 }))
    if case_id is None:
        for row in store.ingestion_audit_rows(resolved):
            events.append(AuditEvent(channel="ingestion", batch_id=resolved, case_id=None,
                                     event_type=row["event_type"], payload=_loads(row["payload_json"], {})))
    for row in store.human_resolution_rows(resolved, case_id):
        events.append(AuditEvent(channel="human", batch_id=resolved, case_id=row["case_id"],
                                 event_type=row["resolution_type"], payload=_row_dict(row)))
    return AuditResponse(batch_id=resolved, events=events)
