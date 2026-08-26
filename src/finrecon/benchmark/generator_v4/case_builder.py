"""Per-archetype v4 case construction.

Each drafter below decides a case's *values* -- references, amounts, dates,
narration -- without touching the shared record-ID sequence. The draft is then
materialized twice: once against a scratch
:class:`~finrecon.benchmark.generator.record_factory.RecordFactory` so the
case can be verified before it costs anything, and again against the real one
only if it passed. That split is what makes a bounded, deterministic redraw
possible: a case that fails its own invariants is drawn again from a derived
seed rather than leaving a gap in the identifier space or, worse, being
accepted.

The redraw is bounded and declared (:data:`MAX_DRAFT_ATTEMPTS`). An exhausted
budget raises; it never falls back to a weaker case. Benchmark v3 uses the
same idiom for its decoy-UTR draw, and for the same reason -- an open-ended
search that silently relaxes what it is searching for is how a benchmark
stops testing what it says it tests.

What every archetype shares
---------------------------

Every candidate in a v4 case settles to the *same* net on a date inside the
declared value-date window, each with its own order, captured payment and
exactly-accounting break-up. So Stage 2's amount and date blocking can never
separate them, its direct-key matcher finds no whole-token reference, and
every case arrives at the investigation layer with its full candidate set
intact. What differs between archetypes is only what evidence the narration
carries, and how much of it any one fragment is worth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from random import Random

from finrecon.models import OrderStatus

from finrecon.benchmark.generator.assertions import CaseRecords
from finrecon.benchmark.generator.record_factory import (
    RecordFactory,
    case_base_timestamp,
    fee_line,
    payment_line,
    refund_line,
    tax_line,
    transfer_line,
)
from finrecon.benchmark.generator.seeding import derive_seed
from finrecon.benchmark.generator_v4.config import GROSS_MAX, GROSS_MIN, V4_PILOT_SLUG
from finrecon.benchmark.generator_v4.families import archetype_spec
from finrecon.benchmark.generator_v4.ground_truth import (
    ClueRecord,
    StructuralDiscriminator,
    V4GroundTruthCase,
    V4Relationship,
)
from finrecon.benchmark.generator_v4.invariants import (
    CaseExpectation,
    CaseVerification,
    PlausibilityInputs,
    V4ConstructError,
    settlements_on_date,
    settlements_with_breakup_amount,
    verify_case,
)
from finrecon.benchmark.generator_v4.money import (
    BreakupPlan,
    no_zero_amount_paise,
    plan_breakup,
)
from finrecon.benchmark.generator_v4.narration import (
    REFERENCELESS_NARRATIONS,
    batch_token,
    date_token,
    money_token,
    render,
    template_ids_for,
)
from finrecon.benchmark.generator_v4.references import (
    BANK_CODES,
    CHANNEL_CODES,
    ReferenceConstructionError,
    StructuredUtr,
    anagram_of,
    distinct_bank,
    distinct_digit_pair,
    distinct_tail,
    make_utr,
    non_anagram_mid,
)

MAX_DRAFT_ATTEMPTS = 48
"""Declared redraw budget per case. Exhaustion raises rather than degrading."""

_SPLIT_SLOTS = ("head", "filler", "tail")
_TRIPLE_SLOTS = ("reordered", "filler", "head", "tail")
_PREFIX_SLOTS = ("prefix", "decoys")
_AMOUNT_SLOTS = ("head", "filler", "money")
_DATED_SLOTS = ("head", "filler", "vdate")

_LONG_PREFIX_LENGTH = 14
"""Head (8) + mid (2) + four tail digits. Long enough that no head-sharing decoy
survives it, short enough that the reference is still genuinely truncated."""


# --- drafts ----------------------------------------------------------------


@dataclass(frozen=True)
class ChainDraft:
    """One candidate: its reference, its arithmetic and when it settled."""

    role: str
    utr: str
    net_paise: int
    refund_paise: int
    transfer_paise: int
    settlement_date: date
    settlement_minute: int
    payment_offset_seconds: int
    refund_offset_hours: int


@dataclass(frozen=True)
class CaseDraft:
    """A complete case, decided but not yet given identifiers."""

    archetype: str
    chains: tuple[ChainDraft, ...]
    true_index: int | None
    narration: str
    narration_template_id: str
    bank_value_date: date
    base_ts: datetime
    net_paise: int
    clues: tuple[tuple[str, str], ...]
    """``(fragment, clue_kind)`` for every clue the construction relies on."""
    single_fragment_indices: tuple[int, ...]
    minimal_arity: int | None
    structural_kind: str | None
    structural_value: str | None
    structural_token: str | None
    structural_singleton_indices: tuple[int, ...]


@dataclass(frozen=True)
class CaseBundle:
    case_id: str
    archetype: str
    records: CaseRecords
    ground_truth: V4GroundTruthCase
    verification: CaseVerification
    expectation: CaseExpectation
    """Carried forward so the batch-wide pass re-checks the *same* claim.

    Case-local verification runs against a case's own records; the split-wide
    pass runs against every record in the split, where another case's
    settlement can have landed in the value-date window. Re-checking with a
    freshly invented expectation would let the second pass quietly agree with
    whatever the wider pool produced.
    """


# --- shared drafting helpers -----------------------------------------------


def _net(rng: Random) -> int:
    """A shared net for every candidate in the case, inside benchmark v3's band."""
    return rng.randint(GROSS_MIN, GROSS_MAX)


