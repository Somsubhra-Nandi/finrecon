"""The read-only investigation tool registry.

Four tools, all of them windows onto the immutable Stage-2 case snapshot
and nothing else. There is no database handle, no loader, no filesystem
path and no batch object anywhere in this module -- a tool physically cannot
write, because it is never given anything writable. The snapshot itself is
a frozen Pydantic model whose collections are tuples, so even the read side
has no ``append`` to reach for.

Why these four
--------------

DESIGN.md 3 sketches six tool names. Two of them would be redundant against
the Stage-2 structures that actually exist, and shipping a redundant tool is
not neutral: it spends step budget and invites the model to look busy.

* ``parse_bank_narration`` is absent. The narration and its Stage-2
  tokenization are already in the case briefing; a tool that re-emits them
  would burn a step to tell the model what it was told.
* ``inspect_refunds`` is absent. A settlement's refund offsets *are*
  ``refund`` lines in its break-up, carrying the referenced refund ID and
  that refund's status. ``inspect_settlement_breakup`` already returns them,
  and a second tool returning a filtered copy of the same rows would be a
  different name for the same fact.

What is deliberately *not* a tool
---------------------------------

There is no ``recover_correct_settlement(case) -> settlement_id``, and
adding one would defeat the entire stage. A tool that performs the
reconciliation and hands the model an answer has not built an investigation
agent; it has built a deterministic matcher with an expensive narrator
attached, and the experiment being run here -- does multi-step agency beat
single-shot extraction (DESIGN.md 5.5) -- would be measuring nothing.

So the split is: ``compare_reference_fragment`` will tell you, mechanically,
that ``PF*******VQ`` is mask-consistent with ``PF1CEIYFJVQ``. It will not
tell you that the candidate carrying that UTR is the right one, and it never
sees more than the one candidate it was asked about. Deciding requires the
complete candidate set, which only the validator holds.

Access control
--------------

Every candidate or settlement identifier is checked against the immutable
snapshot before a handler runs. A model cannot name a settlement that is not
in this case's candidate set and have the system investigate it as though it
were -- that would be adding a candidate through the back door, the mirror
image of the omission attack DESIGN.md 4.1 is built to prevent. An unknown
identifier is a validation failure: not executed, recorded, escalated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import ValidationError

from finrecon.agent.providers.base import ToolSpec
from finrecon.agent.schemas import (
    BreakupLineFacts,
    CompareReferenceFragmentInput,
    CompareReferenceFragmentOutput,
    ComputeExpectedNetInput,
    ComputeExpectedNetOutput,
    InspectSettlementBreakupInput,
    InspectSettlementBreakupOutput,
    LookupCandidateRecordsInput,
    LookupCandidateRecordsOutput,
    SettlementNetFacts,
    SettlementRecordFacts,
    ToolInput,
    ToolOutput,
)
from finrecon.candidates.snapshot import CandidateRecord, CaseSnapshot, SettlementFacts
from finrecon.evidence.reference import REFERENCE_KINDS, ReferenceComparison, compare

TOOL_LOOKUP_CANDIDATE_RECORDS = "lookup_candidate_records"
TOOL_INSPECT_SETTLEMENT_BREAKUP = "inspect_settlement_breakup"
TOOL_COMPUTE_EXPECTED_NET = "compute_expected_net"
TOOL_COMPARE_REFERENCE_FRAGMENT = "compare_reference_fragment"


class ToolValidationError(Exception):
    """A tool call that must not execute.

    Carries a machine-readable ``reason`` so the trajectory records *why* a
    call was refused, and the policy gate can distinguish a schema failure
    from an access-control violation without parsing prose.
    """

    UNKNOWN_TOOL = "unknown_tool"
    MALFORMED_ARGUMENTS_JSON = "malformed_arguments_json"
    DUPLICATE_ARGUMENT_KEY = "duplicate_argument_key"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    UNKNOWN_CANDIDATE = "unknown_candidate"
    UNKNOWN_SETTLEMENT = "unknown_settlement"

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ToolContext:
    """Everything a handler is allowed to see: one immutable snapshot.

    Note what is absent. No :class:`~finrecon.ledger.store.LedgerStore`, no
    :class:`~finrecon.normalize.records.NormalizedBatch`, no split name, no
    path. A handler cannot reach a record outside this case even by
    accident, and cannot write anywhere at all.
    """

    snapshot: CaseSnapshot

    def candidate(self, candidate_id: str) -> CandidateRecord:
        for candidate in self.snapshot.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise ToolValidationError(
            ToolValidationError.UNKNOWN_CANDIDATE,
            f"{candidate_id!r} is not a candidate of case {self.snapshot.case_id!r}; "
            f"known candidates: {list(self.snapshot.candidate_ids())}",
        )

    def settlement(self, settlement_id: str) -> SettlementFacts:
        for facts in self.snapshot.base_evidence.settlement_facts:
            if facts.settlement_id == settlement_id:
                return facts
        raise ToolValidationError(
            ToolValidationError.UNKNOWN_SETTLEMENT,
            f"{settlement_id!r} is not named by any candidate of case "
            f"{self.snapshot.case_id!r}",
        )

    def settlements_of(self, candidate: CandidateRecord) -> tuple[SettlementFacts, ...]:
        return tuple(self.settlement(sid) for sid in candidate.settlement_ids)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[ToolInput]
    output_model: type[ToolOutput]
    handler: Callable[[ToolContext, Any], ToolOutput]

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters_json_schema=self.input_model.model_json_schema(),
        )


# --- handlers -------------------------------------------------------------


def _lookup_candidate_records(
    context: ToolContext, args: LookupCandidateRecordsInput
) -> LookupCandidateRecordsOutput:
    candidate = context.candidate(args.candidate_id)
    settlements = tuple(
        SettlementRecordFacts(
            settlement_id=facts.settlement_id,
            utr=facts.utr,
            amount_paise=facts.amount_paise,
            created_at_utc=facts.created_at_utc,
            settlement_date_utc=facts.settlement_date_utc,
            breakup_line_count=len(facts.derivation.lines),
            breakup_total_paise=facts.derivation.breakup_total_paise,
            breakup_unexplained_delta_paise=facts.derivation.unexplained_delta_paise,
        )
        for facts in context.settlements_of(candidate)
    )
    return LookupCandidateRecordsOutput(
        candidate_id=candidate.candidate_id,
        settlement_ids=candidate.settlement_ids,
        total_paise=candidate.total_paise,
        blocking_rule=candidate.blocking_rule,
        settlements=settlements,
    )


def _inspect_settlement_breakup(
    context: ToolContext, args: InspectSettlementBreakupInput
) -> InspectSettlementBreakupOutput:
    facts = context.settlement(args.settlement_id)
    derivation = facts.derivation
    return InspectSettlementBreakupOutput(
        settlement_id=derivation.settlement_id,
        settlement_amount_paise=derivation.settlement_amount_paise,
        breakup_total_paise=derivation.breakup_total_paise,
        unexplained_delta_paise=derivation.unexplained_delta_paise,
        declared_adjustment_paise=derivation.declared_adjustment_paise,
        totals_by_line_type=derivation.breakup_by_type,
        lines=tuple(
            BreakupLineFacts(
                line_type=line.line_type,
                amount_paise=line.amount_paise,
                reference_id=line.reference_id,
                reference_status=line.reference_status,
            )
            for line in derivation.lines
        ),
    )


def _compute_expected_net(
    context: ToolContext, args: ComputeExpectedNetInput
) -> ComputeExpectedNetOutput:
    candidate = context.candidate(args.candidate_id)
    settlements = context.settlements_of(candidate)
    bank_amount = context.snapshot.base_evidence.bank_record.amount_paise
    group_total = sum(facts.amount_paise for facts in settlements)
    per_settlement = tuple(
        SettlementNetFacts(
            settlement_id=facts.settlement_id,
            settlement_amount_paise=facts.amount_paise,
            breakup_total_paise=facts.derivation.breakup_total_paise,
            unexplained_delta_paise=facts.derivation.unexplained_delta_paise,
        )
        for facts in settlements
    )
    return ComputeExpectedNetOutput(
        candidate_id=candidate.candidate_id,
        bank_amount_paise=bank_amount,
        settlement_group_total_paise=group_total,
        group_unexplained_delta_paise=bank_amount - group_total,
        group_total_is_exact=(bank_amount - group_total) == 0,
        every_breakup_is_exact=all(s.unexplained_delta_paise == 0 for s in per_settlement),
        per_settlement=per_settlement,
    )


def _compare_reference_fragment(
    context: ToolContext, args: CompareReferenceFragmentInput
) -> CompareReferenceFragmentOutput:
    candidate = context.candidate(args.candidate_id)
    narration = context.snapshot.base_evidence.bank_record.narration

    comparisons: list[ReferenceComparison] = []
    for facts in context.settlements_of(candidate):
        references: dict[str, str | None] = {
            "utr": facts.utr,
            "settlement_id": facts.settlement_id,
        }
        for kind in REFERENCE_KINDS:
            value = references[kind]
            if value is None:
                # A settlement with no UTR has nothing to compare against.
                # Reporting no entry is the fact; inventing an empty string
                # would create a comparison that could accidentally hold.
                continue
            comparisons.append(compare(args.fragment, value, kind))  # type: ignore[arg-type]

    return CompareReferenceFragmentOutput(
        candidate_id=candidate.candidate_id,
        fragment=args.fragment,
        fragment_present_in_narration=bool(args.fragment) and args.fragment in narration,
        fragment_offsets=_occurrences(narration, args.fragment),
        narration_length=len(narration),
        comparisons=tuple(comparisons),
    )


def _occurrences(haystack: str, needle: str) -> tuple[int, ...]:
    if not needle:
        return ()
    found: list[int] = []
    start = haystack.find(needle)
    while start != -1:
        found.append(start)
        start = haystack.find(needle, start + 1)
    return tuple(found)


# --- registry -------------------------------------------------------------

TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name=TOOL_LOOKUP_CANDIDATE_RECORDS,
        description=(
            "Return the settlement records behind one candidate: settlement IDs, "
            "the UTR each carries (or null), amounts in paise, dates, and "
            "break-up totals. Facts only."
        ),
        input_model=LookupCandidateRecordsInput,
        output_model=LookupCandidateRecordsOutput,
        handler=_lookup_candidate_records,
    ),
    ToolDefinition(
        name=TOOL_INSPECT_SETTLEMENT_BREAKUP,
        description=(
            "Return one settlement's break-up line by line -- payment, fee, tax, "
            "refund, transfer, adjustment -- with the record each line references, "
            "that record's status, and the exact paise totals."
        ),
        input_model=InspectSettlementBreakupInput,
        output_model=InspectSettlementBreakupOutput,
        handler=_inspect_settlement_breakup,
    ),
    ToolDefinition(
        name=TOOL_COMPUTE_EXPECTED_NET,
        description=(
            "Compare the bank credit against one candidate's settlement total in "
            "exact integer paise, and report each settlement's break-up residual. "
            "Arithmetic only; no tolerance is applied."
        ),
        input_model=ComputeExpectedNetInput,
        output_model=ComputeExpectedNetOutput,
        handler=_compute_expected_net,
    ),
    ToolDefinition(
        name=TOOL_COMPARE_REFERENCE_FRAGMENT,
        description=(
            "Mechanically compare a literal substring of the bank narration against "
            "one candidate's references. Reports, for every declared relation "
            "(exact, prefix, suffix, contains, mask-consistent, separator-normalized, "
            "character-multiset), whether it holds and how many reference characters "
            "it pins. It does not say whether the candidate is correct -- it only "
            "ever sees one candidate."
        ),
        input_model=CompareReferenceFragmentInput,
        output_model=CompareReferenceFragmentOutput,
        handler=_compare_reference_fragment,
    ),
)

TOOLS_BY_NAME: dict[str, ToolDefinition] = {t.name: t for t in TOOL_DEFINITIONS}


def tool_specs() -> tuple[ToolSpec, ...]:
    """The neutral tool specifications handed to a provider adapter."""
    return tuple(definition.spec() for definition in TOOL_DEFINITIONS)


class _DuplicateKeyError(Exception):
    """Internal signal from :func:`_reject_duplicate_keys`; never escapes this module."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that refuses a duplicate key at any nesting level.

    The stdlib decoder calls this once per JSON object it parses -- the top
    level and every nested one -- so raising here catches
    ``{"candidate_id":"A","candidate_id":"B"}`` whether it is the whole
    argument payload or buried inside one. Silently keeping the last value
    (the standard library's default behaviour) would let a model's tool call
    carry two different answers for one field and have Pydantic see only
    the one that happened to be written last -- unsafe ambiguity, not a
    valid call.
    """
    seen: set[str] = set()
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise _DuplicateKeyError(key)
        seen.add(key)
        result[key] = value
    return result


def validate_call(tool_name: str, raw_arguments: str) -> tuple[ToolDefinition, ToolInput]:
    """Parse and validate one requested call. Raises before anything executes."""
    definition = TOOLS_BY_NAME.get(tool_name)
    if definition is None:
        raise ToolValidationError(
            ToolValidationError.UNKNOWN_TOOL,
            f"{tool_name!r} is not a registered tool; known tools: {sorted(TOOLS_BY_NAME)}",
        )

    text = raw_arguments.strip() or "{}"
    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ToolValidationError(
            ToolValidationError.MALFORMED_ARGUMENTS_JSON,
            f"arguments for {tool_name!r} are not valid JSON ({exc.msg})",
        ) from None
    except _DuplicateKeyError as exc:
        raise ToolValidationError(
            ToolValidationError.DUPLICATE_ARGUMENT_KEY,
            f"arguments for {tool_name!r} contain duplicate key {exc.key!r}; "
            "a tool call with ambiguous object keys is never executed",
        ) from None
    if not isinstance(parsed, dict):
        raise ToolValidationError(
            ToolValidationError.MALFORMED_ARGUMENTS_JSON,
            f"arguments for {tool_name!r} decoded to {type(parsed).__name__}, not an object",
        )

    try:
        arguments = definition.input_model.model_validate(parsed)
    except ValidationError as exc:
        raise ToolValidationError(
            ToolValidationError.SCHEMA_VALIDATION_FAILED,
            f"arguments for {tool_name!r} failed schema validation: {exc.error_count()} error(s); "
            f"{[(e['loc'], e['type']) for e in exc.errors()]}",
        ) from None

    return definition, arguments


def execute(context: ToolContext, tool_name: str, raw_arguments: str) -> tuple[ToolInput, ToolOutput]:
    """Validate then run one tool call against the immutable snapshot.

    Access control happens inside the handlers' calls to
    :meth:`ToolContext.candidate` / :meth:`ToolContext.settlement`, which
    raise :class:`ToolValidationError` before any fact is read -- so an
    unknown identifier produces a refusal, never a partial result.
    """
    definition, arguments = validate_call(tool_name, raw_arguments)
    output = definition.handler(context, arguments)
    return arguments, output


__all__ = [
    "TOOLS_BY_NAME",
    "TOOL_COMPARE_REFERENCE_FRAGMENT",
    "TOOL_COMPUTE_EXPECTED_NET",
    "TOOL_DEFINITIONS",
    "TOOL_INSPECT_SETTLEMENT_BREAKUP",
    "TOOL_LOOKUP_CANDIDATE_RECORDS",
    "ToolContext",
    "ToolDefinition",
    "ToolValidationError",
    "execute",
    "tool_specs",
    "validate_call",
]
