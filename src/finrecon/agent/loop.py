"""The bounded investigation loop.

An explicit state machine, roughly thirty lines of control flow, written
this way on purpose. DESIGN.md 7 chose a hand-rolled loop over an agent
framework because this system has to be *auditable*: every step, every
refusal and every termination has to be visible in one function a reviewer
can read end to end. A framework would hide the step budget inside a
runtime and the validation path inside a callback.

The machine:

.. code-block:: text

    step 1..N:
        model turn                      (via the provider chain)
          no tool calls   -> investigation_complete
          tool call       -> validate
                               invalid  -> tool_validation_failed  (stop)
                               valid    -> execute, append result, next step
    fell out of the loop  -> step_budget_exhausted

Four termination states, and only the first is ordinary:

``investigation_complete``
    The model stopped asking for tools. Evidence goes to the validator.
``step_budget_exhausted``
    A hard blocker. The case escalates. It is never a reason to pick the
    best-looking candidate -- DESIGN.md 4.3 lists budget exhaustion as a
    blocker precisely so that running out of steps cannot become a decision.
``tool_validation_failed``
    A call that could not be executed: unknown tool, unparsable arguments,
    a field that fails the schema, or an identifier outside the immutable
    candidate set. The loop stops immediately rather than letting a model
    flail against the schema, records the reason, and escalates.
``provider_infrastructure_failure`` / ``provider_configuration_failure``
    No answer was obtained. Escalate; the evidence was never gathered.

**No failure here is ever retried on a different provider unless it was an
infrastructure failure**, and that decision is made by the provider chain
from the exception type, not by this module. A malformed tool call is the
model's behaviour, and re-rolling it elsewhere would be sampling for a
result the system prefers.

One tool call per step
----------------------

The loop executes at most one call per model turn. Two reasons, both
practical: Gemini correlates tool results by function *name* rather than by
call ID, so two simultaneous calls to the same tool are ambiguous on one of
the three supported providers; and a strictly linear trajectory is far
easier to audit and to replay deterministically. Extra calls in one turn are
recorded as requested and then ignored, which is visible in the trajectory
rather than silent.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from finrecon.agent.prompt import case_briefing, system_prompt
from finrecon.agent.providers.base import (
    ConversationMessage,
    ProviderConfigurationError,
    ProviderError,
    ProviderInfrastructureError,
    ToolCallRequest,
)
from finrecon.agent.providers.chain import AllProvidersFailedError, ChainResult, ProviderChain
from finrecon.agent.tools import ToolContext, ToolValidationError, execute, tool_specs
from finrecon.agent.trajectory import (
    TERMINATION_INVESTIGATION_COMPLETE,
    TERMINATION_PROVIDER_CONFIGURATION_FAILURE,
    TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE,
    TERMINATION_STEP_BUDGET_EXHAUSTED,
    TERMINATION_TOOL_VALIDATION_FAILED,
    ModelStepRecord,
    ProviderAttemptRecord,
    RequestedToolCall,
    ToolInvocationRecord,
    Trajectory,
    UsageRecord,
)
from finrecon.agent.version import (
    AGENT_LOOP_VERSION,
    CACHE_SCHEMA_VERSION,
    PROMPT_VERSION,
    TOOL_SCHEMA_VERSION,
)
from finrecon.candidates.snapshot import CaseSnapshot

DEFAULT_MAX_STEPS = 8
"""Fixed step budget. Bounded, configurable, and asserted by the tests.