def _decoy_transfer(rng: Random, net_paise: int) -> int:
    """A sub-account transfer deduction, sized as benchmark v3's T1 archetype sizes one."""
    return rng.randint(1_00, max(2_00, net_paise // 10))


def _chain(
    rng: Random,
    *,
    role: str,
    utr: StructuredUtr | str,
    net_paise: int,
    settlement_date: date,
    refund_paise: int = 0,
    transfer_paise: int = 0,
) -> ChainDraft:
    return ChainDraft(
        role=role,
        utr=utr.value if isinstance(utr, StructuredUtr) else utr,
        net_paise=net_paise,
        refund_paise=refund_paise,
        transfer_paise=transfer_paise,
        settlement_date=settlement_date,
        settlement_minute=rng.randrange(1440),
        payment_offset_seconds=rng.randint(1, 600),
        refund_offset_hours=rng.randint(1, 48),
    )


def _pick_template(rng: Random, slots: tuple[str, ...]) -> str:
    return rng.choice(template_ids_for(slots))


def _decoy_field(rng: Random, stale_partial: str) -> str:
    """Individually compelling, mechanically insufficient narration noise.

    Three of the decoy shapes the spec names, in one field: a partial
    reference belonging to a *different* candidate but three characters long,
    which is below the declared evidence floor; a settlement-batch token
    repeated as bank exports repeat it; and a generic gateway wrapper that
    prefixes every settlement identifier at once and therefore separates
    nothing.
    """
    batch = batch_token(rng)
    return f"REV{stale_partial} {batch} PGSETL {batch}"


def _settlement_day(base_ts: datetime, rng: Random) -> date:
    return (base_ts + timedelta(days=rng.randint(2, 4))).date()


# --- archetype drafters ----------------------------------------------------


def _draft_single_fragment_control(rng: Random, candidate_count: int) -> CaseDraft:
    base_ts = case_base_timestamp(rng)
    day = _settlement_day(base_ts, rng)
    net = _net(rng)

    true_ref = make_utr(rng)
    banks = {true_ref.bank}
    head_sharer = StructuredUtr(
        bank=true_ref.bank,
        channel=true_ref.channel,
        head_digits=true_ref.head_digits,
        mid=non_anagram_mid(rng, true_ref.mid),
        tail=distinct_tail(rng, {true_ref.tail}),
    )
    unreached = _unreached_reference(rng, banks, {true_ref.tail, head_sharer.tail})

    chains = [
        _chain(rng, role="true", utr=true_ref, net_paise=net, settlement_date=day),
        _chain(
            rng,
            role="decoy_head",
            utr=head_sharer,
            net_paise=net,
            settlement_date=day,
            transfer_paise=_decoy_transfer(rng, net),
        ),
        _chain(
            rng,
            role="decoy_unreached",
            utr=unreached,
            net_paise=net,
            settlement_date=day,
            transfer_paise=_decoy_transfer(rng, net),
        ),
    ]
    order = list(range(len(chains)))
    rng.shuffle(order)
    chains = [chains[i] for i in order]
    true_index = order.index(0)

    prefix = true_ref.long_prefix(_LONG_PREFIX_LENGTH)
    template_id = _pick_template(rng, _PREFIX_SLOTS)
    narration = render(
        template_id, prefix=prefix, decoys=_decoy_field(rng, unreached.tail[-3:])
    )

    return CaseDraft(
        archetype="single_fragment_control",
        chains=tuple(chains),
        true_index=true_index,
        narration=narration,
        narration_template_id=template_id,
        bank_value_date=day + timedelta(days=rng.randint(0, 1)),
        base_ts=base_ts,
        net_paise=net,
        clues=((prefix, "long_prefix"),),
        single_fragment_indices=(true_index,),
        minimal_arity=1,
        structural_kind=None,
        structural_value=None,
        structural_token=None,
        structural_singleton_indices=(),
    )


def _unreached_reference(
    rng: Random, banks_used: set[str], tails_used: set[str]
) -> StructuredUtr:
    """A reference no clue in this case can reach: new bank, distinct tail."""
    bank = distinct_bank(rng, banks_used)
    banks_used.add(bank)
    reference = StructuredUtr(
        bank=bank,
        channel=rng.choice(CHANNEL_CODES),
        head_digits="".join(rng.choice("123456789") for _ in range(2)),
        mid="".join(rng.choice("123456789") for _ in range(2)),
        tail=distinct_tail(rng, tails_used),
    )
    tails_used.add(reference.tail)
    return reference


def _draft_conjunction(rng: Random, candidate_count: int) -> CaseDraft:
    """Head clue and tail clue, each shared with a different decoy.

    The reference survives as a truncated head in one narration field and a
    truncated tail in another. One decoy shares the head (a same-bank,
    same-batch settlement); another shares the tail (a coincidental trailing
    run from a different bank). Neither clue alone separates the set.
    """
    if candidate_count < 3:
        raise ValueError("a conjunction case needs at least three candidates")

    base_ts = case_base_timestamp(rng)
    day = _settlement_day(base_ts, rng)
    net = _net(rng)

    true_ref = make_utr(rng)
    banks = {true_ref.bank}
    tails = {true_ref.tail}

    head_sharer_count = 2 if candidate_count >= 4 else 1
    unreached_count = candidate_count - 1 - head_sharer_count - 1

    head_sharers: list[StructuredUtr] = []
    used_mids = {true_ref.mid}
    for _ in range(head_sharer_count):
        reference = StructuredUtr(
            bank=true_ref.bank,
            channel=true_ref.channel,
            head_digits=true_ref.head_digits,
            mid=distinct_digit_pair(rng, used_mids),
            tail=distinct_tail(rng, tails),
        )
        used_mids.add(reference.mid)
        tails.add(reference.tail)
        head_sharers.append(reference)

    tail_bank = distinct_bank(rng, banks)
    banks.add(tail_bank)
    tail_sharer = StructuredUtr(
        bank=tail_bank,
        channel=rng.choice(CHANNEL_CODES),
        head_digits="".join(rng.choice("123456789") for _ in range(2)),
        mid="".join(rng.choice("123456789") for _ in range(2)),
        tail=true_ref.tail,
    )

    references = [("true", true_ref)]
    references += [("decoy_head", reference) for reference in head_sharers]
    references.append(("decoy_tail", tail_sharer))
    for _ in range(unreached_count):
        references.append(("decoy_unreached", _unreached_reference(rng, banks, tails)))

    chains = [
        _chain(
            rng,
            role=role,
            utr=reference,
            net_paise=net,
            settlement_date=day,
            transfer_paise=0 if role == "true" else _decoy_transfer(rng, net),
        )
        for role, reference in references
    ]
    order = list(range(len(chains)))
    rng.shuffle(order)
    chains = [chains[i] for i in order]
    true_index = order.index(0)

    template_id = _pick_template(rng, _SPLIT_SLOTS)
    narration = render(
        template_id,
        head=true_ref.head,
        filler=batch_token(rng),
        tail=true_ref.tail,
    )

    spec = archetype_spec(
        "conjunction_pair" if candidate_count == 3 else "conjunction_wide"
    )
    return CaseDraft(
        archetype=spec.archetype,
        chains=tuple(chains),
        true_index=true_index,
        narration=narration,
        narration_template_id=template_id,
        bank_value_date=day + timedelta(days=rng.randint(0, 1)),
        base_ts=base_ts,
        net_paise=net,
        clues=((true_ref.head, "head_prefix"), (true_ref.tail, "tail_suffix")),
        single_fragment_indices=(),
        minimal_arity=2,
        structural_kind=None,
        structural_value=None,
        structural_token=None,
        structural_singleton_indices=(),
    )


def _draft_conjunction_triple(rng: Random, candidate_count: int) -> CaseDraft:
    """Head, tail and a digit-reordered rendering; every pair leaves two candidates."""
    base_ts = case_base_timestamp(rng)
    day = _settlement_day(base_ts, rng)
    net = _net(rng)

    true_ref = make_utr(rng)
    head_and_tail = StructuredUtr(
        bank=true_ref.bank,
        channel=true_ref.channel,
        head_digits=true_ref.head_digits,
        mid=non_anagram_mid(rng, true_ref.mid),
        tail=true_ref.tail,
    )
    head_and_anagram = anagram_of(rng, true_ref, keep_head=True, keep_tail=False)
    tail_and_anagram = anagram_of(rng, true_ref, keep_head=False, keep_tail=True)

    banks = {true_ref.bank}
    tails = {
        true_ref.tail,
        head_and_tail.tail,
        head_and_anagram.tail,
        tail_and_anagram.tail,
    }
    unreached = _unreached_reference(rng, banks, tails)

    references = [
        ("true", true_ref),
        ("decoy_head_tail", head_and_tail),
        ("decoy_head_anagram", head_and_anagram),
        ("decoy_tail_anagram", tail_and_anagram),
        ("decoy_unreached", unreached),
    ]
    reordered = true_ref.reordered(
        rng, avoid=frozenset(reference.value for _role, reference in references)
    )

    chains = [
        _chain(
            rng,
            role=role,
            utr=reference,
            net_paise=net,
            settlement_date=day,
            transfer_paise=0 if role == "true" else _decoy_transfer(rng, net),
        )
        for role, reference in references
    ]
    order = list(range(len(chains)))
    rng.shuffle(order)
    chains = [chains[i] for i in order]
    true_index = order.index(0)

    template_id = _pick_template(rng, _TRIPLE_SLOTS)
    narration = render(
        template_id,
        reordered=reordered,
        filler=batch_token(rng),
        head=true_ref.head,
        tail=true_ref.tail,
    )

    return CaseDraft(
        archetype="conjunction_triple",
        chains=tuple(chains),
        true_index=true_index,
        narration=narration,
        narration_template_id=template_id,
        bank_value_date=day + timedelta(days=rng.randint(0, 1)),
        base_ts=base_ts,
        net_paise=net,
        clues=(
            (true_ref.head, "head_prefix"),
            (true_ref.tail, "tail_suffix"),
            (reordered, "reordered"),
        ),
        single_fragment_indices=(),
        minimal_arity=3,
        structural_kind=None,
        structural_value=None,
        structural_token=None,
        structural_singleton_indices=(),
    )


def _draft_amount_reference_hop(rng: Random, candidate_count: int) -> CaseDraft:
    """A head clue and a refund amount, each reaching two candidates.

    The narration names the refund that was netted off inside the settlement.
    Two candidates carry a break-up line of that magnitude; two candidates
    share the reference head; one candidate is in both sets. Reaching it means
    going narration -> amount -> break-up line -> refund record -> settlement,
    which no comparison between a substring and a reference can do.
    """
    base_ts = case_base_timestamp(rng)
    day = _settlement_day(base_ts, rng)
    net = _net(rng)

    true_ref = make_utr(rng)
    banks = {true_ref.bank}
    tails = {true_ref.tail}
    head_sharer = StructuredUtr(
        bank=true_ref.bank,
        channel=true_ref.channel,
        head_digits=true_ref.head_digits,
        mid=non_anagram_mid(rng, true_ref.mid),
        tail=distinct_tail(rng, tails),
    )
    tails.add(head_sharer.tail)
    amount_sharer = _unreached_reference(rng, banks, tails)

    named_refund = _draw_named_refund(rng, (true_ref, head_sharer, amount_sharer))
    other_refund = _draw_named_refund(
        rng, (true_ref, head_sharer, amount_sharer), avoid={named_refund}
    )

    references = [
        ("true", true_ref, named_refund, 0),
        ("decoy_head", head_sharer, other_refund, 0),
        ("decoy_amount", amount_sharer, named_refund, _decoy_transfer(rng, net)),
    ]
    chains = [
        _chain(
            rng,
            role=role,
            utr=reference,
            net_paise=net,
            settlement_date=day,
            refund_paise=refund,
            transfer_paise=transfer,
        )
        for role, reference, refund, transfer in references
    ]
    order = list(range(len(chains)))
    rng.shuffle(order)
    chains = [chains[i] for i in order]
    true_index = order.index(0)
    amount_index = order.index(2)

    template_id = _pick_template(rng, _AMOUNT_SLOTS)
    token = money_token(named_refund)
    narration = render(
        template_id, head=true_ref.head, filler=batch_token(rng), money=token
    )

    return CaseDraft(
        archetype="amount_reference_hop",
        chains=tuple(chains),
        true_index=true_index,
        narration=narration,
        narration_template_id=template_id,
        bank_value_date=day + timedelta(days=rng.randint(0, 1)),
        base_ts=base_ts,
        net_paise=net,
        clues=((true_ref.head, "head_prefix"),),
        single_fragment_indices=(),
        minimal_arity=None,
        structural_kind="breakup_line_amount_paise",
        structural_value=str(named_refund),
        structural_token=token,
        structural_singleton_indices=(true_index,),
    )


def _draw_named_refund(
    rng: Random, references: tuple[StructuredUtr, ...], avoid: set[int] | None = None
) -> int:
    """A four-digit paise amount no reference in this case ends with.

    A money field whose digits happened to be some candidate's trailing run
    would stand in a ``suffix_of_reference`` relation to it, turning the
    amount clue into a lexical one and quietly changing what the case tests.
    """
    avoid = avoid or set()
    for _ in range(128):
        amount = no_zero_amount_paise(rng)
        if amount in avoid:
            continue
        digits = str(amount)
        if any(reference.value.endswith(digits) for reference in references):
            continue
        return amount
    raise ReferenceConstructionError(
        "could not draw a refund amount whose digits are not a reference suffix"
    )


def _draft_conflict_context_resolves(rng: Random, candidate_count: int) -> CaseDraft:
    """A head clue reaching two candidates, and a value-date field reaching two others."""
    base_ts = case_base_timestamp(rng)
    day = _settlement_day(base_ts, rng)
    net = _net(rng)

    true_ref = make_utr(rng)
    banks = {true_ref.bank}
    tails = {true_ref.tail}
    head_sharer = StructuredUtr(
        bank=true_ref.bank,
        channel=true_ref.channel,
        head_digits=true_ref.head_digits,
        mid=non_anagram_mid(rng, true_ref.mid),
        tail=distinct_tail(rng, tails),
    )
    tails.add(head_sharer.tail)
    date_sharer = _unreached_reference(rng, banks, tails)

    references = [
        ("true", true_ref, day),
        ("decoy_head_wrong_date", head_sharer, day - timedelta(days=1)),
        ("decoy_date_no_reference", date_sharer, day),
    ]
    chains = [
        _chain(
            rng,
            role=role,
            utr=reference,
            net_paise=net,
            settlement_date=settlement_date,
            transfer_paise=0 if role == "true" else _decoy_transfer(rng, net),
        )
        for role, reference, settlement_date in references
    ]
    order = list(range(len(chains)))
    rng.shuffle(order)
    chains = [chains[i] for i in order]
    true_index = order.index(0)

    template_id = _pick_template(rng, _DATED_SLOTS)
    token = date_token(day)
    narration = render(
        template_id, head=true_ref.head, filler=batch_token(rng), vdate=token
    )

    return CaseDraft(
        archetype="conflict_context_resolves",
        chains=tuple(chains),
        true_index=true_index,
        narration=narration,
        narration_template_id=template_id,
        bank_value_date=day,
        base_ts=base_ts,
        net_paise=net,
        clues=((true_ref.head, "head_prefix"),),
        single_fragment_indices=(),
        minimal_arity=None,
        structural_kind="settlement_value_date",
        structural_value=day.isoformat(),
        structural_token=token,
        structural_singleton_indices=(true_index,),
    )


def _draft_conflict_stale_reference(rng: Random, candidate_count: int) -> CaseDraft:
    """A stale reference tail pointing one way, a value-date field pointing another.

    Nothing is consistent with all the evidence, so the correct outcome is
    escalation -- and a strategy that resolves on the first fragment to
    separate the candidate set resolves it, wrongly. That is the point of the
    archetype: it is the only one here that can produce an unsafe auto-match.
    """
    base_ts = case_base_timestamp(rng)
    day = _settlement_day(base_ts, rng)
    net = _net(rng)

    banks: set[str] = set()
    tails: set[str] = set()
    stale = _unreached_reference(rng, banks, tails)
    on_date_a = _unreached_reference(rng, banks, tails)
    on_date_b = _unreached_reference(rng, banks, tails)

    references = [
        ("decoy_stale_reference", stale, day - timedelta(days=1)),
        ("decoy_on_value_date", on_date_a, day),
        ("decoy_on_value_date", on_date_b, day),
    ]
    chains = [
        _chain(
            rng,
            role=role,
            utr=reference,
            net_paise=net,
            settlement_date=settlement_date,
            transfer_paise=_decoy_transfer(rng, net),
        )
        for role, reference, settlement_date in references
    ]
    order = list(range(len(chains)))
    rng.shuffle(order)
    chains = [chains[i] for i in order]
    stale_index = order.index(0)

    template_id = _pick_template(rng, _DATED_SLOTS)
    token = date_token(day)
    narration = render(
        template_id, head=stale.tail, filler=batch_token(rng), vdate=token
    )

    return CaseDraft(
        archetype="conflict_stale_reference",
        chains=tuple(chains),
        true_index=None,
        narration=narration,
        narration_template_id=template_id,
        bank_value_date=day,
        base_ts=base_ts,
        net_paise=net,
        clues=((stale.tail, "stale_tail"),),
        single_fragment_indices=(stale_index,),
        minimal_arity=1,
        structural_kind="settlement_value_date",
        structural_value=day.isoformat(),
        structural_token=token,
        structural_singleton_indices=(),
    )


def _draft_ambiguity_no_discriminator(rng: Random, candidate_count: int) -> CaseDraft:
    """benchmark v3's T3, widened: same amount, same instant, nothing to recover."""
    base_ts = case_base_timestamp(rng)
    day = _settlement_day(base_ts, rng)
    net = _net(rng)
    minute = rng.randrange(1440)

    banks: set[str] = set()
    tails: set[str] = set()
    chains = []
    for _ in range(candidate_count):
        reference = _unreached_reference(rng, banks, tails)
        draft = _chain(
            rng,
            role="ambiguous",
            utr=reference,
            net_paise=net,
            settlement_date=day,
            transfer_paise=_decoy_transfer(rng, net),
        )
        # Identical timestamps, exactly as benchmark v3's T3 construct pins
        # them: candidates that differ in no observable structural fact.
        chains.append(
            ChainDraft(
                role=draft.role,
                utr=draft.utr,
                net_paise=draft.net_paise,
                refund_paise=draft.refund_paise,
                transfer_paise=draft.transfer_paise,
                settlement_date=draft.settlement_date,
                settlement_minute=minute,
                payment_offset_seconds=draft.payment_offset_seconds,
                refund_offset_hours=draft.refund_offset_hours,
            )
        )

    narration = rng.choice(REFERENCELESS_NARRATIONS)
    return CaseDraft(
        archetype="ambiguity_no_discriminator",
        chains=tuple(chains),
        true_index=None,
        narration=narration,
        narration_template_id="frozen:referenceless",
        bank_value_date=day + timedelta(days=rng.randint(0, 1)),
        base_ts=base_ts,
        net_paise=net,
        clues=(),
        single_fragment_indices=(),
        minimal_arity=None,
        structural_kind=None,
        structural_value=None,
        structural_token=None,
        structural_singleton_indices=(),
    )


def _draft_ambiguity_conjunction_incomplete(
    rng: Random, candidate_count: int
) -> CaseDraft:
    """A conjunction that does not close: the two clues intersect in two candidates."""
    base_ts = case_base_timestamp(rng)
    day = _settlement_day(base_ts, rng)
    net = _net(rng)

    bank = rng.choice(BANK_CODES)
    channel = rng.choice(CHANNEL_CODES)
    head_digits = "".join(rng.choice("123456789") for _ in range(2))
    shared_tail = "".join(rng.choice("123456789") for _ in range(6))

    used_mids: set[str] = set()

    def _mid() -> str:
        value = distinct_digit_pair(rng, used_mids)
        used_mids.add(value)
        return value

    head_only = StructuredUtr(
        bank=bank,
        channel=channel,
        head_digits=head_digits,
        mid=_mid(),
        tail=distinct_tail(rng, {shared_tail}),
    )
    both_a = StructuredUtr(
        bank=bank, channel=channel, head_digits=head_digits, mid=_mid(), tail=shared_tail
    )
    both_b = StructuredUtr(
        bank=bank, channel=channel, head_digits=head_digits, mid=_mid(), tail=shared_tail
    )
    banks = {bank}
    tails = {shared_tail, head_only.tail}
    unreached = _unreached_reference(rng, banks, tails)

    references = [
        ("ambiguous_head_only", head_only),
        ("ambiguous_head_and_tail", both_a),
        ("ambiguous_head_and_tail", both_b),
        ("decoy_unreached", unreached),
    ]
    chains = [
        _chain(
            rng,
            role=role,
            utr=reference,
            net_paise=net,
            settlement_date=day,
            transfer_paise=_decoy_transfer(rng, net),
        )
        for role, reference in references
    ]
    order = list(range(len(chains)))
    rng.shuffle(order)
    chains = [chains[i] for i in order]

    template_id = _pick_template(rng, _SPLIT_SLOTS)
    head_field = f"{bank}{channel}{head_digits}"
    narration = render(
        template_id, head=head_field, filler=batch_token(rng), tail=shared_tail
    )

    return CaseDraft(
        archetype="ambiguity_conjunction_incomplete",
        chains=tuple(chains),
        true_index=None,
        narration=narration,
        narration_template_id=template_id,
        bank_value_date=day + timedelta(days=rng.randint(0, 1)),
        base_ts=base_ts,
        net_paise=net,
        clues=((head_field, "head_prefix"), (shared_tail, "tail_suffix")),
        single_fragment_indices=(),
        minimal_arity=None,
        structural_kind=None,
        structural_value=None,
        structural_token=None,
        structural_singleton_indices=(),
    )


DRAFTERS = {
    "single_fragment_control": _draft_single_fragment_control,
    "conjunction_pair": _draft_conjunction,
    "conjunction_wide": _draft_conjunction,
    "conjunction_triple": _draft_conjunction_triple,
    "amount_reference_hop": _draft_amount_reference_hop,
    "conflict_stale_reference": _draft_conflict_stale_reference,
    "conflict_context_resolves": _draft_conflict_context_resolves,
    "ambiguity_no_discriminator": _draft_ambiguity_no_discriminator,
    "ambiguity_conjunction_incomplete": _draft_ambiguity_conjunction_incomplete,
}


# --- materialization -------------------------------------------------------


def _materialize(draft: CaseDraft, factory: RecordFactory) -> CaseRecords:
    """Turn a draft into canonical records. Pure in ``(draft, factory state)``."""
    orders = []
    payments = []
    refunds = []
    settlements = []

    for chain in draft.chains:
        plan: BreakupPlan = plan_breakup(
            net_paise=chain.net_paise,
            refund_paise=chain.refund_paise,
            transfer_paise=chain.transfer_paise,
        )
        order = factory.make_order(
            amount=plan.gross_paise, created_at=draft.base_ts, status=OrderStatus.PAID
        )
        payment_ts = draft.base_ts + timedelta(seconds=chain.payment_offset_seconds)
        payment = factory.make_payment(
            order_id=order.order_id, amount=plan.gross_paise, created_at=payment_ts
        )
        lines = [
            payment_line(plan.gross_paise, payment.payment_id),
            fee_line(plan.fee_paise),
            tax_line(plan.gst_paise),
        ]
        if plan.refund_paise:
            refund = factory.make_refund(
                payment_id=payment.payment_id,
                amount=plan.refund_paise,
                created_at=payment_ts + timedelta(hours=chain.refund_offset_hours),
            )
            refunds.append(refund)
            lines.append(refund_line(plan.refund_paise, refund.refund_id))
        if plan.transfer_paise:
            lines.append(transfer_line(-plan.transfer_paise))

        settlement_ts = datetime.combine(chain.settlement_date, time.min) + timedelta(
            minutes=chain.settlement_minute
        )
        settlements.append(
            factory.make_settlement(
                utr=chain.utr,
                net_amount=chain.net_paise,
                created_at=settlement_ts,
                breakup=tuple(lines),
            )
        )
        orders.append(order)
        payments.append(payment)

    bank_record = factory.make_bank_record(
        amount=draft.net_paise,
        narration=draft.narration,
        value_date=draft.bank_value_date,
    )
    return CaseRecords(
        orders=tuple(orders),
        payments=tuple(payments),
        settlements=tuple(settlements),
        refunds=tuple(refunds),
        bank_records=(bank_record,),
    )


def _pool(records: CaseRecords) -> PlausibilityInputs:
    return PlausibilityInputs(
        settlements=records.settlements,
        payments=records.payments,
        refunds=records.refunds,
    )


def _structural_reach(draft: CaseDraft, records: CaseRecords) -> frozenset[str] | None:
    """The non-lexical feature's reach, computed from the records, never declared."""
    if draft.structural_kind is None:
        return None
    if draft.structural_kind == "breakup_line_amount_paise":
        return settlements_with_breakup_amount(
            records.settlements, int(draft.structural_value or 0)
        )
    if draft.structural_kind == "settlement_value_date":
        return settlements_on_date(
            records.settlements, date.fromisoformat(draft.structural_value or "")
        )
    raise V4ConstructError(f"unknown structural feature kind {draft.structural_kind!r}")


def _expectation(draft: CaseDraft, records: CaseRecords) -> CaseExpectation:
    ids = tuple(s.settlement_id for s in records.settlements)
    return CaseExpectation(
        archetype=draft.archetype,
        expected_candidate_count=len(draft.chains),
        true_settlement_id=None if draft.true_index is None else ids[draft.true_index],
        single_fragment_identifications=frozenset(
            ids[index] for index in draft.single_fragment_indices
        ),
        minimal_lexical_arity=draft.minimal_arity,
        structural_reach=_structural_reach(draft, records),
        structural_singletons=frozenset(
            ids[index] for index in draft.structural_singleton_indices
        ),
    )


def _ground_truth(
    case_id: str,
    draft: CaseDraft,
    records: CaseRecords,
    verification: CaseVerification,
) -> V4GroundTruthCase:
    spec = archetype_spec(draft.archetype)
    settlements = records.settlements
    ids = tuple(s.settlement_id for s in settlements)
    bank_record = records.bank_records[0]

    clues = tuple(
        ClueRecord(
            fragment=fragment,
            clue_kind=kind,
            reaches_settlement_ids=tuple(
                sorted(verification.lexical.reach_by_fragment.get(fragment, frozenset()))
            ),
        )
        for fragment, kind in draft.clues
    )

    structural = None
    reach = _structural_reach(draft, records)
    if draft.structural_kind is not None and reach is not None:
        structural = StructuralDiscriminator(
            kind=draft.structural_kind,  # type: ignore[arg-type]
            value=str(draft.structural_value),
            narration_token=str(draft.structural_token),
            reaches_settlement_ids=tuple(sorted(reach)),
        )

    true_id = None if draft.true_index is None else ids[draft.true_index]
    relationship = (
        None
        if true_id is None
        else V4Relationship(
            bank_record_id=bank_record.bank_record_id,
            settlement_ids=(true_id,),
            relationship="one_to_one",
        )
    )
    return V4GroundTruthCase(
        case_id=case_id,
        archetype=draft.archetype,
        families=spec.families,
        required_composition=spec.required_composition,
        record_ids={
            "orders": tuple(o.order_id for o in records.orders),
            "payments": tuple(p.payment_id for p in records.payments),
            "settlements": ids,
            "refunds": tuple(r.refund_id for r in records.refunds),
            "bank_records": (bank_record.bank_record_id,),
        },
        required_outcome=spec.required_outcome,  # type: ignore[arg-type]
        correct_relationship=relationship,
        true_reference=None if true_id is None else draft.chains[draft.true_index].utr,
        distractor_settlement_ids=tuple(
            sorted(sid for sid in ids if sid != true_id)
        ),
        expected_candidate_count=len(draft.chains),
        clues=clues,
        structural_discriminator=structural,
        value_at_stake_paise=draft.net_paise,
    )


def build_case(
    *,
    case_id: str,
    archetype: str,
    candidate_count: int,
    seed: int,
    factory: RecordFactory,
) -> CaseBundle:
    """Draft, verify and materialize one v4 case. Deterministic in ``seed``."""
    drafter = DRAFTERS[archetype]
    last_error: Exception | None = None

    for attempt in range(MAX_DRAFT_ATTEMPTS):
        attempt_rng = Random(derive_seed(seed, case_id, archetype, "attempt", attempt))
        try:
            draft = drafter(attempt_rng, candidate_count)
        except ReferenceConstructionError as error:
            last_error = error
            continue

        scratch = _materialize(draft, RecordFactory(id_slug=V4_PILOT_SLUG))
        try:
            verify_case(
                case_id=case_id,
                bank_record=scratch.bank_records[0],
                pool=_pool(scratch),
                expectation=_expectation(draft, scratch),
            )
        except V4ConstructError as error:
            last_error = error
            continue

        records = _materialize(draft, factory)
        expectation = _expectation(draft, records)
        verification = verify_case(
            case_id=case_id,
            bank_record=records.bank_records[0],
            pool=_pool(records),
            expectation=expectation,
        )
        return CaseBundle(
            case_id=case_id,
            archetype=archetype,
            records=records,
            ground_truth=_ground_truth(case_id, draft, records, verification),
            verification=verification,
            expectation=expectation,
        )

    raise V4ConstructError(
        f"v4 case {case_id!r} ({archetype}, {candidate_count} candidates) could not be "
        f"drafted to satisfy its invariants in {MAX_DRAFT_ATTEMPTS} attempts; "
        f"last failure: {last_error}"
    )


__all__ = [
    "MAX_DRAFT_ATTEMPTS",
    "CaseBundle",
    "CaseDraft",
    "ChainDraft",
    "DRAFTERS",
    "build_case",
]
