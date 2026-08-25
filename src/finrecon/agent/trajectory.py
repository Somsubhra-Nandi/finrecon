"""The investigation trajectory -- the complete, replayable record of one run.

DESIGN.md 13 requires "an audit record for every decision, including full
agent trajectory". This is that record, and it is written to be sufficient
on its own: a reader holding one :class:`Trajectory` and the case snapshot
it names can reconstruct exactly what happened, in order, without access to
the process that produced it.

Recorded: the snapshot hash the run was pinned to, the provider chain, the
provider and exact model ID that answered each step, prompt / tool-schema /
loop versions, the step budget, every model turn with its text and requested
calls, every tool call with its *validated* arguments and *raw* output,
every validation failure with a machine-readable reason, per-attempt
provider outcomes including fallbacks, latency, token usage where the
provider reports it, and the reason the investigation ended.

Never recorded: API keys, authorization headers, or request payloads that
could carry one. The transport layer builds credentials inside the call and
returns only a decoded response body; nothing here is ever handed a secret
to leave out, which is a stronger guarantee than remembering to redact.

The trajectory is also the cache entry. It round-trips through JSON without
loss, and replay reconstructs the same tool evidence the validator saw the
first time -- so ``make eval`` can reproduce an adjudication with no API key
(DESIGN.md 6.2).
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from finrecon.normalize.provenance import FrozenModel

TERMINATION_INVESTIGATION_COMPLETE = "investigation_complete"
"""The model returned a turn with no tool calls. The normal ending."""

TERMINATION_DETERMINISTIC_POLICY_RESOLVED = "deterministic_policy_resolved"
"""Existing validator/policy predicates resolved after a complete tool batch.

The model did not decide or announce completion. The loop stopped before a
redundant next model turn because the deterministic decision layer already
had sufficient raw evidence over the complete immutable candidate set.
"""

TERMINATION_STEP_BUDGET_EXHAUSTED = "step_budget_exhausted"
"""The loop hit its fixed maximum. A hard blocker -- never a reason to guess."""

TERMINATION_TOOL_VALIDATION_FAILED = "tool_validation_failed"
"""A requested call was refused before execution. Fails safe, does not retry."""

TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE = "provider_infrastructure_failure"
"""Every configured provider failed to answer. Escalate; the evidence is absent."""

TERMINATION_PROVIDER_CONFIGURATION_FAILURE = "provider_configuration_failure"
"""Credentials or request shape are wrong. Surfaces rather than failing over."""

TERMINATION_REASONS: tuple[str, ...] = (
    TERMINATION_INVESTIGATION_COMPLETE,
    TERMINATION_DETERMINISTIC_POLICY_RESOLVED,
    TERMINATION_STEP_BUDGET_EXHAUSTED,
    TERMINATION_TOOL_VALIDATION_FAILED,
    TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE,
    TERMINATION_PROVIDER_CONFIGURATION_FAILURE,
)

TerminationReason = Literal[
    "investigation_complete",
    "deterministic_policy_resolved",
    "step_budget_exhausted",
    "tool_validation_failed",
    "provider_infrastructure_failure",
    "provider_configuration_failure",
]


class UsageRecord(FrozenModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ProviderAttemptRecord(FrozenModel):
    """One attempt against one provider, successful or not."""

    provider: str
    model: str
    attempt: int
    outcome: str
    """``"success"`` or the infrastructure/configuration failure kind."""
    error_class: str | None = None
    detail: str | None = None


class RequestedToolCall(FrozenModel):
    """A call exactly as the model asked for it, before any validation."""

    call_id: str
    tool_name: str
    raw_arguments: str


class ModelStepRecord(FrozenModel):
    """One model turn: who answered, how, and what it asked for."""

    index: int
    provider: str
    model: str
    fallback_used: bool
    """True when the first provider in the chain did not produce this turn."""
    fallback_reason: str | None
    """The failure kind that caused the move, e.g. ``rate_limited``."""
    transport_attempts: int
    """Attempts against the provider that ultimately answered, including it."""
    attempts: tuple[ProviderAttemptRecord, ...]
    latency_ms: int | None
    usage: UsageRecord
    finish_reason: str | None
    assistant_text: str
    """The model's prose. Recorded for audit; **never** an input to adjudication."""
    requested_tool_calls: tuple[RequestedToolCall, ...]