Sized for the shape of the work rather than for a target coverage number: a
two-candidate case needs a lookup per candidate, a comparison per candidate,
and a closing turn -- five -- so eight leaves room for one detour without
inviting a model to wander. Exhausting it escalates; it never degrades into
a guess, so raising it buys coverage and can never buy an unsafe match.
"""

MAX_TOOL_CALLS_PER_STEP = 1


@dataclass(frozen=True)
class LoopConfig:
    max_steps: int = DEFAULT_MAX_STEPS
    max_tool_calls_per_step: int = MAX_TOOL_CALLS_PER_STEP

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if self.max_tool_calls_per_step < 1:
            raise ValueError("max_tool_calls_per_step must be at least 1")


def _usage_record(chain_result: ChainResult) -> UsageRecord:
    usage = chain_result.response.usage
    return UsageRecord(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )


def _attempt_records(chain_result: ChainResult) -> tuple[ProviderAttemptRecord, ...]:
    return tuple(
        ProviderAttemptRecord(
            provider=attempt.provider,
            model=attempt.model,
            attempt=attempt.attempt,
            outcome=attempt.outcome,
            error_class=attempt.error_class,
            detail=attempt.detail,
        )
        for attempt in chain_result.attempts
    )


def _requested(calls: tuple[ToolCallRequest, ...]) -> tuple[RequestedToolCall, ...]:
    return tuple(
        RequestedToolCall(
            call_id=call.call_id, tool_name=call.tool_name, raw_arguments=call.raw_arguments
        )
        for call in calls
    )


def run_investigation(
    *,
    snapshot: CaseSnapshot,
    chain: ProviderChain,
    config: LoopConfig | None = None,
    cache_key: str = "",
) -> Trajectory:
    """Run one bounded investigation over one immutable case snapshot.

    Returns a :class:`Trajectory` in every outcome, including every failure
    mode. Nothing here raises on a bad run: a case that could not be
    investigated still has to reach the policy gate with a record of why,
    because "we could not gather evidence" and "the evidence was
    inconclusive" must both end in escalation and both be auditable.
    """
    config = config or LoopConfig()
    context = ToolContext(snapshot=snapshot)
    specs = tool_specs()

    messages: list[ConversationMessage] = [
        ConversationMessage(role="system", content=system_prompt()),
        ConversationMessage(role="user", content=case_briefing(snapshot)),
    ]

    steps: list[ModelStepRecord] = []
    invocations: list[ToolInvocationRecord] = []
    termination = TERMINATION_STEP_BUDGET_EXHAUSTED
    termination_detail: str | None = (
        f"step budget of {config.max_steps} exhausted without a terminal model turn"
    )

    def finish() -> Trajectory:
        return Trajectory(
            case_id=snapshot.case_id,
            snapshot_hash=snapshot.content_hash,
            batch_id=snapshot.batch_id,
            prompt_version=PROMPT_VERSION,
            tool_schema_version=TOOL_SCHEMA_VERSION,
            agent_loop_version=AGENT_LOOP_VERSION,
            cache_schema_version=CACHE_SCHEMA_VERSION,
            max_steps=config.max_steps,
            provider_chain=chain.describe(),
            steps=tuple(steps),
            tool_invocations=tuple(invocations),
            termination_reason=termination,  # type: ignore[arg-type]
            termination_detail=termination_detail,
            cache_key=cache_key,
            replayed=False,
        )

    for step_index in range(1, config.max_steps + 1):
        try:
            chain_result = chain.complete(tuple(messages), specs)
        except AllProvidersFailedError as exc:
            steps.append(
                _failed_step(step_index, chain, tuple(
                    ProviderAttemptRecord(
                        provider=a.provider,
                        model=a.model,
                        attempt=a.attempt,
                        outcome=a.outcome,
                        error_class=a.error_class,
                        detail=a.detail,
                    )
                    for a in exc.attempts
                ))
            )
            termination = TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE
            termination_detail = str(exc)
            return finish()
        except ProviderConfigurationError as exc:
            termination = TERMINATION_PROVIDER_CONFIGURATION_FAILURE
            termination_detail = str(exc)
            return finish()
        except ProviderInfrastructureError as exc:  # single-provider chain
            termination = TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE
            termination_detail = str(exc)
            return finish()
        except ProviderError as exc:
            # A semantic failure raised by an adapter: no fallback, fail safe.
            termination = TERMINATION_TOOL_VALIDATION_FAILED
            termination_detail = str(exc)
            return finish()

        response = chain_result.response
        steps.append(
            ModelStepRecord(
                index=step_index,
                provider=response.provider,
                model=response.model,
                fallback_used=chain_result.provider_fallback_used,
                fallback_reason=chain_result.fallback_reason
                if chain_result.provider_fallback_used
                else None,
                transport_attempts=response.transport_attempts,
                attempts=_attempt_records(chain_result),
                latency_ms=response.latency_ms,
                usage=_usage_record(chain_result),
                finish_reason=response.finish_reason,
                assistant_text=response.text,
                requested_tool_calls=_requested(response.tool_calls),
            )
        )

        if not response.tool_calls:
            termination = TERMINATION_INVESTIGATION_COMPLETE
            termination_detail = None
            return finish()

        messages.append(
            ConversationMessage(
                role="assistant",
                content=response.text,
                tool_calls=response.tool_calls[: config.max_tool_calls_per_step],
            )
        )

        for call_index, call in enumerate(
            response.tool_calls[: config.max_tool_calls_per_step]
        ):
            started = time.perf_counter()
            try:
                arguments, output = execute(context, call.tool_name, call.raw_arguments)
            except ToolValidationError as exc:
                invocations.append(
                    ToolInvocationRecord(
                        step_index=step_index,
                        call_index=call_index,
                        tool_name=call.tool_name,
                        raw_arguments=call.raw_arguments,
                        validated_arguments=None,
                        validation_error_reason=exc.reason,
                        validation_error_detail=exc.detail,
                        output=None,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                )
                termination = TERMINATION_TOOL_VALIDATION_FAILED
                termination_detail = f"{exc.reason}: {exc.detail}"
                return finish()

            payload = output.model_dump(mode="json")
            invocations.append(
                ToolInvocationRecord(
                    step_index=step_index,
                    call_index=call_index,
                    tool_name=call.tool_name,
                    raw_arguments=call.raw_arguments,
                    validated_arguments=arguments.model_dump(mode="json"),
                    validation_error_reason=None,
                    validation_error_detail=None,
                    output=payload,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            )
            messages.append(
                ConversationMessage(
                    role="tool",
                    content=_tool_message_content(payload),
                    tool_call_id=call.call_id,
                    tool_name=call.tool_name,
                )
            )

    return finish()


def _failed_step(
    step_index: int, chain: ProviderChain, attempts: tuple[ProviderAttemptRecord, ...]
) -> ModelStepRecord:
    """A step that produced no model turn. Recorded so the failure is not invisible."""
    first = attempts[0] if attempts else None
    return ModelStepRecord(
        index=step_index,
        provider=first.provider if first else chain.providers[0].provider_id,
        model=first.model if first else chain.providers[0].model,
        fallback_used=False,
        fallback_reason=first.outcome if first else None,
        transport_attempts=len(attempts),
        attempts=attempts,
        latency_ms=None,
        usage=UsageRecord(),
        finish_reason=None,
        assistant_text="",
        requested_tool_calls=(),
    )


def _tool_message_content(payload: dict) -> str:
    """Canonical JSON, so the same evidence is fed back identically every run."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


__all__ = [
    "DEFAULT_MAX_STEPS",
    "MAX_TOOL_CALLS_PER_STEP",
    "LoopConfig",
    "run_investigation",
]
