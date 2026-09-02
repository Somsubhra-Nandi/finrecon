"""The bounded schema-proposal service: what it sends, accepts, and refuses.

Every test here drives a fake provider. No model is contacted, which the
suite enforces anyway (see ``tests/conftest.py``), and nothing measured here
is a statement about model capability -- these are assertions about the
*boundary*: how much data leaves, what shapes are accepted, what happens
when the answer is wrong or absent, and that the retry is genuinely bounded.

The load-bearing claim under test is negative: a proposal cannot become a
mapping. The service is handed no store, and the two tests at the bottom of
this file assert that structurally rather than by inspection.
"""

from __future__ import annotations

import json

import pytest

from finrecon.adapters.bank.mapping.formats import SUPPORTED_VALUE_DATE_FORMATS
from finrecon.adapters.bank.mapping.proposal import PROPOSAL_TOOL_NAME
from finrecon.adapters.bank.mapping.sample import (
    MAX_CELL_CHARS,
    MAX_SAMPLE_ROWS,
    read_sample,
)
from finrecon.adapters.bank.mapping.service import (
    MAX_MODEL_CALLS,
    ProposalFailure,
    propose_mapping,
)
from finrecon.agent.providers.base import (
    ModelProvider,
    ModelResponse,
    ProviderConfigurationError,
    ProviderInfrastructureError,
    ToolCallRequest,
)
from finrecon.agent.providers.chain import ProviderChain

HEADER_LINE = "Txn Reference,Posted On,Particulars,Withdrawal Amt,Deposit Amt"
ROWS = (
    "UTR9911,07/08/2024,NEFT SETTLEMENT RZP,0.00,125000.00\n"
    "UTR9912,08/08/2024,NEFT SETTLEMENT RZP,0.00,50000.00\n"
    "UTR9913,21/08/2024,ACH DEBIT CHARGES,1200.00,0.00\n"
)


def sample_of(header_line: str = HEADER_LINE, rows: str = ROWS):
    return read_sample(f"{header_line}\n{rows}".encode("utf-8"))


def proposal_payload(**mapping_overrides) -> dict:
    money = {
        "kind": "debit_credit",
        "debit_column": "Withdrawal Amt",
        "credit_column": "Deposit Amt",
        "inactive_side_marker": "empty_or_zero",
        "amount_column": None,
        "direction_column": None,
        "credit_values": None,
        "debit_values": None,
    }
    money.update(mapping_overrides.pop("money", {}))
    mapping = {
        "value_date_column": "Posted On",
        "value_date_format": "%d/%m/%Y",
        "value_date_format_certain": True,
        "narration_column": "Particulars",
        "reference_id_column": "Txn Reference",
        "money": money,
    }
    mapping.update(mapping_overrides)
    return {
        "mapping": mapping,
        "reasoning_summary": {
            "value_date": "Posted On holds dd/mm/yyyy dates.",
            "money": "Two amount columns; the unused side is zero-filled.",
            "narration": "Particulars is the free-text description.",
            "reference": "Txn Reference carries UTR-shaped values.",
        },
        "uncertainties": [],
    }


class Scripted(ModelProvider):
    """A provider that replays a scripted sequence of turns. Records every call."""

    provider_id = "scripted"

    def __init__(self, *turns) -> None:
        self._turns = list(turns)
        self.calls: list[tuple] = []

    @property
    def model(self) -> str:
        return "scripted-mapper-v1"

    def complete(self, messages, tools):
        self.calls.append((messages, tools))
        turn = self._turns[min(len(self.calls) - 1, len(self._turns) - 1)]
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, str):
            # Raw argument text, so malformed output can be scripted verbatim.
            calls = (ToolCallRequest("c", PROPOSAL_TOOL_NAME, turn),)
        elif turn is None:
            calls = ()  # answered with prose instead of calling the tool
        else:
            calls = (ToolCallRequest("c", PROPOSAL_TOOL_NAME, json.dumps(turn)),)
        return ModelResponse(
            provider=self.provider_id, model=self.model,
            text="" if calls else "I think the date column is the second one.",
            tool_calls=calls, reported_model="scripted-mapper-v1-0001",
        )


def propose(*turns, sample=None):
    provider = Scripted(*turns)
    outcome = propose_mapping(sample or sample_of(), chain=ProviderChain((provider,)))
    return outcome, provider


