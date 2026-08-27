"""RAW RAZORPAY RECON -> GROUP / VALIDATE -> CANONICAL FINRECON RECORDS + MANIFEST.

Deterministically reconstructs canonical :class:`finrecon.models.Settlement`
records (with ``breakup``) from a complete collection of Razorpay settlement
recon rows (:class:`~finrecon.adapters.razorpay.recon_row.RazorpayReconRow`).
See ``notes/RAZORPAY-INPUT-GAP.md`` §4 for the design this implements.

**Payment/Refund companions.** This module also builds the minimum
canonical ``Payment`` companions (and, where the recon feed's evidence
falls short, :class:`UnresolvedRefundCompanion` facts rather than
canonical ``Refund`` records) needed to mechanically check
:func:`finrecon.matchers.derivation.breakup_references_are_sound` against
imported settlements. See ``README.md``'s "Payment/Refund companions"
section for the sourced justification of the ``PaymentStatus.CAPTURED``
derivation and why ``RefundStatus.PROCESSED`` is deliberately never
derived. It does not build canonical ``Order`` records — the engine's
``breakup_references_are_sound`` predicate never looks one up, so nothing
here needs one (RAZORPAY-INPUT-GAP.md §2.3, and see item 4 of the
companion-construction task brief: "minimum data only").

**Downstream boundary.** The engine reads exactly the five ``loader.py``
files; nothing here changes that contract, and the resulting
:class:`Settlement` objects validate against the unmodified canonical model.
The :class:`~finrecon.adapters.manifest.IngestManifest` this module also
produces is never read by the reconciliation path — it exists for audit,
debugging and ingestion conformance only.

**Completeness is the caller's job.** This module accepts any ``Sequence``
of rows and does not care whether they came from one paginated fetch, many,
or a single exported report — it never fetches anything itself. See
:class:`ReconRowCollection` for a thin wrapper that makes "this is the
complete set for the period" an explicit, typed claim rather than an
implicit one.

**Breakup line identity.** A line's ``reference_id`` is the row's own
``entity_id`` for ``payment``/``refund`` rows (never ``payment_id``, which
is ``null`` on a payment row and the *linked* payment on a refund/transfer
row) and ``None`` for ``transfer``/``adjustment`` rows, which have no
canonical counterpart object at all. See :func:`_reference_id_for`.

**Money.** A line's amount is the row's gross principal
(``(credit - debit) + fee``, see :func:`_row_principal`), not
``credit - debit`` directly — ``credit``/``debit`` are already net of fee
on the documented examples, so subtracting the fee again after using
``credit - debit`` as the principal double-counts it. The group's fee/tax
columns become at most one aggregate ``FEE`` line and one aggregate
``TAX`` line (:func:`_settlement_deductions`), split as
``FEE = -(fee_total - tax_total)``, ``TAX = -tax_total`` when
``tax_total <= fee_total`` (fee is tax-inclusive), or left as one
undifferentiated ``FEE`` line plus a recorded conflict when it is not. In
every case ``sum(breakup) == sum(credit - debit)`` holds exactly, by
construction, not by assumption.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from finrecon.models import (
    Payment,
    PaymentStatus,
    Refund,
    Settlement,
    SettlementLineItem,
    SettlementLineType,
)
from finrecon.models.money import Paise

from ..manifest import (
    ConformanceReport,
    IngestConflict,
    IngestManifest,
    IngestWarning,
    RowProvenance,
)
from .recon_row import RazorpayReconRow, RazorpayReconType


class AdapterInvariantError(RuntimeError):
    """A proven-by-construction adapter invariant did not hold.

    This is never a fact about *source data* — a source-data inconsistency
    is reported as an :class:`~finrecon.adapters.manifest.IngestConflict`
    and quarantines the affected settlement (see
    :data:`NON_BLOCKING_CONFLICT_KINDS`). This exception is reserved for
    the complementary case: a state this adapter's own algebra guarantees
    cannot happen for *any* supported input mapping. Reaching it means the
    construction in this module is broken, not that the settlement's data
    is untrustworthy.

    Deliberately not a bare ``assert``: assertions are stripped when
    Python runs with ``-O``/``-OO``, silently turning a financial-integrity
    check into a no-op in a production interpreter configuration. This
    exception always raises, in every interpreter mode.
    """


_LINE_TYPE_BY_ROW_TYPE: dict[RazorpayReconType, SettlementLineType] = {
    RazorpayReconType.PAYMENT: SettlementLineType.PAYMENT,
    RazorpayReconType.REFUND: SettlementLineType.REFUND,
    RazorpayReconType.TRANSFER: SettlementLineType.TRANSFER,
    RazorpayReconType.ADJUSTMENT: SettlementLineType.ADJUSTMENT,
}

NON_BLOCKING_CONFLICT_KINDS: frozenset[str] = frozenset()
"""``IngestConflict`` kinds that do NOT quarantine their settlement.

Deliberately an *allowlist*, empty today, rather than a denylist of
"blocking" kinds. A denylist fails open: a new conflict kind nobody
remembered to add to it would silently reach the eligible collection. An
allowlist fails closed: every kind is blocking by default, and a kind is
only ever exempted after someone explicitly verifies it is harmless to
the reconciliation path and adds it here (see :func:`is_blocking_conflict`).

Every ``IngestConflict`` kind this adapter currently emits was checked
against that bar and found blocking:

* ``duplicate_entity_id_conflict`` — the conflicting rows are excluded
  from the breakup entirely (task brief §5), so the settlement's
  reconstructed total is missing an unknown amount of money. Emitting it
  as an ordinary settlement would misrepresent it as complete.
