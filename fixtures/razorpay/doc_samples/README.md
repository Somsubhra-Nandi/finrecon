# Razorpay recon fixtures — official-doc-derived, NOT real merchant data

Every JSON file in this directory is a list of settlement recon rows shaped
after the public API reference (`GET /v1/settlements/recon/combined`), field
names and value shapes taken from:

- Settlement entity — https://razorpay.com/docs/api/settlements/entity/
- Fetch Settlement Recon Details — https://razorpay.com/docs/api/settlements/fetch-recon/

**These are not real Razorpay merchant data.** They prove the adapter's
*transformation* is correct against the documented shape; they say nothing
about how real production data is distributed, how often any scenario here
occurs, or what a real settlement's accounting looks like once `on_hold`,
reserve balance, disputes and negative settlements are involved. See
`notes/RAZORPAY-INPUT-GAP.md` §5 for what remains unknown until real data
arrives, and its closing caution about the documented recon sample's own
`settlement_utr` being a test-mode value.

All entity/settlement/payment/order ids in these fixtures are synthetic
and namespaced `docsample` (`pay_docsample_*`, `rfnd_docsample_*`,
`trf_docsample_*`, `adj_docsample_*`, `setl_docsample_*`, ...) so they can
never be mistaken for a captured production identifier. `entity_id` uses
the prefix matching the row's own `type`, per the documented contract that
`entity_id` is the settled transaction's *own* id — a payment row's
`entity_id` is its payment id, a refund row's is its refund id, and so on;
`payment_id` is `null` on payment rows and only ever the *linked* payment
on refund/transfer rows.

| File | Scenario |
|---|---|
| `official_doc_payment_example.json` | The correction brief's documented payment example verbatim: `amount=100000, credit=97100, fee=2900, tax=0` |
| `official_doc_transfer_example.json` | The correction brief's documented transfer example verbatim: `amount=100000, debit=100296, fee=296, tax=46` |
| `multi_payment_settlement.json` | A: one settlement, multiple payment rows |
| `payment_and_refund.json` | B: payment + refund in one settlement |
| `payment_and_transfer.json` | C: payment + transfer in one settlement |
| `adjustment_null_utr.json` | D: adjustment row with `settlement_utr: null` |
| `mixed_utr_and_null.json` | E: one UTR on some rows, null on others -> resolves to that UTR |
| `conflicting_utr.json` | F: two distinct non-null UTRs in one group -> conflict |
| `multi_row_fees_taxes.json` | G: fee/tax present on multiple rows, aggregated |
| `negative_settlement.json` | H: debit exceeds credit (documented negative movement) |
| `duplicate_rows.json` | J: the same physical row appears twice |
| `on_hold_settlement.json` | K: a row carries `on_hold: true` |
| `dispute_present.json` | L: a row carries a non-null `dispute_id` |
| `tax_exceeds_fee.json` | M: `tax` column exceeds `fee` column — the fee-is-tax-inclusive split is provably invalid, so the adapter falls back to one undifferentiated FEE line and records a conflict |

Scenario I (stable transformation independent of input order) is not a
fixture file — it is a property tests assert by permuting one of the files
above and checking the adapter's output is unchanged.

Every fixture's `credit`/`debit`/`fee`/`tax`/`amount` values satisfy the
adapter's documented accounting identity (`(credit - debit) + fee ==
±amount`) except `tax_exceeds_fee.json`, which is deliberately malformed
to exercise the conflict path.