class TestInputBounds:
    def test_only_headers_and_a_bounded_row_sample_are_sent(self):
        long_csv = HEADER_LINE + "\n" + "".join(
            f"UTR{index},07/08/2024,ROW {index},0.00,{index}00.00\n" for index in range(50)
        )
        sample = read_sample(long_csv.encode("utf-8"))
        outcome, provider = propose(proposal_payload(), sample=sample)
        assert outcome.succeeded

        sent = "\n".join(message.content for message, in
                         [(m,) for m in provider.calls[0][0]])
        assert "ROW 0" in sent
        # Row 5 onward never leaves the process.
        assert "ROW 5" not in sent
        assert len(sample.rows) == MAX_SAMPLE_ROWS

    def test_the_sample_stops_reading_rather_than_reading_the_whole_file(self):
        long_csv = HEADER_LINE + "\n" + "".join(
            f"UTR{index},07/08/2024,ROW {index},0.00,100.00\n" for index in range(500)
        )
        sample = read_sample(long_csv.encode("utf-8"))
        # Scanned, not total: a large upload costs the same as a small one.
        assert sample.total_data_rows_scanned == MAX_SAMPLE_ROWS
        assert sample.bounds_payload()["sample_rows_sent"] == MAX_SAMPLE_ROWS

    def test_long_cells_are_truncated(self):
        narration = "X" * 500
        sample = sample_of(rows=f"UTR1,07/08/2024,{narration},0.00,100.00\n")
        cell = sample.column("Particulars")[0]
        assert len(cell) == MAX_CELL_CHARS
        assert sample.cells_truncated == 1

    def test_the_tool_schema_is_closed_over_the_files_own_headers(self):
        """Defence in depth: a nonexistent column is not even expressible."""
        _, provider = propose(proposal_payload())
        tools = provider.calls[0][1]
        assert len(tools) == 1
        schema = tools[0].parameters_json_schema
        enum = schema["properties"]["mapping"]["properties"]["value_date_column"]["enum"]
        assert enum == ["Txn Reference", "Posted On", "Particulars", "Withdrawal Amt", "Deposit Amt"]
        formats = schema["properties"]["mapping"]["properties"]["value_date_format"]["enum"]
        assert formats == list(SUPPORTED_VALUE_DATE_FORMATS)

    def test_the_prompt_carries_no_reconciliation_vocabulary(self):
        """A schema question must not arrive dressed as an investigation.

        Asserted against the instructions FinRecon writes, not against the
        sampled rows -- a bank's own narration may well say "SETTLEMENT", and
        redacting the file's own words would defeat the point of sampling it.
        What must be absent is FinRecon's decision vocabulary: nothing here
        may invite the model to reason about candidates or adjudicate a case.
        """
        from finrecon.adapters.bank.mapping import prompt as prompt_module

        instructions = prompt_module.SYSTEM_PROMPT.lower()
        for forbidden in ("candidate", "razorpay", "escalate", "settlement"):
            assert forbidden not in instructions, forbidden

    def test_the_razorpay_side_is_never_part_of_a_proposal(self):
        """The proposal boundary sees one bank table and no counterparty data."""
        _, provider = propose(proposal_payload())
        sent = "\n".join(m.content for m in provider.calls[0][0]).lower()
        for forbidden in ("razorpay", "candidate_id", "settlement_id", "case_id"):
            assert forbidden not in sent


