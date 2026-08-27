# Razorpay settlement recon adapter

```
RAW RAZORPAY RECON ROWS  (RazorpayReconRow, one per settled transaction)
        |
        v
GROUP / VALIDATE   (recon.py: build_recon_result)
  - group by settlement_id, sorted for deterministic output
  - dedupe identical rows; flag same-entity_id/different-content as conflict
  - resolve settlement_utr: 0 -> None, 1 -> that value, >1 -> settlement-level
    ingestion conflict (utr left None, never guessed, never batch-failing)
  - build breakup: one principal line per payment/refund/transfer/adjustment
    row -- amount = (credit - debit) + fee, i.e. the row's gross amount,
    NOT credit - debit alone (credit/debit are already net of fee on the
    documented examples; subtracting fee again would double-count it) --
    plus at most one aggregate fee line and one aggregate tax line per
    group, split as FEE = -(fee_total - tax_total), TAX = -tax_total
    (fee is tax-inclusive) when tax_total <= fee_total, else one
    undifferentiated FEE line and a recorded conflict
  - reference_id: the row's own entity_id for payment/refund lines (never
    payment_id, which is null on a payment row and the *linked* payment on
    a refund/transfer row); None for transfer/adjustment lines, which have
    no canonical counterpart object
  - conformance diagnostic: report whether canonical breakup total agrees
    with source net (credit - debit) -- an algebraic identity by
    construction, not an assumption; never manufacture a balancing line
  - QUARANTINE: any settlement carrying a blocking IngestConflict is
    routed to quarantined_settlements, never settlements -- reconstruction
    is still attempted for the audit trail, but the result cannot reach
    the decision-eligible collection (see "Quarantine" below)
        |
        v
CANONICAL FINRECON RECORDS               INGESTION MANIFEST
  result.settlements                      finrecon.adapters.manifest.IngestManifest
  result.payments                         (row provenance, conflicts, warnings,
  result.refunds (always empty --          conformance reports — audit/debug only,
   see "Payment/Refund companions")        never read by the decision engine)
  result.unresolved_refund_companions
  (decision-eligible ONLY; validates
   against the unmodified canonical
   model; loader.py's contract is
   untouched)
        |
        v (separately)
QUARANTINE ARTIFACT (ingestion review only, never fed to the decision engine)
  result.quarantined_settlements
  each entry: settlement_id, best-effort reconstructed Settlement (or None
  if nothing could be reconstructed), every source row's fingerprint, and
  the blocking IngestConflict(s) that caused quarantine
```

## Quarantine

A settlement carrying any **blocking** `IngestConflict` never appears in
`result.settlements` — the decision-eligible collection — no matter how
far its reconstruction got. It appears in `result.quarantined_settlements`
instead, which is a strictly separate, ingestion-review-only artifact. The
rest of the batch is unaffected: quarantine is per-settlement, never
whole-batch.

**Why this exists.** The reconciliation engine deliberately never reads
the ingestion manifest/conflict sidecar (§4.5 of
`notes/RAZORPAY-INPUT-GAP.md`). Before this quarantine mechanism, a
settlement with a source contradiction — e.g. two distinct non-null
`settlement_utr` values in one group — was still emitted as an ordinary
`Settlement(utr=None)`, bit-for-bit indistinguishable downstream from an
*ordinary missing* UTR. That conflation was unsafe: "the UTR is missing"
and "the UTR is contradictory" are different facts that must stay
distinguishable, and only the sidecar (which the engine never reads)
carried the difference.

**Which conflicts are blocking.** See `is_blocking_conflict` and
`NON_BLOCKING_CONFLICT_KINDS` in `recon.py` — an *allowlist* of
non-blocking kinds (empty today), not a denylist of blocking ones, so an
unclassified future conflict kind defaults to blocking rather than
silently leaking through. All six kinds this adapter currently emits
(`duplicate_entity_id_conflict`, `conflicting_settlement_utr`,
`row_principal_amount_mismatch`, `tax_exceeds_fee_unsplit_deduction`,
`inconsistent_settled_at`, `settled_at_unavailable`) were individually
verified and are blocking — the last two because `Settlement.created_at`
directly drives Stage 2's ±day candidate-window blocking
(`finrecon.matchers.blocking`), so a settlement date this adapter cannot
stand behind must not reach candidate generation. `IngestWarning` is a
distinct type and currently has no members this adapter emits (its one
former kind, `breakup_does_not_balance_to_source_net`, was removed once
the money reconstruction made the condition it reported a proven-unreachable
algebraic identity — see `_conformance` — and is now an assertion instead).

**Serialization boundary.** No ingest CLI exists yet (out of scope for
this task). Whenever one is built, it must serialize only
`result.settlements` (or `result.eligible_settlements()`) into the
`loader.py`-compatible visible dataset files. `result.quarantined_settlements`
is a separate artifact and must never be merged into the same output —
this is enforced today by the two fields being distinct types
(`tuple[Settlement, ...]` vs. `tuple[QuarantinedSettlement, ...]`), and by
a runtime assertion in `build_recon_result` that the two settlement-id
sets are always disjoint.

## Payment/Refund companions

`build_recon_result` also builds the minimum canonical `Payment[]` (and,
short of a canonical `Refund`, `UnresolvedRefundCompanion[]`) companions
needed so an imported `Settlement.breakup` can be mechanically checked by
`finrecon.matchers.derivation.breakup_references_are_sound`. This is
deliberately the *minimum* extension: no `Order`, no live API client, no
webhook ingestion. `breakup_references_are_sound` never looks up an
`Order`, so none is built.

