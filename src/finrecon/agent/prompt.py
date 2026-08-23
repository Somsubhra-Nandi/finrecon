"""The versioned investigator prompt, and the deterministic case briefing.

Two things live here, and the second is more interesting than the first.

**The prompt** is short on purpose. It tells the model what it is (an
investigator), what it cannot do (resolve anything), what is fixed (the
candidate set), and that abstention is an acceptable outcome. It does not
contain a worked example, a scoring rubric, or a nudge toward finding a
match -- "always find a reference" is the single instruction most likely to
manufacture one. Every word is versioned by
:data:`finrecon.agent.version.PROMPT_VERSION` and feeds the trajectory cache
key, so editing this file invalidates exactly the cached trajectories it
should.

**The briefing** is built mechanically from the immutable snapshot, so the
same case always produces byte-identical prompt text. That is what makes
the cache key meaningful: if the briefing could drift, a cache hit would not
imply the model saw the same case.

What the briefing deliberately withholds
----------------------------------------

* **The tier.** T0/T1/T2/T3 exist only in hidden ground truth, and the
  production path never sees them. A prompt that said "this is a degraded
  reference case" would be leaking the answer's shape, and the benchmark
  would stop measuring anything.
* **Ground truth of any kind** -- the true reference, the correct
  settlement, the degradation category, the decoy's identity.
* **The candidates' reference values.** The briefing lists candidate IDs,
  their settlement IDs and their totals; the UTRs come from
  ``lookup_candidate_records``. Handing them over up front would collapse
  the investigation into one comparison call and make the multi-step
  question DESIGN.md 5.5 asks unanswerable.
"""

from __future__ import annotations

from finrecon.agent.version import PROMPT_VERSION
from finrecon.candidates.snapshot import CaseSnapshot

SYSTEM_PROMPT = """\
You are a reconciliation investigator inside a financial controller.

A deterministic engine has already tried and failed to settle this bank
credit. It has frozen the case: the complete set of plausible counterparty
candidates is fixed, was built before you were called, and is handed to the
decision layer independently of you.

Your job is to gather concrete evidence with the read-only tools provided,
and nothing else.

What you cannot do, structurally:
- You cannot resolve the case, choose a winner, or rank candidates.
- You cannot add, remove, hide or replace a candidate.
- You cannot write anything anywhere.
- Your prose is not read by the decision layer. Only raw tool outputs are.

How to work:
- Read the narration. If it appears to carry a damaged reference, identify
  the exact substring that survived and test it with
  compare_reference_fragment against each candidate.
- Copy fragments from the narration verbatim. Never repair, complete,
  un-mask or guess missing characters. A reconstructed reference is
  fabricated evidence and will be rejected.
- Use lookup_candidate_records, inspect_settlement_breakup and
  compute_expected_net when the case turns on amounts rather than
  references.
- Stop as soon as you have gathered the evidence that exists. Say briefly
  what you tested.

Ambiguity is an acceptable and expected outcome. Many of these cases have no
answer at all, and the correct result is that a human looks at them. If the
evidence does not distinguish the candidates, say so and stop. There is no
penalty for finding nothing, and no reward for producing a match.
"""


def system_prompt() -> str:
    return SYSTEM_PROMPT


def case_briefing(snapshot: CaseSnapshot) -> str:
    """Deterministic, tier-blind case text built only from the immutable snapshot."""
    bank = snapshot.base_evidence.bank_record

    lines = [
        "Unresolved bank credit.",
        "",
        f"case_id: {snapshot.case_id}",
        f"bank_record_id: {bank.bank_record_id}",
        f"amount_paise: {bank.amount_paise}",
        f"direction: {bank.direction.value}",
        f"value_date: {bank.value_date.isoformat()}",
        f"narration: {bank.narration}",
        f"narration_tokens: {list(bank.reference_tokens)}",
        "",
        (
            "The deterministic engine stopped under rule "
            f"{snapshot.unresolved_rule_id!r} ({snapshot.unresolved_matcher_id})."
        ),
        "",
        f"Candidates ({len(snapshot.candidates)}). This set is complete and fixed:",
    ]
    for candidate in snapshot.candidates:
        lines.append(
            f"  - candidate_id: {candidate.candidate_id}\n"
            f"    settlement_ids: {list(candidate.settlement_ids)}\n"
            f"    total_paise: {candidate.total_paise}\n"
            f"    unexplained_delta_paise: {candidate.unexplained_delta_paise}\n"
            f"    blocking_rule: {candidate.blocking_rule}"
        )
    lines += [
        "",
        "Investigate. Use only these candidate_ids and the settlement_ids they name.",
    ]
    return "\n".join(lines)


def prompt_fingerprint() -> str:
    """What the cache key records about the prompt: its version, not its bytes.

    The version is bumped by hand when the text changes, which is a
    deliberate choice over hashing the string: an incidental whitespace edit
    should not silently invalidate a fixture corpus, and a substantive edit
    should be an explicit act recorded in the version constant.
    """
    return PROMPT_VERSION


__all__ = ["SYSTEM_PROMPT", "case_briefing", "prompt_fingerprint", "system_prompt"]