class TestAcceptance:
    def test_a_strict_structured_response_is_accepted(self):
        outcome, provider = propose(proposal_payload())
        assert outcome.succeeded
        assert outcome.validation is not None and outcome.validation.ok
        assert outcome.proposal.mapping.value_date_column == "Posted On"
        assert outcome.proposal.mapping.money.kind == "debit_credit"
        assert outcome.proposal.mapping.money.inactive_side_marker == "empty_or_zero"
        assert len(provider.calls) == 1

    def test_the_resolved_money_payload_drops_the_unused_side(self):
        outcome, _ = propose(proposal_payload())
        resolved = outcome.proposal.mapping.money.resolved()
        assert set(resolved) == {"kind", "debit_column", "credit_column", "inactive_side_marker"}

    def test_an_amount_direction_proposal_is_accepted_when_the_file_supports_it(self):
        header = "Ref,Date,Narration,Amount,Dr/Cr"
        rows = "U1,07/08/2024,NEFT RZP,125000.00,CR\nU2,21/08/2024,CHARGES,1200.00,DR\n"
        payload = proposal_payload(
            value_date_column="Date", narration_column="Narration",
            reference_id_column="Ref",
            money={
                "kind": "amount_direction", "debit_column": None, "credit_column": None,
                "inactive_side_marker": None, "amount_column": "Amount",
                "direction_column": "Dr/Cr", "credit_values": ["CR"], "debit_values": ["DR"],
            },
        )
        outcome, _ = propose(payload, sample=sample_of(header, rows))
        assert outcome.succeeded, outcome.validation and outcome.validation.payload()
        assert outcome.proposal.mapping.money.kind == "amount_direction"

    def test_provenance_metadata_records_the_model_without_conferring_authority(self):
        outcome, _ = propose(proposal_payload())
        provenance = outcome.provenance_payload()
        assert provenance["provider"] == "scripted"
        assert provenance["model"] == "scripted-mapper-v1"
        assert provenance["reported_model"] == "scripted-mapper-v1-0001"
        assert provenance["model_calls"] == 1
        assert provenance["sample_bounds"]["max_sample_rows"] == MAX_SAMPLE_ROWS
        # Nothing in the provenance claims the mapping is authoritative.
        assert "provenance" not in provenance
        assert "human_confirmed" not in json.dumps(provenance)


class TestRejection:
    def test_a_nonexistent_proposed_column_is_rejected(self):
        outcome, provider = propose(
            proposal_payload(narration_column="Description Of Transaction"),
            proposal_payload(narration_column="Also Not A Column"),
        )
        assert not outcome.succeeded
        assert outcome.failure_code == ProposalFailure.INVALID_PROPOSAL
        codes = {issue.code for issue in outcome.validation.errors}
        assert "unknown_column" in codes

    def test_a_malformed_money_mapping_is_rejected(self):
        """Debit and credit naming one column cannot express "one side active"."""
        outcome, _ = propose(
            proposal_payload(money={"debit_column": "Deposit Amt", "inactive_side_marker": "empty_only"}),
            proposal_payload(money={"debit_column": "Deposit Amt", "inactive_side_marker": "empty_only"}),
        )
        assert not outcome.succeeded
        codes = {issue.code for issue in outcome.validation.errors}
        assert "debit_equals_credit" in codes

    def test_an_unsupported_date_format_is_refused_by_the_schema_itself(self):
        outcome, _ = propose(proposal_payload(value_date_format="%d %m %Y %H:%M:%S"))
        # The strict Literal-free enum lives in the JSON schema, so an
        # unsupported format arrives as an invalid *proposal* rather than a
        # mapping FinRecon might try to use.
        assert not outcome.succeeded
        assert outcome.failure_code == ProposalFailure.INVALID_PROPOSAL
        codes = {issue.code for issue in outcome.validation.errors}
        assert "unsupported_date_format" in codes

    def test_a_date_format_contradicted_by_the_sample_is_rejected(self):
        outcome, _ = propose(proposal_payload(value_date_format="%Y-%m-%d"))
        assert not outcome.succeeded
        codes = {issue.code for issue in outcome.validation.errors}
        assert "date_format_contradicted_by_sample" in codes

    def test_a_money_model_contradicted_by_the_sample_is_rejected(self):
        """A zero-filled statement read as empty-only would reject every row."""
        outcome, _ = propose(
            proposal_payload(money={"inactive_side_marker": "empty_only"}),
            proposal_payload(money={"inactive_side_marker": "empty_only"}),
        )
        assert not outcome.succeeded
        codes = {issue.code for issue in outcome.validation.errors}
        assert "money_model_contradicted_by_sample" in codes

    def test_malformed_json_output_fails_cleanly(self):
        outcome, provider = propose("{not json at all")
        assert not outcome.succeeded
        assert outcome.failure_code == ProposalFailure.MALFORMED_OUTPUT
        assert outcome.failure_message
        # Not retried: nothing specific can be said about "that was not JSON".
        assert len(provider.calls) == 1

    def test_duplicate_keys_in_the_output_fail_cleanly(self):
        """Two answers for one field is unsafe ambiguity, not a valid call."""
        raw = '{"mapping": {"value_date_column": "Posted On", "value_date_column": "Particulars"}}'
        outcome, _ = propose(raw)
        assert outcome.failure_code == ProposalFailure.MALFORMED_OUTPUT

    def test_a_schema_violating_object_fails_cleanly(self):
        outcome, _ = propose({"mapping": {"value_date_column": "Posted On"}})
        assert outcome.failure_code == ProposalFailure.MALFORMED_OUTPUT

    def test_an_extra_field_is_refused_rather_than_ignored(self):
        payload = proposal_payload()
        payload["mapping"]["confidence_score"] = 0.97
        outcome, _ = propose(payload)
        assert outcome.failure_code == ProposalFailure.MALFORMED_OUTPUT

    def test_prose_instead_of_a_tool_call_fails_cleanly(self):
        outcome, provider = propose(None)
        assert outcome.failure_code == ProposalFailure.NO_TOOL_CALL
        assert len(provider.calls) == 1


