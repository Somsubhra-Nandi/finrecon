"""The bounded schema-proposal service. Proposes a mapping; authorizes nothing.

This module's entire authority is to return a
:class:`~.proposal.MappingProposal` object to its caller. Structurally, not
by convention: it is handed no :class:`~finrecon.ledger.store.LedgerStore`,
imports nothing from :mod:`finrecon.orchestrate`, :mod:`finrecon.stage3` or
the saved-mapping store, and therefore cannot persist a mapping, open a
batch, create a ``BankRecord`` or start a reconciliation even if a future
edit here tried to. The only way a proposal becomes real is a human
confirming it through the mapping API, which writes the mapping itself.

**Bounded in three dimensions.**

*Input* -- one :class:`~.sample.BankCsvSample`, whose caps live in that
module and are not parameters here.

*Turns* -- one model call, plus at most one repair call when the first
proposal fails deterministic validation. Two calls maximum, per proposal
request, enforced by :data:`MAX_MODEL_CALLS` and a loop that cannot exceed
it. There is no adaptive budget and no third attempt.

*Blast radius on failure* -- every failure mode returns a
:class:`ProposalOutcome` carrying a failure code. Nothing here raises past
its caller for a provider problem, because "the model was unavailable" must
degrade to "fill the mapping in by hand", never to an error page or, worse,
a run that proceeded without a mapping.

**Why a tool call rather than a JSON-mode response.** The provider
abstraction (:mod:`finrecon.agent.providers.base`) already speaks tools and
already sends strict function schemas where the dialect supports them; it has
no ``response_format`` concept. Asking for one tool call is therefore the
provider-neutral way to get structured output here, and it reuses the strict
duplicate-key argument decoder rather than inventing a second one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from finrecon.agent.providers.base import (
    ConversationMessage,
    ProviderConfigurationError,
    ProviderError,
    ToolSpec,
)
from finrecon.agent.providers.chain import AllProvidersFailedError, ProviderChain
from finrecon.agent.tools import ToolValidationError, decode_tool_arguments

from . import prompt as prompt_module
from .proposal import (
    PROPOSAL_SCHEMA_VERSION,
    PROPOSAL_TOOL_NAME,
    MappingProposal,
    proposal_tool_schema,
)
from .sample import BankCsvSample
from .validation import MappingValidation, validate_mapping_payload

MAX_MODEL_CALLS = 2
"""One proposal call plus at most one repair call. Never more.

The brief's ceiling, made a constant so it is checkable rather than
implied by control flow. A third attempt would be resampling until the
validator happens to agree, which is the same anti-pattern the provider
chain refuses for Stage-3 semantic failures.
"""

PROPOSAL_PROFILE_ID = "proposed"
"""Placeholder ``profile_id`` used only to build the proposal for validation.

