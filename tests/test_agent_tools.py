"""The read-only tools: schemas, access control, and the absence of authority.

Three groups of claims are tested here, in rising order of importance.

1. The schemas are real -- a tool call is validated before it executes, and
   a malformed one is refused rather than repaired.
2. The access control is real -- a candidate or settlement outside the
   immutable snapshot cannot be investigated, so a model cannot smuggle a
   counterparty into the case through a tool argument.
3. The tools have no authority -- they never mutate anything, never write,
   never reach ground truth, and never return a conclusion.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pydantic
import pytest

from finrecon.agent import tools
from finrecon.agent.schemas import (
    CompareReferenceFragmentOutput,
    ComputeExpectedNetOutput,
    InspectSettlementBreakupOutput,
    LookupCandidateRecordsOutput,
)
from finrecon.agent.tools import ToolContext, ToolValidationError
from tests.stage3_factories import (
    MASKED_NARRATION,
    TRUE_SETTLEMENT_ID,
    TRUE_UTR,
    no_reference_snapshot,
    settlement_facts,
    snapshot_of,
    two_candidate_snapshot,
)


@pytest.fixture
def snapshot():
    return two_candidate_snapshot()


@pytest.fixture
def context(snapshot):
    return ToolContext(snapshot=snapshot)


class TestRegistry:
    def test_the_registry_is_small_and_explicit(self):
        assert set(tools.TOOLS_BY_NAME) == {
            "lookup_candidate_records",
            "inspect_settlement_breakup",
            "compute_expected_net",
            "compare_reference_fragment",
        }

    def test_no_tool_performs_the_reconciliation_itself(self):
        """A tool named like this would hand the model the answer (DESIGN.md §4.1)."""
        for forbidden in ("recover", "resolve", "match", "choose", "decide", "rank", "score"):
            assert not any(forbidden in name for name in tools.TOOLS_BY_NAME), forbidden

    def test_every_tool_exposes_a_json_schema_for_its_arguments(self):
        for spec in tools.tool_specs():
            schema = spec.parameters_json_schema
            assert schema["type"] == "object"
            assert schema["additionalProperties"] is False, spec.name

    def test_tool_descriptions_do_not_promise_a_verdict(self):
        for definition in tools.TOOL_DEFINITIONS:
            lowered = definition.description.lower()
            assert "correct candidate" not in lowered
            assert "which candidate is right" not in lowered


class TestArgumentValidation:
    def test_an_unknown_tool_is_refused(self, context):
        with pytest.raises(ToolValidationError) as exc:
            tools.execute(context, "recover_correct_settlement", "{}")
        assert exc.value.reason == ToolValidationError.UNKNOWN_TOOL

    def test_arguments_that_are_not_json_are_refused(self, context):
        with pytest.raises(ToolValidationError) as exc:
            tools.execute(context, "compute_expected_net", "{candidate_id: oops")
        assert exc.value.reason == ToolValidationError.MALFORMED_ARGUMENTS_JSON

    def test_arguments_that_are_not_an_object_are_refused(self, context):
        with pytest.raises(ToolValidationError) as exc:
            tools.execute(context, "compute_expected_net", '["a"]')
        assert exc.value.reason == ToolValidationError.MALFORMED_ARGUMENTS_JSON

    def test_a_missing_required_field_is_refused(self, context):
        with pytest.raises(ToolValidationError) as exc:
            tools.execute(context, "compare_reference_fragment", '{"fragment": "PF"}')
        assert exc.value.reason == ToolValidationError.SCHEMA_VALIDATION_FAILED

    def test_a_wrongly_typed_field_is_refused(self, context, snapshot):
        with pytest.raises(ToolValidationError) as exc:
            tools.execute(
                context, "compute_expected_net", json.dumps({"candidate_id": 7})
            )
        assert exc.value.reason == ToolValidationError.SCHEMA_VALIDATION_FAILED

    def test_an_extra_field_is_refused(self, context, snapshot):
        payload = json.dumps(
            {"candidate_id": snapshot.candidate_ids()[0], "force_resolve": True}
        )
        with pytest.raises(ToolValidationError) as exc:
            tools.execute(context, "compute_expected_net", payload)
        assert exc.value.reason == ToolValidationError.SCHEMA_VALIDATION_FAILED

    def test_a_refused_call_produces_no_output_at_all(self, context):
        """Fail safe means no partial result, not a result with a warning."""
        with pytest.raises(ToolValidationError):
            tools.execute(context, "compute_expected_net", '{"candidate_id": "nope"}')


class TestCandidateAccessControl:
    def test_a_candidate_outside_the_snapshot_is_refused(self, context):
        with pytest.raises(ToolValidationError) as exc:
            tools.execute(
                context, "lookup_candidate_records", '{"candidate_id": "bnk_x|setl_smuggled"}'
            )
        assert exc.value.reason == ToolValidationError.UNKNOWN_CANDIDATE

    def test_a_settlement_outside_the_snapshot_is_refused(self, context):
        with pytest.raises(ToolValidationError) as exc:
            tools.execute(
                context, "inspect_settlement_breakup", '{"settlement_id": "setl_elsewhere"}'
            )
        assert exc.value.reason == ToolValidationError.UNKNOWN_SETTLEMENT

    def test_a_real_settlement_from_another_case_is_still_refused(self, context):
        """Existing somewhere is not the same as being a candidate here."""
        other = no_reference_snapshot()
        outsider = other.base_evidence.settlement_facts[0].settlement_id
        with pytest.raises(ToolValidationError) as exc:
            tools.execute(
                context,
                "inspect_settlement_breakup",
                json.dumps({"settlement_id": outsider}),
            )
        assert exc.value.reason == ToolValidationError.UNKNOWN_SETTLEMENT

    def test_comparing_against_an_unknown_candidate_is_refused(self, context):
        with pytest.raises(ToolValidationError) as exc:
            tools.execute(
                context,
                "compare_reference_fragment",
                json.dumps({"candidate_id": "invented", "fragment": "PF*******VQ"}),
            )
        assert exc.value.reason == ToolValidationError.UNKNOWN_CANDIDATE

    def test_the_error_names_the_candidates_that_do_exist(self, context, snapshot):
        with pytest.raises(ToolValidationError) as exc:
            tools.execute(context, "compute_expected_net", '{"candidate_id": "nope"}')
        for candidate_id in snapshot.candidate_ids():
            assert candidate_id in exc.value.detail


class TestOutputsAreFacts:
    def test_lookup_returns_the_settlements_of_that_candidate_only(self, context, snapshot):
        target = snapshot.candidate_ids()[1]
        _, output = tools.execute(
            context, "lookup_candidate_records", json.dumps({"candidate_id": target})
        )
        assert isinstance(output, LookupCandidateRecordsOutput)
        assert output.settlement_ids == (TRUE_SETTLEMENT_ID,)
        assert output.settlements[0].utr == TRUE_UTR

    def test_breakup_inspection_reports_exact_paise_arithmetic(self, context):
        _, output = tools.execute(
            context,
            "inspect_settlement_breakup",
            json.dumps({"settlement_id": TRUE_SETTLEMENT_ID}),
        )
        assert isinstance(output, InspectSettlementBreakupOutput)
        assert output.unexplained_delta_paise == 0
        assert {line.line_type for line in output.lines} == {"payment", "fee", "tax"}

    def test_expected_net_reports_the_residual_not_a_judgement(self, context, snapshot):
        _, output = tools.execute(
            context,
            "compute_expected_net",
            json.dumps({"candidate_id": snapshot.candidate_ids()[0]}),
        )
        assert isinstance(output, ComputeExpectedNetOutput)
        assert output.group_unexplained_delta_paise == 0
        assert output.group_total_is_exact is True

    def test_comparison_reports_every_relation_for_one_candidate_only(self, context, snapshot):
        _, output = tools.execute(
            context,
            "compare_reference_fragment",
            json.dumps({"candidate_id": snapshot.candidate_ids()[1], "fragment": "PF*******VQ"}),
        )
        assert isinstance(output, CompareReferenceFragmentOutput)
        assert output.fragment_present_in_narration is True
        assert output.candidate_id == snapshot.candidate_ids()[1]
        # One entry per (settlement, reference kind): utr and settlement_id.
        assert {c.reference_kind for c in output.comparisons} == {"utr", "settlement_id"}

    def test_a_fabricated_fragment_is_reported_as_absent_not_hidden(self, context, snapshot):
        _, output = tools.execute(
            context,
            "compare_reference_fragment",
            json.dumps({"candidate_id": snapshot.candidate_ids()[1], "fragment": TRUE_UTR}),
        )
        assert output.fragment_present_in_narration is False
        assert output.fragment_offsets == ()

    def test_a_settlement_with_no_reference_yields_no_utr_comparison(self):
        snapshot = no_reference_snapshot()
        context = ToolContext(snapshot=snapshot)
        _, output = tools.execute(
            context,
            "compare_reference_fragment",
            json.dumps({"candidate_id": snapshot.candidate_ids()[0], "fragment": "CREDIT"}),
        )
        assert {c.reference_kind for c in output.comparisons} == {"settlement_id"}

    def test_no_tool_output_contains_a_verdict_field(self, context, snapshot):
        calls = [
            ("lookup_candidate_records", {"candidate_id": snapshot.candidate_ids()[0]}),
            ("inspect_settlement_breakup", {"settlement_id": TRUE_SETTLEMENT_ID}),
            ("compute_expected_net", {"candidate_id": snapshot.candidate_ids()[0]}),
            (
                "compare_reference_fragment",
                {"candidate_id": snapshot.candidate_ids()[0], "fragment": "PF*******VQ"},
            ),
        ]
        for name, args in calls:
            _, output = tools.execute(context, name, json.dumps(args))
            serialized = json.dumps(output.model_dump(mode="json")).lower()
            for forbidden in ("confidence", "is_correct", "winner", "recommend", "score", "rank"):
                assert forbidden not in serialized, f"{name} leaked {forbidden}"

    def test_fragment_offsets_locate_every_occurrence(self, context, snapshot):
        _, output = tools.execute(
            context,
            "compare_reference_fragment",
            json.dumps({"candidate_id": snapshot.candidate_ids()[0], "fragment": "R"}),
        )
        assert len(output.fragment_offsets) == MASKED_NARRATION.count("R")


class TestReadOnly:
    def test_running_every_tool_leaves_the_snapshot_hash_unchanged(self, snapshot):
        before = snapshot.content_hash
        context = ToolContext(snapshot=snapshot)
        tools.execute(
            context,
            "lookup_candidate_records",
            json.dumps({"candidate_id": snapshot.candidate_ids()[0]}),
        )
        tools.execute(
            context,
            "inspect_settlement_breakup",
            json.dumps({"settlement_id": TRUE_SETTLEMENT_ID}),
        )
        tools.execute(
            context,
            "compute_expected_net",
            json.dumps({"candidate_id": snapshot.candidate_ids()[1]}),
        )
        tools.execute(
            context,
            "compare_reference_fragment",
            json.dumps({"candidate_id": snapshot.candidate_ids()[1], "fragment": "PF*******VQ"}),
        )
        assert snapshot.content_hash == before
        assert snapshot.verify_integrity()

    def test_the_snapshot_cannot_be_mutated_through_the_context(self, snapshot):
        context = ToolContext(snapshot=snapshot)
        with pytest.raises(pydantic.ValidationError):
            context.snapshot.candidates = ()
        with pytest.raises(AttributeError):
            context.snapshot.candidates.append("x")

    def test_the_context_carries_nothing_writable(self):
        """A handler cannot write because it is never handed anything that can."""
        assert set(ToolContext.__dataclass_fields__) == {"snapshot"}

    def test_the_tools_module_imports_no_storage_or_loader(self):
        source = Path(tools.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        for forbidden in ("sqlite3", "finrecon.ledger", "finrecon.loader", "finrecon.pipeline"):
            assert not any(m.startswith(forbidden) for m in imported), forbidden

    def test_the_tools_module_never_mentions_ground_truth(self):
        source = Path(tools.__file__).read_text(encoding="utf-8")
        assert "ground_truth" not in source

    def test_no_tool_output_can_carry_a_tier_label(self, context, snapshot):
        _, output = tools.execute(
            context,
            "lookup_candidate_records",
            json.dumps({"candidate_id": snapshot.candidate_ids()[0]}),
        )
        serialized = json.dumps(output.model_dump(mode="json"))
        for leak in ("tier", "archetype", "required_outcome", "true_reference"):
            assert leak not in serialized


class TestOutputSchemasAreClosed:
    def test_an_output_model_rejects_an_unmodelled_field(self):
        with pytest.raises(pydantic.ValidationError):
            ComputeExpectedNetOutput.model_validate(
                {
                    "candidate_id": "c",
                    "bank_amount_paise": 1,
                    "settlement_group_total_paise": 1,
                    "group_unexplained_delta_paise": 0,
                    "group_total_is_exact": True,
                    "every_breakup_is_exact": True,
                    "per_settlement": [],
                    "recommended": "c",
                }
            )

    def test_an_output_model_rejects_a_float_where_paise_belong(self):
        with pytest.raises(pydantic.ValidationError):
            InspectSettlementBreakupOutput.model_validate(
                {
                    "settlement_id": "s",
                    "settlement_amount_paise": 10.5,
                    "breakup_total_paise": 10,
                    "unexplained_delta_paise": 0,
                    "declared_adjustment_paise": 0,
                    "totals_by_line_type": [],
                    "lines": [],
                }
            )


def test_a_single_settlement_snapshot_still_validates_access():
    snapshot = snapshot_of(
        narration="NEFT CR REF ABCDEF12345",
        settlements=(settlement_facts("setl_only", "ABCDEF12345"),),
    )
    context = ToolContext(snapshot=snapshot)
    _, output = tools.execute(
        context,
        "compare_reference_fragment",
        json.dumps({"candidate_id": snapshot.candidate_ids()[0], "fragment": "ABCDEF12345"}),
    )
    assert output.fragment_present_in_narration is True