* ``conflicting_settlement_utr`` — collapses to ``Settlement(utr=None)``
  if allowed through, which is bit-for-bit indistinguishable downstream
  from an ordinary *missing* UTR. That conflation is exactly what this
  hardening pass exists to prevent (task brief's "Known remaining
  architectural issue").
* ``row_principal_amount_mismatch`` — the reconstructed breakup line
  amount does not match the row's own documented ``amount``, i.e. the
  credit/debit-vs-fee netting convention this adapter assumes is
  *unproven* for that specific row.
  :func:`finrecon.matchers.derivation.breakup_references_are_sound`
  requires a payment line's amount to equal the referenced payment's
  amount exactly; an unproven amount cannot be trusted to satisfy that.
* ``tax_exceeds_fee_unsplit_deduction`` — the *total* deduction stays
  exact even when unsplit (the algebraic identity documented on
  :func:`_settlement_deductions` does not depend on the split holding),
  so this conflict is the one closest to "harmless". It is still
  classified blocking: the tax-is-a-component-of-fee convention proven
  false for this settlement means this adapter's understanding of the
  source's fee semantics has a demonstrated gap on this exact data, which
  is treated conservatively rather than assumed harmless elsewhere in the
  same settlement.
* ``inconsistent_settled_at`` / ``settled_at_unavailable`` — both feed
  ``Settlement.created_at``, and ``.date()`` of that value is exactly
  what :func:`finrecon.matchers.blocking.settlements_in_window` keys the
  declared ±day candidate-generation window on (see
  :func:`_settlement_created_at`). Neither case has an authoritative
  value: disagreement means no single ``settled_at`` can be trusted, and
  outright absence forces a fallback (transaction ``created_at``) that
  measures a different thing entirely (when the underlying payment/refund
  was created, not when the settlement paid out). A silently wrong date
  here does not just look wrong — it can drop the settlement out of the
  window a real matching bank credit falls in, and Stage 2's blocking is
  exhaustive only over what it actually enumerates, so that failure would
  never surface as an escalation; the credit would simply never generate
  the candidate. Both are therefore blocking rather than merely observed.
* ``payment_companion_not_settled`` — the documented ``settled`` field on a
  recon row is explicitly boolean, and the endpoint is not documented to
  guarantee it is always ``true`` (the on-hold/reserve case can return
  ``settled=false``, see ``on_hold_settlement.json``). The capture-before-
  settlement precondition this adapter relies on to derive
  ``PaymentStatus.CAPTURED`` only holds once ``settled`` is actually
  ``true``, so a row that is not settled cannot produce a trustworthy
  companion — endpoint membership alone is not documented to imply it.
* ``payment_companion_missing_order_id`` — :class:`finrecon.models.Payment`
  requires ``order_id``; a ``payment`` recon row with none cannot produce a
  companion that ``breakup_references_are_sound`` could ever accept, so the
  settlement whose breakup references it can no longer be mechanically
  verified and must not reach the eligible collection.
* ``refund_companion_missing_payment_id`` — the same reasoning for
  :class:`UnresolvedRefundCompanion`, which requires the row's linked
  ``payment_id`` (see :func:`_build_refund_companion`).

``IngestWarning`` is a distinct type from ``IngestConflict`` and is never
blocking. Its one former kind (``breakup_does_not_balance_to_source_net``)
was removed once :func:`_row_principal`/:func:`_settlement_deductions` made
the condition it reported a proven-unreachable algebraic identity, now
enforced as an assertion instead (see :func:`_conformance`). It currently
has exactly one member this adapter emits:
``refund_status_unprovable_from_recon`` — see :func:`_build_refund_companion`
— which is deliberately non-blocking: the *refund* itself is real and its
amount/linkage are proven exactly, only its terminal status is unprovable
from this feed alone, and that gap belongs to
``breakup_references_are_sound`` to enforce (by finding no ``Refund`` to
look up), not to ingestion-level quarantine.
"""


def is_blocking_conflict(conflict: IngestConflict) -> bool:
    """Whether ``conflict`` must keep its settlement out of the eligible set.

    See :data:`NON_BLOCKING_CONFLICT_KINDS` for the fail-closed rule: any
    kind not explicitly allowlisted there is blocking.
    """
    return conflict.kind not in NON_BLOCKING_CONFLICT_KINDS


@dataclass(frozen=True)
class ReconRowCollection:
    """A caller's explicit claim that ``rows`` is the *complete* recon set
    for whatever period/source it represents.

    Deliberately does no fetching and no pagination — that is out of scope
    for this adapter (task brief §12). It exists only so "this collection
    is complete" is a typed statement instead of an assumption buried in
    however many API pages a caller happened to concatenate.
    """

    source_id: str
    rows: tuple[RazorpayReconRow, ...]

    @classmethod
    def of(cls, source_id: str, rows: Sequence[RazorpayReconRow]) -> "ReconRowCollection":
        return cls(source_id=source_id, rows=tuple(rows))


@dataclass(frozen=True)
class QuarantinedSettlement:
    """One settlement kept OUT of the decision-eligible collection.

    Never fed to ``loader.py``-shaped consumers or the reconciliation
    path. Exists purely as an ingestion-review/audit artifact — task
    brief §3/§8.
    """

    settlement_id: str
    settlement: Settlement | None
    """Best-effort reconstruction, if the surviving rows were enough to
    build one. ``None`` when every row for this ``settlement_id`` was
    excluded as a conflicting duplicate (task brief §5) and nothing was
    left to reconstruct from."""
    row_fingerprints: tuple[str, ...]
    """Fingerprints of every source row associated with this
    ``settlement_id`` — kept and excluded alike — for audit. Cross-index
    into :class:`~finrecon.adapters.manifest.IngestManifest.rows`."""
    blocking_conflicts: tuple[IngestConflict, ...]
    """Always non-empty — the reason this settlement is quarantined."""


@dataclass(frozen=True)
class UnresolvedRefundCompanion:
    """Refund facts extracted from one ``refund`` recon row, deliberately
    kept apart from :class:`finrecon.models.Refund`.

    Recon proves ``refund_id`` (the row's own ``entity_id``), its linked
    ``payment_id``, and the exact settled amount — all honestly readable
    off the row, and all cross-checked against the same
    :func:`_row_principal` the settlement's own REFUND breakup line is
    built from (see :func:`_build_refund_companion`), so ``amount`` here is
    *provably* the positive counterpart of that line's amount, never merely
    expected to agree with it.

    It does **not** prove :class:`finrecon.models.RefundStatus`. Unlike a
    payment (Razorpay's settlement docs establish that settlement requires
    a *captured* payment — see :func:`_build_payment_companion`), no
    equivalent documented guarantee ties settlement-recon presence to
    ``RefundStatus.PROCESSED``: a refund can be included in a settlement's
    accounting while still ``pending`` from Razorpay's own perspective (see
    ``README.md``'s "Refund status: unresolved by design"). Fabricating
    ``PROCESSED`` would let ``breakup_references_are_sound`` pass on an
    unproven status — exactly the "manufactured accounting" this adapter
    exists to refuse — so this type exists instead of a canonical
    ``Refund``, and no canonical ``Refund`` is built from recon rows at
    all (:data:`RazorpayReconAdapterResult.refunds` is always empty).
    """

    refund_id: str
    payment_id: str
    amount: Paise
    currency: str
    created_at: datetime
    settlement_id: str


@dataclass(frozen=True)
class RazorpayReconAdapterResult:
    """The adapter's output. ``settlements`` is the decision-eligible set.

    **Mechanical invariant** (enforced in :func:`build_recon_result`, and
    covered by tests): ``settlements`` and ``quarantined_settlements``
    never share a ``settlement_id``. A settlement with any blocking
    ``IngestConflict`` (see :func:`is_blocking_conflict`) can *only*
    appear in ``quarantined_settlements`` — never in ``settlements``, no
    matter how far reconstruction got. Callers building loader.py-shaped
    files MUST serialize only ``settlements`` (or ``eligible_settlements()``)
    into the visible dataset files; ``quarantined_settlements`` is a
    separate, ingestion-review-only artifact and must never be merged
    into the same output.

    **Companions.** ``payments`` is built from every ``payment`` recon row
    that survived deduplication/conflict exclusion and carried an
    ``order_id`` (see :func:`_build_payment_companion`) — independent of
    whether that row's own settlement ended up quarantined for an
    unrelated reason, since the fact "this payment was captured and
    settled for this amount" is true regardless. ``refunds`` is always
    empty by design (see :class:`UnresolvedRefundCompanion`);
    ``unresolved_refund_companions`` carries the refund facts recon *can*
    prove, for audit and for a future enrichment step that supplies an
    authoritative status.
    """

    settlements: tuple[Settlement, ...]
    quarantined_settlements: tuple[QuarantinedSettlement, ...]
    manifest: IngestManifest
    conflicts: tuple[IngestConflict, ...] = field(default=())
    warnings: tuple[IngestWarning, ...] = field(default=())
    payments: tuple[Payment, ...] = field(default=())
    refunds: tuple[Refund, ...] = field(default=())
    """Always empty. See :class:`UnresolvedRefundCompanion` for why this
    adapter never fabricates a canonical ``Refund`` from recon rows alone.
    Kept as an explicit field (rather than omitted) so a caller wiring this
    result into ``breakup_references_are_sound(settlement, payments=...,
    refunds=...)`` has the same three-collection shape to reach for, ready
    for whatever later step supplies real ``Refund`` records."""
    unresolved_refund_companions: tuple[UnresolvedRefundCompanion, ...] = field(default=())

    def eligible_settlements(self) -> tuple[Settlement, ...]:
        """The only settlements safe to hand to the reconciliation path."""
        return self.settlements

    def quarantined_settlement_ids(self) -> tuple[str, ...]:
        return tuple(q.settlement_id for q in self.quarantined_settlements)


def _epoch_to_utc(value: int) -> datetime:
    """Convert an absolute Unix epoch second count to an aware UTC datetime.

    Never produces a naive datetime (task brief §8 / RAZORPAY-INPUT-GAP.md
    §4.2). No IST or other local-zone assumption belongs here: the API
    value is already absolute.
    """
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _dedupe(
    rows: Sequence[RazorpayReconRow],
) -> tuple[list[RazorpayReconRow], tuple[str, ...], tuple[IngestConflict, ...], list[RazorpayReconRow]]:
    """Split ``rows`` into (kept rows, dropped duplicate fingerprints,
    conflicts, excluded conflicting rows).

    Two rows sharing ``entity_id`` and an identical fingerprint are the
    same physical row observed twice (repeated page fetch, overlapping
    pagination, a duplicated fixture line) and are collapsed to one —
    NOT a conflict, and the settlement they belong to stays eligible.

    Two rows sharing ``entity_id`` with *different* fingerprints are a
    data conflict, not a duplicate (task brief §11 / §5 of the hardening
    brief): both are excluded from grouping, both are returned as
    ``excluded conflicting rows`` (so their settlement(s) can still be
    quarantined with a full audit trail rather than silently vanishing —
    see :func:`build_recon_result`), and one ``IngestConflict`` is emitted
    per *distinct* ``settlement_id`` touched by the conflicting copies
    (ordinarily one, but a conflict could in principle disagree about its
    own ``settlement_id`` too — every settlement it touches must still be
    quarantined, not just the first one encountered).
    """
    by_entity: dict[str, list[RazorpayReconRow]] = defaultdict(list)
    for row in rows:
        by_entity[row.entity_id].append(row)

    kept: list[RazorpayReconRow] = []
    dropped_duplicates: list[str] = []
    conflicts: list[IngestConflict] = []
    excluded: list[RazorpayReconRow] = []

    for entity_id, group in by_entity.items():
        fingerprints = {row.fingerprint() for row in group}
        if len(fingerprints) == 1:
            kept.append(group[0])
            dropped_duplicates.extend(row.fingerprint() for row in group[1:])
        else:
            excluded.extend(group)
            for settlement_id in sorted({row.settlement_id for row in group}):
                conflicts.append(
                    IngestConflict(
                        kind="duplicate_entity_id_conflict",
                        settlement_id=settlement_id,
                        detail=(
                            f"entity_id {entity_id!r} appears {len(group)} times with "
                            f"{len(fingerprints)} distinct row contents; excluded from "
                            "grouping rather than guessing which copy is authoritative"
                        ),
                        entity_ids=(entity_id,),
                    )
                )
    return kept, tuple(dropped_duplicates), tuple(conflicts), excluded


def _resolve_utr(
    rows: Sequence[RazorpayReconRow], settlement_id: str
) -> tuple[str | None, IngestConflict | None]:
    """Task brief §5: 0 -> None, 1 -> that value, >1 -> settlement-level conflict."""
    distinct_non_null = sorted({row.settlement_utr for row in rows if row.settlement_utr is not None})
    if not distinct_non_null:
        return None, None
    if len(distinct_non_null) == 1:
        return distinct_non_null[0], None
    conflict = IngestConflict(
        kind="conflicting_settlement_utr",
        settlement_id=settlement_id,
        detail=(
            f"{len(distinct_non_null)} distinct non-null settlement_utr values in "
            f"one settlement group: {distinct_non_null!r}; canonical utr left None "
            "so this settlement cannot auto-resolve on UTR"
        ),
        entity_ids=tuple(row.entity_id for row in rows if row.settlement_utr is not None),
    )
    return None, conflict


def _settlement_created_at(rows: Sequence[RazorpayReconRow]) -> tuple[datetime, IngestConflict | None]:
    """Proxy for the settlement's own timestamp, which the recon feed does not carry.

    The recon feed has no settlement-level ``created_at`` — that lives on
    the separate settlement *entity*, which this adapter (recon rows only)
    does not fetch. ``Settlement.created_at`` is not a display-only field:
    ``settlement_date_utc`` (its ``.date()``) is exactly what
    :func:`finrecon.matchers.blocking.index_by_settlement_date` and
    :func:`~finrecon.matchers.blocking.settlements_in_window` key the
    declared ±day candidate-generation window on. A wrong date here does
    not just look wrong — it can silently drop the settlement out of the
    window a real matching bank credit falls in, so a candidate group that
    should exist is never even generated (a false negative Stage 2 cannot
    recover from, since blocking is exhaustive-by-construction over
    exactly what it enumerates).

    Two cases where this adapter cannot derive a value it can stand behind
    are therefore reported back as a **blocking** :class:`IngestConflict`,
    not a warning, so the settlement is quarantined rather than entering
    candidate generation on an unproven date:

    * **All rows agree on ``settled_at``** (the ordinary case): that
      shared value converts cleanly and is used, no conflict.
    * **Rows disagree on ``settled_at``** (``inconsistent_settled_at``):
      one settlement should settle in one payout event, so disagreement
      means either a grouping problem or genuinely unreliable source data
      — there is no way to tell which (if either) value is authoritative.
      The earliest value is still used for the *reconstruction* attached
      to the quarantine entry (audit only, never decision-eligible).
    * **No row carries ``settled_at`` at all** (``settled_at_unavailable``):
      the only fallback available, ``created_at`` (a *transaction's* own
      creation time — RAZORPAY-INPUT-GAP.md §2.2's `created_at`, not a
      settlement payout time), is a materially different concept and can
      be off from the true settlement date by however long the merchant's
      settlement cycle is. Not safe to use for date-window blocking, so
      it too is only ever used for the quarantined reconstruction, never
      silently promoted to an eligible ``Settlement``.
    """
    settled_values = sorted({row.settled_at for row in rows if row.settled_at is not None})
    if settled_values:
        if len(settled_values) > 1:
            conflict = IngestConflict(
                kind="inconsistent_settled_at",
                settlement_id=rows[0].settlement_id,
                detail=(
                    f"{len(settled_values)} distinct settled_at values in one "
                    f"settlement group: {settled_values!r}; no single value can be "
                    "trusted as this settlement's date for Stage-2 candidate-window "
                    "blocking, so the earliest is used only for the quarantined "
                    "reconstruction attached to this conflict"
                ),
                entity_ids=tuple(row.entity_id for row in rows if row.settled_at is not None),
            )
            return _epoch_to_utc(settled_values[0]), conflict
        return _epoch_to_utc(settled_values[0]), None
    conflict = IngestConflict(
        kind="settled_at_unavailable",
        settlement_id=rows[0].settlement_id,
        detail=(
            "no row in this settlement group carries settled_at; the only "
            "fallback (earliest row created_at) is a transaction creation "
            "time, not a settlement payout time, and is not safe for "
            "Stage-2 date-window blocking, so it is used only for the "
            "quarantined reconstruction attached to this conflict"
        ),
        entity_ids=tuple(row.entity_id for row in rows),
    )
    return _epoch_to_utc(min(row.created_at for row in rows)), conflict


def _reference_id_for(row: RazorpayReconRow) -> str | None:
    """Canonical breakup-line identity, per the documented recon contract.

    ``payment_id`` is *not* the row's own identity — it is ``null`` on a
    ``payment`` row and the *linked* payment on a ``refund``/``transfer``
    row. The row that IS a payment or a refund is itself identified by its
    own ``entity_id`` — the very field :class:`finrecon.matchers.derivation`
    looks up against ``Payment.payment_id`` / ``Refund.refund_id``
    (``payments.get(line.reference_id)`` / ``refunds.get(line.reference_id)``).
    ``transfer`` and ``adjustment`` lines have no canonical counterpart
    object in :mod:`finrecon.models` at all, so their line carries no
    reference — the row's own identity still reaches the ingestion
    manifest via :class:`~finrecon.adapters.manifest.RowProvenance`.
    """
    if row.type in (RazorpayReconType.PAYMENT, RazorpayReconType.REFUND):
        return row.entity_id
    return None


def _row_principal(row: RazorpayReconRow) -> int:
    """The row's gross principal, in the line's own signed direction.

    ``credit - debit`` is already **net of fee** (the documented payment
    example: ``credit=97100`` for ``amount=100000, fee=2900`` — a debit-side
    row nets the other way: ``debit=100296`` for ``amount=100000, fee=296``).
    Emitting ``credit - debit`` as the line amount *and* a separate
    aggregate ``-fee`` deduction double-counts the fee. Adding the fee back
    here (``(credit - debit) + fee``) recovers the gross principal exactly
    once, so that principal plus the fee/tax deduction lines built by
    :func:`_settlement_deductions` sums, by construction, to exactly
    ``credit - debit`` — see the algebraic identity documented there.

    Both documented examples independently confirm this also equals the
    row's own ``amount`` field (in the correct sign): ``97100 + 2900 ==
    100000`` and ``-100296 + 296 == -100000``. That agreement is checked in
    :func:`_build_breakup` as a conformance cross-check, not assumed by
    this function — this function needs only ``credit``, ``debit`` and
    ``fee``.
    """
    return (row.credit - row.debit) + row.fee


def _build_payment_companion(
    row: RazorpayReconRow,
) -> tuple[Payment | None, IngestConflict | None]:
    """Derive a canonical :class:`~finrecon.models.Payment` from one
    ``payment`` recon row.

    ``status=PaymentStatus.CAPTURED`` is not guessed. Razorpay's own
    documentation states capture as a settlement *prerequisite* ("The
    payment must be captured") and describes the settlement cycle itself
    as counting from the capture date ("We automatically settle captured
    payments to the bank account... following our settlement cycle" —
    settlement cycle FAQs; "captured payments are settled... T+2 working
    days (T being the date of transaction capture)" — About Settlements).
    Crucially, that precondition is only established for a row that is
    itself actually settled: the documented ``settled`` field on this feed
    is explicitly boolean ("Indicates whether the transaction has been
    settled or not. Possible values: true, false"), NOT a field the
    endpoint guarantees is always ``true`` for every row it returns — the
    on-hold/reserve case (RAZORPAY-INPUT-GAP.md §2.4/§5, and this
    adapter's own ``on_hold_settlement.json`` fixture) is documented
    exactly as a row that can appear with ``settled=False``. So
    ``row.settled`` is checked explicitly below rather than inferred from
    "this row was returned by the recon endpoint at all" — endpoint
    membership alone is not documented to imply settlement, only the
    ``settled`` field itself is.

    A row's presence in the *settlement recon* feed with ``settled=True``
    is exactly that documented precondition having already held, so
    deriving ``CAPTURED`` here is reading off a documented implication,
    not inventing a field the source never supplied. See ``README.md``'s
    "Payment status: CAPTURED derivation" for the full sourced
    justification and citations.

    ``amount`` intentionally reuses :func:`_row_principal` — the exact
    integer the settlement's own PAYMENT breakup line is built from (see
    :func:`_build_breakup`) — rather than re-deriving it from ``row.amount``
    independently, so the canonical Payment's amount is *provably* equal
    to the referencing line's amount (what
    :func:`finrecon.matchers.derivation.breakup_references_are_sound`
    checks), not merely expected to agree with it.

    Returns ``(None, IngestConflict)`` when ``settled`` is not ``True`` —
    the CAPTURED derivation's own precondition does not hold for this row
    (``payment_companion_not_settled``) — or when ``order_id`` is absent —
    the canonical :class:`~finrecon.models.Payment` model requires one and
    the recon feed does not guarantee it is populated
    (``payment_companion_missing_order_id``). Either way no companion can
    be built and the settlement referencing this row cannot be verified
    (:data:`NON_BLOCKING_CONFLICT_KINDS` classifies both as blocking).
    """
    if row.settled is not True:
        return None, IngestConflict(
            kind="payment_companion_not_settled",
            settlement_id=row.settlement_id,
            detail=(
                f"payment row {row.entity_id!r} has settled={row.settled!r}; "
                "the documented capture-before-settlement precondition this "
                "adapter relies on to derive PaymentStatus.CAPTURED only "
                "holds for a row that is actually settled, so no companion "
                "Payment can be built for this row and its settlement's "
                "breakup references cannot be mechanically verified"
            ),
            entity_ids=(row.entity_id,),
        )
    if row.order_id is None:
        return None, IngestConflict(
            kind="payment_companion_missing_order_id",
            settlement_id=row.settlement_id,
            detail=(
                f"payment row {row.entity_id!r} has no order_id; the "
                "canonical Payment model requires one, so no companion "
                "Payment can be built for this row and its settlement's "
                "breakup references cannot be mechanically verified"
            ),
            entity_ids=(row.entity_id,),
        )
    raw_method = getattr(row, "method", None)
    method = raw_method if isinstance(raw_method, str) else None
    payment = Payment(
        payment_id=row.entity_id,
        order_id=row.order_id,
        amount=Paise(_row_principal(row)),
        currency=row.currency,
        status=PaymentStatus.CAPTURED,
        method=method,
        created_at=_epoch_to_utc(row.created_at),
    )
    return payment, None


def _build_refund_companion(
    row: RazorpayReconRow,
) -> tuple[UnresolvedRefundCompanion | None, IngestConflict | None]:
    """Extract refund facts from one ``refund`` recon row, without
    fabricating a status. See :class:`UnresolvedRefundCompanion`.

    ``amount`` is ``-_row_principal(row)``: the REFUND breakup line's
    amount is negative (a deduction), and
    :func:`finrecon.matchers.derivation.breakup_references_are_sound`
    checks ``refund.amount_paise == -line.amount_paise``, so this is the
    exact positive counterpart of that line's amount, provably (same
    source integer), not merely expected to match.

    Returns ``(None, IngestConflict)`` when ``payment_id`` is absent —
    the documented recon contract carries the *linked* payment on a
    refund row, and without it no companion can be built at all (blocking,
    same reasoning as :func:`_build_payment_companion`).
    """
    if row.payment_id is None:
        return None, IngestConflict(
            kind="refund_companion_missing_payment_id",
            settlement_id=row.settlement_id,
            detail=(
                f"refund row {row.entity_id!r} has no linked payment_id; "
                "an UnresolvedRefundCompanion requires one, so no companion "
                "can be built for this row and its settlement's breakup "
                "references cannot be mechanically verified"
            ),
            entity_ids=(row.entity_id,),
        )
    companion = UnresolvedRefundCompanion(
        refund_id=row.entity_id,
        payment_id=row.payment_id,
        amount=Paise(-_row_principal(row)),
        currency=row.currency,
        created_at=_epoch_to_utc(row.created_at),
        settlement_id=row.settlement_id,
    )
    return companion, None


def _settlement_deductions(
    rows: Sequence[RazorpayReconRow], settlement_id: str
) -> tuple[list[SettlementLineItem], list[str], IngestConflict | None]:
    """Aggregate fee/tax deduction lines for one settlement group.

    ``fee`` is the *total* deduction (task brief's transfer example:
    ``fee=296`` already includes ``tax=46`` — ``fee - tax == 250`` is the
    non-tax component). Splitting it into a ``FEE`` line and a ``TAX`` line
    as ``-(fee - tax)`` and ``-tax`` sums back to exactly ``-fee`` — so the
    total deduction is correct *regardless* of whether the "tax is a
    component of fee" assumption holds. That assumption only affects how
    the deduction is split across the two line *types*.

    If any row's ``tax`` exceeds its ``fee`` the split assumption is
    provably wrong for this group (a component cannot exceed its whole),
    so no split is attempted: the full ``fee_total`` is emitted as one
    undifferentiated ``FEE`` line (still exact — this is the amount
    :func:`_row_principal` already added back) and the mismatch is recorded
    as a settlement-scoped conflict rather than silently guessing a split.
    """
    fee_total = sum(row.fee for row in rows)
    tax_total = sum(row.tax for row in rows)
    lines: list[SettlementLineItem] = []
    labels: list[str] = []
    conflict: IngestConflict | None = None

    if tax_total > fee_total:
        conflict = IngestConflict(
            kind="tax_exceeds_fee_unsplit_deduction",
            settlement_id=settlement_id,
            detail=(
                f"aggregate tax {tax_total} paise exceeds aggregate fee {fee_total} paise "
                "in this settlement group; the 'tax is a component of fee' split does not "
                "hold here, so the full fee total is recorded as one undifferentiated FEE "
                "line instead of being split into FEE/TAX lines"
            ),
            entity_ids=tuple(row.entity_id for row in rows if row.tax > row.fee),
        )
        if fee_total != 0:
            lines.append(SettlementLineItem(type=SettlementLineType.FEE, amount=Paise(-fee_total), reference_id=None))
            labels.append("fee:aggregate:unsplit")
        return lines, labels, conflict

    fee_before_tax = fee_total - tax_total
    if fee_before_tax != 0:
        lines.append(SettlementLineItem(type=SettlementLineType.FEE, amount=Paise(-fee_before_tax), reference_id=None))
        labels.append("fee:aggregate")
    if tax_total != 0:
        lines.append(SettlementLineItem(type=SettlementLineType.TAX, amount=Paise(-tax_total), reference_id=None))
        labels.append("tax:aggregate")
    return lines, labels, None


def _build_breakup(
    rows: Sequence[RazorpayReconRow], settlement_id: str
) -> tuple[tuple[SettlementLineItem, ...], list[str], list[IngestConflict]]:
    """One principal line per transaction row, plus the group's fee/tax
    deduction lines (task brief §6). The sum always equals
    ``sum(credit - debit)`` over the group exactly, by construction — see
    :func:`_row_principal` and :func:`_settlement_deductions`.

    Returns the breakup, a human-readable label per line (positionally
    aligned, for :class:`~finrecon.adapters.manifest.RowProvenance`), and
    any conflicts discovered while building it.
    """
    lines: list[SettlementLineItem] = []
    line_labels: list[str] = []
    conflicts: list[IngestConflict] = []

    for row in rows:
        principal = _row_principal(row)
        lines.append(
            SettlementLineItem(
                type=_LINE_TYPE_BY_ROW_TYPE[row.type],
                amount=Paise(principal),
                reference_id=_reference_id_for(row),
            )
        )
        line_labels.append(f"breakup[{len(lines) - 1}]:{row.type.value}:{row.entity_id}")

        if abs(principal) != row.amount:
            conflicts.append(
                IngestConflict(
                    kind="row_principal_amount_mismatch",
                    settlement_id=settlement_id,
                    detail=(
                        f"row {row.entity_id!r}: reconstructed principal "
                        f"{principal} paise (credit={row.credit}, debit={row.debit}, "
                        f"fee={row.fee}) does not match the documented amount "
                        f"{row.amount} paise in magnitude; the credit/debit-vs-fee "
                        "netting convention assumed for this row is unproven"
                    ),
                    entity_ids=(row.entity_id,),
                )
            )

    deduction_lines, deduction_labels, deduction_conflict = _settlement_deductions(rows, settlement_id)
    lines.extend(deduction_lines)
    line_labels.extend(f"breakup[{len(rows) + i}]:{label}" for i, label in enumerate(deduction_labels))
    if deduction_conflict is not None:
        conflicts.append(deduction_conflict)

    return tuple(lines), line_labels, conflicts


def _conformance(
    settlement_id: str, rows: Sequence[RazorpayReconRow], breakup: Sequence[SettlementLineItem]
) -> ConformanceReport:
    """Report, never manufacture, whether the reconstruction balances.

    **``source_net``**, precisely: ``sum(row.credit for row in rows) -
    sum(row.debit for row in rows)`` — the group's total source-reported
    money movement, independent of anything this adapter constructs.

    ``sum(canonical_breakup) == source_net`` is NOT assumed (task brief
    §7) — it is measured and recorded, not asserted here. But given
    :func:`_row_principal` (``principal = (credit - debit) + fee`` per
    row) and :func:`_settlement_deductions` (deduction lines always sum to
    exactly ``-fee_total``, split into FEE/TAX or left as one
    undifferentiated FEE line), the two algebraically cancel for *any*
    input, with no dependency on any per-row or per-group anomaly:

        sum(breakup)
      = sum(principal) + sum(deductions)
      = (sum(credit - debit) + fee_total) + (-fee_total)
      = sum(credit - debit)
      = source_net

    So ``totals_agree`` is a *proven* invariant, not merely an
    expectation — :func:`build_recon_result` raises
    :class:`AdapterInvariantError` if it ever does not hold for a
    settlement it builds (deliberately not a bare ``assert``: those are
    stripped under ``python -O``, and this is a financial-integrity check
    that must never silently become a no-op). There is deliberately no
    ``breakup_does_not_balance_to_source_net`` warning any more: it could
    never fire (an unreachable warning is worse than none, since it
    implies a failure mode that does not exist), and a violation now means
    a bug in this construction, not a fact worth reporting about the
    source data — hence an explicit adapter-internal exception, not a
    warning and not an ``IngestConflict`` (the settlement's *source data*
    is not in question here; this adapter's own code is).
    """
    credit_total = sum(row.credit for row in rows)
    debit_total = sum(row.debit for row in rows)
    source_net = credit_total - debit_total
    breakup_total = sum(int(line.amount) for line in breakup)
    return ConformanceReport(
        settlement_id=settlement_id,
        source_credit_total=credit_total,
        source_debit_total=debit_total,
        source_net=source_net,
        canonical_breakup_total=breakup_total,
        totals_agree=breakup_total == source_net,
        difference=breakup_total - source_net,
    )


def _excluded_row_provenance(
    row: RazorpayReconRow, source_id: str, row_index: int
) -> RowProvenance:
    """Provenance for a row excluded entirely by :func:`_dedupe` (task brief §3/§5).

    Nothing canonical was produced from it — ``produced`` and
    ``source_fields_used`` are empty — but it still must be traceable by
    fingerprint, which is the whole point of quarantine keeping an audit
    trail rather than the row simply vanishing.
    """
    return RowProvenance(
        source_id=source_id,
        row_index=row_index,
        row_fingerprint=row.fingerprint(),
        entity_id=row.entity_id,
        settlement_id=row.settlement_id,
        produced=(),
        source_fields_used=(),
        dropped_fields=row.dropped_fields(),
        unrecognized_fields=row.unrecognized_fields(),
    )


def build_recon_result(collection: ReconRowCollection) -> RazorpayReconAdapterResult:
    """Transform a complete collection of recon rows into canonical settlements.

    Deterministic: two calls with the same rows in any input order produce
    byte-identical ``Settlement`` objects, because every group is sorted by
    ``entity_id`` before it is folded into a breakup (task brief §4/§9's
    "stable ordering is mandatory") — quarantine routing included, since it
    is decided from the same per-settlement conflict list.

    **Quarantine.** A settlement with any blocking ``IngestConflict``
    (:func:`is_blocking_conflict`) is built the same way as any other —
    reconstruction is still attempted, for the audit trail — but the
    result is routed to ``quarantined_settlements`` instead of
    ``settlements``, and never both. This includes the degenerate case
    where every row for a ``settlement_id`` was excluded by ``_dedupe``
    (task brief §5): that settlement still gets a
    :class:`QuarantinedSettlement` entry (with ``settlement=None``, since
    there is nothing left to reconstruct from) rather than disappearing
    from the result entirely.
    """
    kept_rows, duplicate_fingerprints, dedupe_conflicts, excluded_rows = _dedupe(collection.rows)

    by_settlement: dict[str, list[RazorpayReconRow]] = defaultdict(list)
    for row in kept_rows:
        by_settlement[row.settlement_id].append(row)

    excluded_by_settlement: dict[str, list[RazorpayReconRow]] = defaultdict(list)
    for row in excluded_rows:
        excluded_by_settlement[row.settlement_id].append(row)

    dedupe_conflicts_by_settlement: dict[str, list[IngestConflict]] = defaultdict(list)
    for conflict in dedupe_conflicts:
        dedupe_conflicts_by_settlement[conflict.settlement_id].append(conflict)

    settlements: list[Settlement] = []
    quarantined: list[QuarantinedSettlement] = []
    all_conflicts: list[IngestConflict] = []
    warnings: list[IngestWarning] = []
    conformance: list[ConformanceReport] = []
    provenance: list[RowProvenance] = []
    payments: list[Payment] = []
    unresolved_refund_companions: list[UnresolvedRefundCompanion] = []

    row_index_by_fingerprint = {row.fingerprint(): idx for idx, row in enumerate(collection.rows)}

    all_settlement_ids = sorted(set(by_settlement) | set(excluded_by_settlement))

    for settlement_id in all_settlement_ids:
        rows = sorted(by_settlement.get(settlement_id, []), key=lambda r: r.entity_id)
        settlement_conflicts: list[IngestConflict] = list(
            dedupe_conflicts_by_settlement.get(settlement_id, [])
        )

        reconstructed: Settlement | None = None
        row_fingerprints: list[str] = []

        if rows:
            utr, utr_conflict = _resolve_utr(rows, settlement_id)
            if utr_conflict is not None:
                settlement_conflicts.append(utr_conflict)

            created_at, timestamp_conflict = _settlement_created_at(rows)
            if timestamp_conflict is not None:
                settlement_conflicts.append(timestamp_conflict)

            breakup, line_labels, breakup_conflicts = _build_breakup(rows, settlement_id)
            settlement_conflicts.extend(breakup_conflicts)
            report = _conformance(settlement_id, rows, breakup)
            conformance.append(report)
            if not report.totals_agree:
                raise AdapterInvariantError(
                    f"settlement {settlement_id!r}: canonical breakup total "
                    f"{report.canonical_breakup_total} paise != source net (credit-debit) "
                    f"{report.source_net} paise; this is an algebraic identity by "
                    "construction (see _row_principal/_settlement_deductions) and its "
                    "failure means that construction has a bug, not a fact about the "
                    "source data — see ConformanceReport.totals_agree"
                )

            reconstructed = Settlement(
                settlement_id=settlement_id,
                utr=utr,
                amount=Paise(report.canonical_breakup_total),
                created_at=created_at,
                breakup=breakup,
            )

            for row in rows:
                if row.type is RazorpayReconType.PAYMENT:
                    payment, payment_conflict = _build_payment_companion(row)
                    if payment_conflict is not None:
                        settlement_conflicts.append(payment_conflict)
                    if payment is not None:
                        payments.append(payment)
                elif row.type is RazorpayReconType.REFUND:
                    refund_companion, refund_conflict = _build_refund_companion(row)
                    if refund_conflict is not None:
                        settlement_conflicts.append(refund_conflict)
                    if refund_companion is not None:
                        unresolved_refund_companions.append(refund_companion)
                        warnings.append(
                            IngestWarning(
                                kind="refund_status_unprovable_from_recon",
                                settlement_id=settlement_id,
                                detail=(
                                    f"refund row {row.entity_id!r}: settlement recon proves "
                                    "refund_id, linked payment_id and exact amount, but carries "
                                    "no evidence for RefundStatus — no canonical Refund is built; "
                                    "see UnresolvedRefundCompanion. "
                                    "breakup_references_are_sound stays fail-closed on this line "
                                    "until an authoritative refund status is available"
                                ),
                            )
                        )

            # `_build_breakup` emits one label per breakup line, in the same
            # order as `rows` for the per-row lines followed by the aggregate
            # fee/tax lines; walk both in lockstep to attribute provenance.
            per_row_labels = line_labels[: len(rows)]
            aggregate_labels = line_labels[len(rows) :]
            for row, label in zip(rows, per_row_labels):
                row_fingerprints.append(row.fingerprint())
                fields_used = {
                    "settlement_id",
                    "type",
                    "credit",
                    "debit",
                    "entity_id",
                    "amount",  # cross-checked against the reconstructed principal, not copied
                }
                if row.settlement_utr is not None:
                    fields_used.add("settlement_utr")
                if row.fee != 0:
                    fields_used.add("fee")
                if row.tax != 0:
                    fields_used.add("tax")
                if row.settled_at is not None:
                    fields_used.add("settled_at")
                else:
                    fields_used.add("created_at")
                produced = (f"settlement:{settlement_id}#{label}",)
                if aggregate_labels:
                    produced = produced + tuple(
                        f"settlement:{settlement_id}#{lbl}" for lbl in aggregate_labels
                    )
                provenance.append(
                    RowProvenance(
                        source_id=collection.source_id,
                        row_index=row_index_by_fingerprint[row.fingerprint()],
                        row_fingerprint=row.fingerprint(),
                        entity_id=row.entity_id,
                        settlement_id=settlement_id,
                        produced=produced,
                        source_fields_used=tuple(sorted(fields_used)),
                        dropped_fields=row.dropped_fields(),
                        unrecognized_fields=row.unrecognized_fields(),
                    )
                )

        for excluded_row in excluded_by_settlement.get(settlement_id, []):
            row_fingerprints.append(excluded_row.fingerprint())
            provenance.append(
                _excluded_row_provenance(
                    excluded_row,
                    collection.source_id,
                    row_index_by_fingerprint[excluded_row.fingerprint()],
                )
            )

        all_conflicts.extend(settlement_conflicts)

        blocking = tuple(c for c in settlement_conflicts if is_blocking_conflict(c))
        if blocking:
            quarantined.append(
                QuarantinedSettlement(
                    settlement_id=settlement_id,
                    settlement=reconstructed,
                    row_fingerprints=tuple(row_fingerprints),
                    blocking_conflicts=blocking,
                )
            )
        elif reconstructed is not None:
            settlements.append(reconstructed)
        # else: rows for this settlement_id existed only as excluded
        # duplicates/conflicts and none of them were classified blocking —
        # cannot happen with the current NON_BLOCKING_CONFLICT_KINDS (always
        # empty for duplicate_entity_id_conflict), but if it ever does, the
        # settlement correctly produces neither an eligible nor a
        # quarantined entry, since there is nothing to say about it.

    manifest = IngestManifest(
        source_id=collection.source_id,
        rows=tuple(provenance),
        duplicate_rows_dropped=duplicate_fingerprints,
        conflicts=tuple(all_conflicts),
        warnings=tuple(warnings),
        conformance=tuple(conformance),
    )

    eligible_ids = {s.settlement_id for s in settlements}
    quarantined_ids = {q.settlement_id for q in quarantined}
    if not eligible_ids.isdisjoint(quarantined_ids):
        raise AdapterInvariantError(
            "mechanical quarantine invariant violated: "
            f"{eligible_ids & quarantined_ids} appear in both the eligible and "
            "quarantined collections"
        )

    return RazorpayReconAdapterResult(
        settlements=tuple(settlements),
        quarantined_settlements=tuple(quarantined),
        manifest=manifest,
        conflicts=tuple(all_conflicts),
        warnings=tuple(warnings),
        payments=tuple(payments),
        unresolved_refund_companions=tuple(unresolved_refund_companions),
    )


__all__ = [
    "AdapterInvariantError",
    "NON_BLOCKING_CONFLICT_KINDS",
    "QuarantinedSettlement",
    "RazorpayReconAdapterResult",
    "ReconRowCollection",
    "UnresolvedRefundCompanion",
    "build_recon_result",
    "is_blocking_conflict",
]