INVOCATION_SUCCEEDED = "succeeded"
"""Preflight passed, the handler ran, and its output is validator input."""

INVOCATION_VALIDATION_FAILED = "validation_failed"
"""The call was refused by preflight. No handler ran; there is no output."""

INVOCATION_SKIPPED_BATCH_REJECTED = "skipped_due_to_batch_rejection"
"""The call passed preflight but a *sibling* call in the same batch did not.

Batch preflight is atomic: one invalid call means zero handlers execute
(DESIGN.md 4.1). Before this state existed, the calls that lost their batch
this way left no record at all, so a rejected turn looked in the audit trail
like a turn that had only ever asked for the one malformed call. That is a
false account of what the model did. The evidence such a call would have
produced is still never gathered -- ``output`` stays ``None``, so it can
never reach :func:`finrecon.decide.validator.raw_tool_evidence` -- but the
request is now visible.
"""

INVOCATION_STATUSES: tuple[str, ...] = (
    INVOCATION_SUCCEEDED,
    INVOCATION_VALIDATION_FAILED,
    INVOCATION_SKIPPED_BATCH_REJECTED,
)

InvocationStatus = Literal[
    "succeeded",
    "validation_failed",
    "skipped_due_to_batch_rejection",
]


class ToolInvocationRecord(FrozenModel):
    """One tool call: what was asked, whether it was allowed, and what came back.

    Every call the model requested gets one of these, including calls that
    were never executed. ``status`` is stored rather than derived so replay
    reproduces the exact disposition of each call instead of re-inferring it
    from the presence of an output.
    """

    step_index: int
    call_index: int
    tool_name: str
    raw_arguments: str
    status: InvocationStatus
    """Which of the three declared dispositions this call reached."""
    validated_arguments: dict | None
    """Present when preflight passed -- including for a call later skipped
    because its batch was rejected. ``None`` for a refused call."""
    validation_error_reason: str | None
    validation_error_detail: str | None
    output: dict | None
    """The tool's raw output payload. This, and only this, is validator input."""
    latency_ms: int | None = None

    @model_validator(mode="after")
    def _status_matches_the_record(self) -> "ToolInvocationRecord":
        """The three states are mutually exclusive, and the record must say so.

        Enforced rather than trusted: a ``skipped`` record carrying an output
        would be evidence the controller never gathered, and a ``succeeded``
        record carrying a validation error would be a refused call counted as
        a fact. Both are caught here, at construction, rather than downstream.
        """
        failed = self.validation_error_reason is not None
        has_output = self.output is not None
        expected = {
            INVOCATION_SUCCEEDED: (False, True),
            INVOCATION_VALIDATION_FAILED: (True, False),
            INVOCATION_SKIPPED_BATCH_REJECTED: (False, False),
        }[self.status]
        if (failed, has_output) != expected:
            raise ValueError(
                f"tool invocation status {self.status!r} is inconsistent with the "
                f"record: validation_error_reason set={failed}, output set={has_output}"
            )
        return self

    @property
    def succeeded(self) -> bool:
        return self.status == INVOCATION_SUCCEEDED

    @property
    def skipped(self) -> bool:
        return self.status == INVOCATION_SKIPPED_BATCH_REJECTED


