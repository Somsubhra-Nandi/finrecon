"""The schema-proposal prompt. One bounded task, deliberately not an investigation.

This prompt is separate from :mod:`finrecon.agent.prompt` and shares nothing
with it. Stage 3 investigates a reconciliation case against an immutable
snapshot, has a tool budget, and produces a decision the validator and policy
gate adjudicate. This asks a single closed question about a table's column
*names* and returns before anything financial exists. Reusing the Stage-3
system prompt or loop here would make a schema question look like a
reconciliation trajectory in the audit trail, and would drag a decision
vocabulary ("RESOLVE", "candidate", "evidence") into a task that must not
have one.

The prompt states the target schema, states what the model may not do, and
tells it to prefer saying "uncertain" over guessing. That last instruction is
not a safety decoration: the day/month ambiguity is genuinely unresolvable
from data, and a model that answers anyway produces a confident wrong reading
of every date in the file.
"""

from __future__ import annotations

from .formats import FORMAT_LABELS, SUPPORTED_VALUE_DATE_FORMATS
from .proposal import PROPOSAL_TOOL_NAME
from .sample import BankCsvSample

PROMPT_VERSION = "bank-mapping-proposal-prompt-v1"

SYSTEM_PROMPT = f"""You map the columns of a bank statement CSV onto FinRecon's \
canonical transaction schema. You are given the header row and a few sample \
data rows. Nothing else about the statement is available to you, and nothing \
else is needed.

Your entire output is one call to `{PROPOSAL_TOOL_NAME}`. Do not write prose.

FinRecon's canonical bank transaction needs exactly these things:

1. value_date  - the date the transaction is valued, and the strptime format \
that reads that column exactly.
2. narration   - the free-text description the bank wrote for the transaction.
3. reference   - the bank's own per-transaction reference / UTR / cheque number, \
if the table has such a column. Null if it does not. Do not nominate a row \
number, a serial number, a running balance or an account number as a reference.
4. money       - the amount and its direction, under one of exactly two models:
   - `debit_credit`: two separate amount columns. You must also say whether the \
side a row does NOT use is left blank (`empty_only`) or filled with zero \
(`empty_or_zero`). Look at the sample rows: if a row shows an amount in one \
column and `0`/`0.00` in the other, the answer is `empty_or_zero`.
   - `amount_direction`: one amount column plus a separate column carrying a \
direction marker (e.g. `DR`/`CR`). List the exact marker strings you can see.

Supported value-date formats, and nothing else:
{chr(10).join(f"  {fmt}   {label}" for fmt, label in FORMAT_LABELS.items() if fmt in SUPPORTED_VALUE_DATE_FORMATS)}

Rules you must follow:

- Only name columns that appear verbatim in the header row you are given.
- Never infer direction from an amount's sign or size.
- If every sampled date has both of its first two numbers at 12 or below, then \
day-first and month-first are indistinguishable from this data. Say \
`value_date_format_certain: false` and list it in `uncertainties`. Do not guess \
from what bank you think wrote the file.
- Prefer `value_date_format_certain: false` over a confident wrong answer \
anywhere the sample does not settle the question.
- `reasoning_summary` is one short sentence per field naming the evidence you \
actually used. It is shown to a person who will review and correct your \
proposal. Do not include step-by-step deliberation.

Your proposal is a suggestion. A person reviews every field, edits whatever is \
wrong, and only their confirmation makes a mapping real. Nothing you output \
reconciles anything or is saved on its own."""


def user_message(sample: BankCsvSample) -> str:
    """The bounded excerpt, rendered as delimited text.

    Rendered rather than JSON-encoded so a cell containing a quote or a comma
    cannot be mistaken for structure. The row index is 1-based and labelled,
    because a model that can see there are only five rows is less likely to
    make claims about the statement as a whole.
    """
    lines = [
        f"HEADER ROW ({len(sample.raw_headers)} columns), in order:",
    ]
    for index, header in enumerate(sample.raw_headers, start=1):
        lines.append(f"  [{index}] {header!r}")
    lines.append("")
    if sample.rows:
        lines.append(
            f"FIRST {len(sample.rows)} DATA ROW(S) of this file (a bounded sample, "
            "not the whole statement):"
        )
        for row_index, row in enumerate(sample.rows, start=1):
            lines.append(f"  row {row_index}:")
            for header, cell in zip(sample.raw_headers, row):
                lines.append(f"    {header!r} = {cell!r}")
    else:
        lines.append(
            "NO DATA ROWS were available to sample. Judge from the header row "
            "alone and mark every field you cannot verify as uncertain."
        )
    lines.append("")
    lines.append(f"Call {PROPOSAL_TOOL_NAME} once with your proposed mapping.")
    return "\n".join(lines)


def repair_message(reasons: tuple[str, ...]) -> str:
    """The single bounded retry's correction message.

    States what the deterministic validator rejected and asks for one more
    call. It carries the *validator's* findings and no new instruction, so
    the retry is a chance to satisfy the declared contract rather than an
    invitation to negotiate it.
    """
    listed = "\n".join(f"  - {reason}" for reason in reasons)
    return (
        "Your proposal was rejected by FinRecon's deterministic checks:\n"
        f"{listed}\n\n"
        "Call the tool exactly once more with a corrected proposal. Use only "
        "columns from the header row above. If you cannot determine a field "
        "from the sample, mark it uncertain rather than guessing."
    )


__all__ = ["PROMPT_VERSION", "SYSTEM_PROMPT", "repair_message", "user_message"]