### Payment status: `CAPTURED` derivation

A `payment` recon row's canonical `Payment.status` is derived as
`PaymentStatus.CAPTURED`, never guessed. Razorpay's own documentation
states this as a settlement *precondition*, not an adapter assumption:

* Settlements FAQ, prerequisites for a payment to settle: **"The payment
  must be captured."**
* About Settlements: **"We automatically settle captured payments to the
  bank account you submitted to us during your KYC verification following
  our settlement cycle."**
* About Settlements (cycle definition): **"captured payments are settled
  within [the settlement cycle]... T+2 working days (T being the date of
  transaction capture)"**.

(Sources: <https://razorpay.com/docs/payments/settlements/faqs/>,
<https://razorpay.com/docs/payments/settlements/>.)

The settlement recon feed does **not** guarantee every row it returns is
itself settled — the documented `settled` field is explicitly boolean
("Indicates whether the transaction has been settled or not. Possible
values: true, false"), and the on-hold/reserve case is a documented
example of a row the endpoint can return with `settled=false` (see
`on_hold_settlement.json` and RAZORPAY-INPUT-GAP.md §2.4/§5). So this
adapter does not derive `CAPTURED` from endpoint membership alone: a
`payment` row's own `settled` field must explicitly be `True` before the
capture-before-settlement precondition above is read as having held for
*that* row. `_build_payment_companion` checks this first and fails closed
(`payment_companion_not_settled`, blocking) when it does not — see
"Companion conflict/quarantine boundary" below. Only once `settled=True`
is deriving `CAPTURED` reading off a documented implication rather than
inventing a field the source never supplied. `Payment.amount`, `order_id`,
`currency`, `method` (when present) and `created_at` are otherwise
copied/converted honestly from the row — see `_build_payment_companion` in
`recon.py`.

### Refund status: unresolved by design

The equivalent claim for refunds — that a `refund` row's presence in
settlement recon proves `RefundStatus.PROCESSED` — is **not** established
by official Razorpay documentation. The refund entity docs define
`processed` as refund's own terminal state but do not tie it to settlement
recon inclusion, and the settlement docs describe *what* a settled refund
deducts, not confirmation that its status has reached `processed` by the
time it is recon'd. Fabricating `PROCESSED` here would let
`breakup_references_are_sound` pass on an unproven status.

So this adapter never builds a canonical `Refund` from recon rows —
`RazorpayReconAdapterResult.refunds` is always the empty tuple. Instead it
builds `UnresolvedRefundCompanion` — `refund_id`, linked `payment_id` and
exact `amount`, all of which recon *does* honestly prove — and records a
non-blocking `refund_status_unprovable_from_recon` `IngestWarning` per
refund row. A settlement whose breakup references a refund therefore
always fails `breakup_references_are_sound` (there is no `Refund` for the
lookup to find), which is the correct, fail-closed outcome pending a real
refunds-entity-backed enrichment step — not an ingestion-level quarantine,
since the settlement's own reconstructed data is not in question.

### Companion conflict/quarantine boundary

A `payment` row with `settled` not `True`, a `payment` row with no
`order_id`, or a `refund` row with no linked `payment_id`, cannot produce a
trustworthy companion at all. Each is a new blocking `IngestConflict` kind
(`payment_companion_not_settled`, `payment_companion_missing_order_id`,
`refund_companion_missing_payment_id` — see `NON_BLOCKING_CONFLICT_KINDS`
in `recon.py`) that quarantines the row's settlement, same as any other
source contradiction. Duplicate/conflicting payment or refund evidence
(same `entity_id`, different content) is already caught by the existing
row-level dedupe/conflict mechanism (`duplicate_entity_id_conflict`) —
nothing new was needed there, since a payment or refund row's own
`entity_id` *is* its companion's identity.

## What this adapter does not build

`Order` canonical records are not synthesized from recon rows —
`breakup_references_are_sound` never looks one up, so nothing in the
reconciliation path needs one. A live Razorpay API/webhook client, a
refunds-entity enrichment step (needed to ever populate `refunds` with a
real `RefundStatus`), and bank ingestion are all likewise out of scope for
this adapter.

## Honesty notes

- The fixtures under `fixtures/razorpay/doc_samples/` are derived from the
  public API documentation, not captured from a real merchant. They prove
  the *transformation* is correct against the documented shape; they say
  nothing about real-world distribution, frequency, or accounting once
  `on_hold`, reserve balance, disputes and negative settlements are
  involved in production. See `notes/RAZORPAY-INPUT-GAP.md` §5.
- Coverage measured against the synthetic v3/v4 benchmark is a statement
  about the benchmark's own declared narration conventions
  (`notes/RAZORPAY-INPUT-GAP.md` §3/§6), not a prediction of real
  Razorpay data — that is a separate axis from this adapter's own
  conformance, which is what the `ConformanceReport`/conflict/warning
  entries in `IngestManifest` measure.
- The frozen decision engine's safety properties (closed evidence sets,
  zero unsafe auto-match, escalation on contradiction, integer-paise
  money) are properties of the decision path and are unaffected by
  anything in this package. This adapter's own correctness — did it group,
  resolve UTRs and reconstruct breakups right — is a separate, narrower
  claim, checked by `tests/test_razorpay_recon_adapter.py` and never by
  running the decision engine over adapter output.