class TestDateAmbiguity:
    def test_an_indistinguishable_date_order_is_flagged_for_a_human(self):
        """The case a model must not be believed about.

        Every sampled day-of-month is 12 or lower, so day-first and
        month-first read the same bytes into different dates and nothing in
        the file decides between them.
        """
        rows = "U1,07/08/2024,NEFT RZP,0.00,125000.00\nU2,08/09/2024,NEFT RZP,0.00,5000.00\n"
        outcome, _ = propose(proposal_payload(), sample=sample_of(rows=rows))
        assert outcome.succeeded  # a proposal, but not a settled one
        assert outcome.validation.fields_requiring_human_choice == ("value_date_format",)
        codes = {w.code for w in outcome.validation.warnings}
        assert "date_format_ambiguous" in codes
        assert set(outcome.validation.date_format.ambiguous_with) == {"%m/%d/%Y"}

    def test_a_models_claim_of_certainty_does_not_override_the_data(self):
        rows = "U1,07/08/2024,NEFT RZP,0.00,125000.00\n"
        payload = proposal_payload()
        assert payload["mapping"]["value_date_format_certain"] is True
        outcome, _ = propose(payload, sample=sample_of(rows=rows))
        # It said it was sure. The sample says otherwise, and the sample wins.
        assert outcome.proposal.mapping.value_date_format_certain is True
        assert "value_date_format" in outcome.validation.fields_requiring_human_choice

    def test_an_unambiguous_sample_needs_no_human_choice(self):
        # 21 cannot be a month, so only day-first parses.
        outcome, _ = propose(proposal_payload())
        assert outcome.validation.fields_requiring_human_choice == ()
        assert outcome.validation.date_format.requires_human_choice is False


class TestBoundedRetry:
    def test_exactly_one_repair_attempt_is_made(self):
        outcome, provider = propose(
            proposal_payload(narration_column="Nope"),
            proposal_payload(),  # corrected
        )
        assert outcome.succeeded
        assert len(provider.calls) == 2
        assert [a.outcome for a in outcome.attempts] == ["invalid_proposal", "accepted"]

    def test_the_retry_carries_the_validators_own_findings(self):
        _, provider = propose(
            proposal_payload(narration_column="Nope"), proposal_payload()
        )
        second_turn = provider.calls[1][0]
        tool_messages = [m for m in second_turn if m.role == "tool"]
        assert len(tool_messages) == 1
        assert "'Nope' is not a column in this statement." in tool_messages[0].content

    def test_the_retry_count_is_bounded_and_never_loops(self):
        """A model that keeps producing the same invalid proposal is stopped."""
        bad = proposal_payload(narration_column="Nope")
        outcome, provider = propose(bad, bad, bad, bad, bad)
        assert len(provider.calls) == MAX_MODEL_CALLS == 2
        assert outcome.failure_code == ProposalFailure.INVALID_PROPOSAL
        assert len(outcome.attempts) == 2

    def test_a_malformed_reply_is_not_given_a_repair_turn(self):
        outcome, provider = propose("[]", proposal_payload())
        assert len(provider.calls) == 1
        assert outcome.failure_code == ProposalFailure.MALFORMED_OUTPUT