A real ``profile_id`` namespaces every ``bank_record_id`` a mapping
produces, so it is assigned by the saved-mapping store at confirmation time
and is never taken from a model. This constant exists so validation can
construct a profile from a proposal without inventing an identity that
might be mistaken for one.
"""


class ProposalFailure:
    """The failure codes a proposal request can return. All non-fatal."""

    PROVIDER_UNAVAILABLE = "provider_unavailable"
    """No provider is configured, or every configured provider failed."""
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    NO_TOOL_CALL = "no_tool_call"
    """The model answered with prose instead of calling the tool."""
    MALFORMED_OUTPUT = "malformed_output"
    """The tool arguments were not decodable, or failed the strict schema."""
    INVALID_PROPOSAL = "invalid_proposal"
    """Schema-valid, but rejected by deterministic validation against the file."""
    UNREADABLE_SAMPLE = "unreadable_sample"


@dataclass(frozen=True)
class ProposalAttempt:
    """One model call, and what became of it. Recorded for the UI and audit."""

    index: int
    provider: str | None
    model: str | None
    outcome: str
    detail: str | None = None

    def payload(self) -> dict:
        return {
            "index": self.index,
            "provider": self.provider,
            "model": self.model,
            "outcome": self.outcome,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ProposalOutcome:
    """The result of one proposal request: a proposal, or a stated failure.

    Never both, and never neither. A caller reads :attr:`succeeded` and has
    exactly two branches to write -- show the editor pre-filled, or show the
    editor empty with the failure explained.
    """

    sample: BankCsvSample
    proposal: MappingProposal | None = None
    validation: MappingValidation | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    attempts: tuple[ProposalAttempt, ...] = ()
    provider: str | None = None
    model: str | None = None
    reported_model: str | None = None
    proposed_at: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.proposal is not None

    def provenance_payload(self) -> dict:
        """Where this proposal came from. Metadata, never authority.

        Stored beside a confirmed mapping so a reviewer can see that a model
        was involved and which one. The mapping's authority remains
        ``human_confirmed`` regardless of what this says, including when it
        says nothing because no model was ever consulted.
        """
        return {
            "schema_version": PROPOSAL_SCHEMA_VERSION,
            "prompt_version": prompt_module.PROMPT_VERSION,
            "provider": self.provider,
            "model": self.model,
            "reported_model": self.reported_model,
            "proposed_at": self.proposed_at,
            "model_calls": len(self.attempts),
            "sample_bounds": self.sample.bounds_payload(),
        }


def _tool_spec(sample: BankCsvSample) -> ToolSpec:
    return ToolSpec(
        name=PROPOSAL_TOOL_NAME,
        description=(
            "Propose how this statement's columns map onto FinRecon's canonical "
            "bank transaction. Call exactly once. Your proposal is reviewed and "
            "edited by a person before it is used."
        ),
        parameters_json_schema=proposal_tool_schema(
            tuple(h for h in sample.raw_headers if h)
        ),
    )


def _decode(raw_arguments: str) -> MappingProposal:
    """Strict decode plus strict schema validation. Raises on anything else."""
    parsed = decode_tool_arguments(PROPOSAL_TOOL_NAME, raw_arguments)
    return MappingProposal.model_validate(parsed)


def _validated(
    proposal: MappingProposal, sample: BankCsvSample
) -> MappingValidation:
    return validate_mapping_payload(
        proposal.mapping.profile_payload(profile_id=PROPOSAL_PROFILE_ID), sample
    )


def propose_mapping(
    sample: BankCsvSample,
    *,
    chain: ProviderChain,
) -> ProposalOutcome:
    """Ask a model for one column mapping over a bounded sample.

    Returns an outcome in every case. The bounded retry is spent only on a
    proposal that decoded cleanly and then failed *deterministic validation*
    -- there is something specific to correct, and the validator's own
    findings are what is sent back. A malformed or missing tool call is not
    retried: nothing useful can be said about it beyond "that was not a
    proposal", and a second identical prompt is not a correction.
    """
    now = datetime.now(timezone.utc).isoformat()
    tools = (_tool_spec(sample),)
    messages: list[ConversationMessage] = [
        ConversationMessage(role="system", content=prompt_module.SYSTEM_PROMPT),
        ConversationMessage(role="user", content=prompt_module.user_message(sample)),
    ]
    attempts: list[ProposalAttempt] = []
    last_validation: MappingValidation | None = None

    for call_index in range(1, MAX_MODEL_CALLS + 1):
        try:
            result = chain.complete(tuple(messages), tools)
        except ProviderConfigurationError as exc:
            attempts.append(
                ProposalAttempt(call_index, exc.provider, None, exc.kind, exc.message)
            )
            return ProposalOutcome(
                sample=sample,
                failure_code=ProposalFailure.PROVIDER_NOT_CONFIGURED,
                failure_message=(
                    "No model provider is configured on this server, so a mapping "
                    "could not be proposed automatically."
                ),
                attempts=tuple(attempts),
                proposed_at=now,
            )
        except AllProvidersFailedError as exc:
            attempts.append(
                ProposalAttempt(
                    call_index,
                    exc.last_error.provider,
                    None,
                    exc.last_error.kind,
                    exc.last_error.message,
                )
            )
            return ProposalOutcome(
                sample=sample,
                failure_code=ProposalFailure.PROVIDER_UNAVAILABLE,
                failure_message=(
                    "The mapping-proposal service could not reach a model provider."
                ),
                attempts=tuple(attempts),
                proposed_at=now,
            )
        except ProviderError as exc:
            attempts.append(
                ProposalAttempt(call_index, exc.provider, None, exc.kind, exc.message)
            )
            return ProposalOutcome(
                sample=sample,
                failure_code=ProposalFailure.PROVIDER_UNAVAILABLE,
                failure_message=(
                    "The mapping-proposal service could not reach a model provider."
                ),
                attempts=tuple(attempts),
                proposed_at=now,
            )

        response = result.response
        identity: dict[str, Any] = {
            "provider": response.provider,
            "model": response.model,
        }
        call = next(
            (c for c in response.tool_calls if c.tool_name == PROPOSAL_TOOL_NAME), None
        )
        if call is None:
            attempts.append(
                ProposalAttempt(
                    call_index,
                    response.provider,
                    response.model,
                    ProposalFailure.NO_TOOL_CALL,
                    "the model returned no call to the proposal tool",
                )
            )
            return ProposalOutcome(
                sample=sample,
                failure_code=ProposalFailure.NO_TOOL_CALL,
                failure_message=(
                    "The model did not return a structured mapping proposal."
                ),
                attempts=tuple(attempts),
                proposed_at=now,
                **identity,
            )

        try:
            proposal = _decode(call.raw_arguments)
        except ToolValidationError as exc:
            attempts.append(
                ProposalAttempt(
                    call_index, response.provider, response.model, exc.reason, exc.detail
                )
            )
            return ProposalOutcome(
                sample=sample,
                failure_code=ProposalFailure.MALFORMED_OUTPUT,
                failure_message=(
                    "The model's mapping proposal was not valid structured output."
                ),
                attempts=tuple(attempts),
                proposed_at=now,
                **identity,
            )
        except ValidationError as exc:
            attempts.append(
                ProposalAttempt(
                    call_index,
                    response.provider,
                    response.model,
                    "schema_validation_failed",
                    f"{exc.error_count()} field error(s)",
                )
            )
            return ProposalOutcome(
                sample=sample,
                failure_code=ProposalFailure.MALFORMED_OUTPUT,
                failure_message=(
                    "The model's mapping proposal did not match the required schema."
                ),
                attempts=tuple(attempts),
                proposed_at=now,
                **identity,
            )

        validation = _validated(proposal, sample)
        last_validation = validation
        if validation.ok:
            attempts.append(
                ProposalAttempt(
                    call_index, response.provider, response.model, "accepted"
                )
            )
            return ProposalOutcome(
                sample=sample,
                proposal=proposal,
                validation=validation,
                attempts=tuple(attempts),
                reported_model=response.reported_model,
                proposed_at=now,
                **identity,
            )

        reasons = tuple(issue.message for issue in validation.errors)
        attempts.append(
            ProposalAttempt(
                call_index,
                response.provider,
                response.model,
                ProposalFailure.INVALID_PROPOSAL,
                "; ".join(reasons),
            )
        )
        if call_index >= MAX_MODEL_CALLS:
            break
        # The one permitted repair. The model is told exactly what the local
        # checks rejected and gets a single further call; the loop bound, not
        # a judgement about the reply, is what stops this.
        messages.append(
            ConversationMessage(
                role="assistant", content="", tool_calls=(call,)
            )
        )
        messages.append(
            ConversationMessage(
                role="tool",
                content=prompt_module.repair_message(reasons),
                tool_call_id=call.call_id,
                tool_name=PROPOSAL_TOOL_NAME,
            )
        )

    return ProposalOutcome(
        sample=sample,
        validation=last_validation,
        failure_code=ProposalFailure.INVALID_PROPOSAL,
        failure_message=(
            "The proposed mapping did not pass FinRecon's checks against this "
            "file, and the one permitted correction did not either. Map the "
            "columns manually below."
        ),
        attempts=tuple(attempts),
        proposed_at=now,
        provider=attempts[-1].provider if attempts else None,
        model=attempts[-1].model if attempts else None,
    )


__all__ = [
    "MAX_MODEL_CALLS",
    "PROPOSAL_PROFILE_ID",
    "ProposalAttempt",
    "ProposalFailure",
    "ProposalOutcome",
    "propose_mapping",
]