class Trajectory(FrozenModel):
    """The complete record of one bounded investigation."""

    case_id: str
    snapshot_hash: str
    """Content hash of the immutable snapshot this run was pinned to."""
    batch_id: str
    prompt_version: str
    tool_schema_version: str
    agent_loop_version: str
    cache_schema_version: str
    validator_version: str
    policy_version: str
    policy_declaration: dict
    max_steps: int
    max_tool_calls_per_step: int
    provider_chain: tuple[str, ...]
    """``provider:model`` for each configured provider, in order."""
    steps: tuple[ModelStepRecord, ...]
    tool_invocations: tuple[ToolInvocationRecord, ...]
    termination_reason: TerminationReason
    termination_detail: str | None = None
    total_latency_ms: int | None = None
    """End-to-end live investigation latency, including model and tool time."""
    cache_key: str = ""
    replayed: bool = False
    """True when this trajectory was served from cache rather than a live run."""

    # --- derived facts, all computed from the record itself ---------------

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def budget_exhausted(self) -> bool:
        return self.termination_reason == TERMINATION_STEP_BUDGET_EXHAUSTED

    @property
    def completed_normally(self) -> bool:
        return self.termination_reason == TERMINATION_INVESTIGATION_COMPLETE

    @property
    def deterministic_early_stop(self) -> bool:
        return self.termination_reason == TERMINATION_DETERMINISTIC_POLICY_RESOLVED

    @property
    def had_validation_failure(self) -> bool:
        return any(inv.validation_error_reason is not None for inv in self.tool_invocations)

    @property
    def fallback_used(self) -> bool:
        return any(step.fallback_used for step in self.steps)

    @property
    def fallback_reasons(self) -> tuple[str, ...]:
        return tuple(
            step.fallback_reason for step in self.steps if step.fallback_reason is not None
        )

    @property
    def providers_used(self) -> tuple[str, ...]:
        seen: list[str] = []
        for step in self.steps:
            if step.provider not in seen:
                seen.append(step.provider)
        return tuple(seen)

    @property
    def models_used(self) -> tuple[str, ...]:
        seen: list[str] = []
        for step in self.steps:
            key = f"{step.provider}:{step.model}"
            if key not in seen:
                seen.append(key)
        return tuple(seen)

    def total_tokens(self) -> int | None:
        values = [s.usage.total_tokens for s in self.steps if s.usage.total_tokens is not None]
        return sum(values) if values else None

    def input_tokens(self) -> int | None:
        values = [s.usage.input_tokens for s in self.steps if s.usage.input_tokens is not None]
        return sum(values) if values else None

    def output_tokens(self) -> int | None:
        values = [s.usage.output_tokens for s in self.steps if s.usage.output_tokens is not None]
        return sum(values) if values else None

    def provider_latency_ms(self) -> int | None:
        values = [s.latency_ms for s in self.steps if s.latency_ms is not None]
        return sum(values) if values else None

    @property
    def provider_call_count(self) -> int:
        """Transport attempts made across all model steps, including fallbacks."""
        return sum(len(step.attempts) for step in self.steps)

    @property
    def tool_invocation_count(self) -> int:
        return len(self.successful_tool_invocations())

    @property
    def requested_tool_call_count(self) -> int:
        return sum(len(step.requested_tool_calls) for step in self.steps)

    def successful_tool_invocations(self) -> tuple[ToolInvocationRecord, ...]:
        return tuple(inv for inv in self.tool_invocations if inv.succeeded)

    def skipped_tool_invocations(self) -> tuple[ToolInvocationRecord, ...]:
        """Calls that passed preflight but lost their batch to a sibling failure."""
        return tuple(inv for inv in self.tool_invocations if inv.skipped)


__all__ = [
    "INVOCATION_SKIPPED_BATCH_REJECTED",
    "INVOCATION_STATUSES",
    "INVOCATION_SUCCEEDED",
    "INVOCATION_VALIDATION_FAILED",
    "TERMINATION_DETERMINISTIC_POLICY_RESOLVED",
    "TERMINATION_INVESTIGATION_COMPLETE",
    "TERMINATION_PROVIDER_CONFIGURATION_FAILURE",
    "TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE",
    "TERMINATION_REASONS",
    "TERMINATION_STEP_BUDGET_EXHAUSTED",
    "TERMINATION_TOOL_VALIDATION_FAILED",
    "InvocationStatus",
    "ModelStepRecord",
    "ProviderAttemptRecord",
    "RequestedToolCall",
    "TerminationReason",
    "Trajectory",
    "ToolInvocationRecord",
    "UsageRecord",
]