class TestProviderFailure:
    def test_an_unconfigured_provider_degrades_to_manual(self):
        outcome, _ = propose(
            ProviderConfigurationError(
                "scripted", ProviderConfigurationError.MISSING_CREDENTIALS, "no key"
            )
        )
        assert not outcome.succeeded
        assert outcome.failure_code == ProposalFailure.PROVIDER_NOT_CONFIGURED
        assert outcome.failure_message

    def test_an_infrastructure_failure_degrades_to_manual(self):
        outcome, _ = propose(
            ProviderInfrastructureError(
                "scripted", ProviderInfrastructureError.TIMEOUT, "timed out"
            )
        )
        assert not outcome.succeeded
        assert outcome.failure_code == ProposalFailure.PROVIDER_UNAVAILABLE

    def test_a_rate_limit_across_every_provider_degrades_to_manual(self):
        chain = ProviderChain((
            Scripted(ProviderInfrastructureError(
                "a", ProviderInfrastructureError.RATE_LIMITED, "429")),
            Scripted(ProviderInfrastructureError(
                "b", ProviderInfrastructureError.QUOTA_EXHAUSTED, "no quota")),
        ))
        outcome = propose_mapping(sample_of(), chain=chain)
        assert outcome.failure_code == ProposalFailure.PROVIDER_UNAVAILABLE

    def test_no_failure_mode_raises_out_of_the_service(self):
        """The product must not stop working when a model does.

        Parametrised over every failure the service can meet, because a
        single escaping exception would turn "map it by hand" into an error
        page.
        """
        failures = [
            ProviderConfigurationError("s", ProviderConfigurationError.UNAUTHORIZED, "401"),
            ProviderInfrastructureError("s", ProviderInfrastructureError.SERVER_ERROR, "500"),
            "not json",
            None,
            {"mapping": {}},
            proposal_payload(narration_column="Nope"),
        ]
        for failure in failures:
            outcome, _ = propose(failure, failure)
            assert outcome.proposal is None or outcome.succeeded
            if not outcome.succeeded:
                assert outcome.failure_code and outcome.failure_message


class TestNoAuthority:
    def test_the_service_is_given_nothing_it_could_persist_with(self):
        """Structural, not aspirational.

        ``propose_mapping`` takes a sample and a provider chain. There is no
        parameter through which a store, a connection, a batch or a path
        could be passed, so the function cannot write even if its body
        wanted to.
        """
        import inspect

        signature = inspect.signature(propose_mapping)
        assert set(signature.parameters) == {"sample", "chain"}

    def test_no_module_in_the_mapping_package_imports_anything_that_can_write(self):
        """A proposal has no route to the ledger, orchestration or Stage 3.

        Read off the parsed import statements rather than the file text, so
        the assertion is about what the package can actually reach and not
        about which module names its prose happens to mention.
        """
        import ast
        import pathlib

        import finrecon.adapters.bank.mapping as package

        forbidden = ("finrecon.ledger", "finrecon.orchestrate", "finrecon.stage3")
        directory = pathlib.Path(package.__file__).parent
        checked = 0
        for path in sorted(directory.glob("*.py")):
            checked += 1
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    imported.append(node.module)
            for module in imported:
                assert not any(module.startswith(bad) for bad in forbidden), (
                    f"{path.name} imports {module}, which can write"
                )
        assert checked >= 6  # the package's modules, so this cannot pass vacuously

    def test_a_successful_proposal_produces_no_profile_id_of_its_own(self):
        """Record-namespacing identity is FinRecon's to assign, not a model's."""
        outcome, _ = propose(proposal_payload())
        # The proposal object has no profile_id field at all.
        assert not hasattr(outcome.proposal.mapping, "profile_id")
        payload = outcome.proposal.mapping.profile_payload(profile_id="caller-assigned")
        assert payload["profile_id"] == "caller-assigned"


@pytest.mark.parametrize("fmt", SUPPORTED_VALUE_DATE_FORMATS)
def test_every_offered_date_format_has_a_human_readable_label(fmt):
    """A format the operator cannot interpret is a format they cannot confirm."""
    from finrecon.adapters.bank.mapping.formats import FORMAT_LABELS

    assert fmt in FORMAT_LABELS
    assert FORMAT_LABELS[fmt].strip()
