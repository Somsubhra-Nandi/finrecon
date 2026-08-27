# Real Razorpay input vs. our canonical model — gap analysis and adapter plan

Scope: what changes when the five synthetic JSONL files under
`benchmark/datasets/<split>/` are replaced by a real merchant's Razorpay
data and a real bank statement. Razorpay-side field names below are from
the public API reference (see Sources). Bank-side statements are **not** a
Razorpay artifact and have no single format; that asymmetry is the largest
finding here.

## 1. Summary of the gap

Four differences are structural — they change the shape of the data, not
just its field names — and each is a place the current pipeline would be
silently wrong rather than loudly broken.

1. **`Settlement.breakup` does not exist in the Razorpay API.** The
   settlement entity is flat. The break-up is a *separate*, period-keyed
   report — one row per settled transaction — that must be grouped by
   `settlement_id` to reconstruct our nested shape.
2. **Timestamps are Unix epoch integers in the API and bare rendered
   strings in dashboard reports.** Our normalizer interprets a naive
   datetime as UTC. The reports carry no timezone in the string and the
   docs state no convention, so an unlocalised local timestamp is a silent
   multi-hour shift onto the value-date rule and the ±1-day candidate
   window. Ambiguous date *formats* (`03/07/2026`) are the same class of
   bug and fail even more quietly.
3. **`fee` and `tax` are per-transaction columns, not break-up line types.**
   Our `SettlementLineType` treats them as line kinds with one aggregate
   line each per settlement.
4. **`settlement_id` is the grouping key; `settlement_utr` is the bank-side
   join key and is *not* guaranteed per row.** In Razorpay's own recon
   sample the `adjustment` row carries `settlement_utr: null` while the
   payment, refund and transfer rows in the same settlement all carry
   `1568176960vxp0rj`. So UTR must be resolved per settlement group, not
   read off any single row. Our `dev` split leaves `utr` null entirely and
   joins on `settlement_id` recovered from narration; `v4-pilot` populates
   it. Real reconciliation is UTR-first against the bank, and the
   degraded-reference problem is the tail case, not the trunk.

## 2. Field-level comparison

### 2.1 Order

| Ours (`models/order.py`) | Razorpay order entity | Note |
|---|---|---|
| `order_id: str` | `id` (`order_<14 base62>`) | ours is `ORD-dev-000001`; format only, no semantic gap |
| `amount: Paise` | `amount` (integer subunits) | **exact match** — both integer paise |
| `currency: str = "INR"` | `currency` (ISO-3) | match |
| `status` created/attempted/paid | `status` created/attempted/paid | **exact match**, all three values |
| `created_at: datetime` | `created_at` (Unix epoch int) | representation differs |
| — | `amount_paid`, `amount_due`, `attempts`, `receipt`, `notes`, `entity` | not modelled; `receipt` is the merchant's own key and is the field most likely to matter for a real merchant's internal recon |

### 2.2 Payment

| Ours (`models/payment.py`) | Razorpay payment entity | Note |
|---|---|---|
| `payment_id`, `order_id`, `amount`, `currency` | `id`, `order_id`, `amount`, `currency` | match |
| `status` created/authorized/captured/failed/refunded | `status` (same vocabulary) | match |
| `method: str \| None` | `method` (card/netbanking/wallet/upi/emi) | ours is untyped and null in every generated row |
| `created_at` | `created_at` (epoch) | representation differs |
| — | `captured` (bool) | **separate from `status`** — a real payment can be `authorized` and not captured; we collapse this |
| — | `amount_refunded`, `refund_status` | partial-refund state we do not carry |
| — | `fee`, `tax` | per-payment Razorpay fee; in our model fee/tax live only on the settlement break-up |
| — | `acquirer_data.rrn` / `auth_code` / `upi_transaction_id` | **the acquirer's own reference** — a second, independent reference axis our evidence engine has never seen |
| — | `invoice_id`, `international`, `card_id`, `bank`, `wallet`, `vpa`, `email`, `contact`, `notes`, `error_*` | not modelled |

### 2.3 Refund

| Ours (`models/refund.py`) | Razorpay refund entity | Note |
|---|---|---|
| `refund_id`, `payment_id`, `amount` | `id`, `payment_id`, `amount` | match |
| `status` pending/processed/failed | `status` pending/processed/failed | **exact match** |
| `created_at` | `created_at` (epoch) | representation differs |
| — | `speed_requested` / `speed_processed` (normal/optimum/instant) | **instant refunds settle on a different rail and may never appear in a settlement break-up at all** |
| — | `acquirer_data` (RRN, ARN or UTR) | a refund carries its own reference number |
| — | `batch_id`, `receipt`, `currency`, `notes` | not modelled |

### 2.4 Settlement — the structural break

Ours:

```json
{"settlement_id":"setl_v4pilot_000001","utr":"HDFCRT8939876856","amount":3421409,
 "created_at":"2026-07-19T19:04:00",
 "breakup":[{"type":"payment","amount":3801287,"reference_id":"pay_v4pilot_000001"},
            {"type":"fee","amount":-72224,"reference_id":null},
            {"type":"tax","amount":-13000,"reference_id":null},
            {"type":"transfer","amount":-294654,"reference_id":null}]}
```

Razorpay settlement entity — flat, no break-up:

| Field | Type |
|---|---|
| `id` (`setl_…`), `entity` | string |
| `amount` | integer subunits — net credited |
| `status` | `created` / `processed` / `failed` |
| `fees`, `tax` | integer; **typically 0** on a normal settlement, because fees are already netted per transaction |
| `utr` | string |
| `created_at` | Unix epoch |

The break-up comes from the settlement recon report
(`GET /v1/settlements/recon/combined?year=&month=&day=`, and the equivalent
dashboard CSV/XLSX). One row per settled transaction:

`entity_id`, `type` (payment / refund / transfer / adjustment), `debit`,
`credit`, `amount`, `currency`, `fee`, `tax`, `on_hold`, `settled`,
`created_at`, `settled_at`, `settlement_id`, `description`, `notes`,
`payment_id`, `settlement_utr`, `order_id`, `order_receipt`, `method`,
`card_network`, `card_issuer`, `card_type`, `dispute_id`.

Consequences for the adapter:

* Our `breakup` is a **GROUP BY `settlement_id` over recon rows**. The
  endpoint is queried by *period*, not by settlement: `year` and `month`
  are required, `day` is optional, and `count` (1–1000) / `skip` paginate.
  So a month is one paginated fetch, not thirty daily calls. The adapter
  must still paginate to completion and detect duplicate `entity_id`s
  across pages before grouping, because a partial fetch yields a settlement
  whose break-up is silently short.
* **UTR resolution is a per-group decision.** Collect the distinct non-null
  `settlement_utr` values within each `settlement_id` group: exactly one is
  the settlement's UTR; zero means fall back to the settlement entity's
  `utr` (and if that is unavailable, the UTR is unknown, not invented);
  more than one is an ingestion contradiction. See §4.5 for how a
  contradiction should be handled without stalling the batch.
* Recon has **separate `debit` and `credit` columns**; our line `amount` is
  signed. Mapping: `signed = credit - debit`.
* Recon has **`fee` and `tax` per row**; we have one aggregate `fee` line
  and one aggregate `tax` line per settlement. Either the adapter sums them
  (loses per-payment attribution) or emits one fee line and one tax line
  per source row (changes break-up cardinality, and every test that assumes
  one fee line per settlement).
* Recon `type` has **four** values; our enum has six, because `fee` and
  `tax` are types for us and columns for them.
* `on_hold` rows, reserve-balance movements and negative settlements
  (refunds exceeding collections) have no representation in our model.
  **Do not assume `sum(breakup) == amount` holds on real data** until it is
  measured; the generator enforces it by construction.
* `dispute_id` means chargebacks reach the break-up. We have no dispute
  line type.

### 2.5 Bank record — not a Razorpay artifact

Ours: `bank_record_id`, `amount: Paise`, `direction`, `narration` (raw free
text, held byte-identical), `value_date`.

Reality: the merchant's bank supplies this, in one of

* netbanking CSV/XLS export — column names differ per bank, dates as
  `DD/MM/YYYY`, amounts as rupee decimal text, separate Withdrawal/Deposit
  columns rather than a direction flag;
* **MT940 / MT942** — `:61:` carries value date, entry date, D/C mark and
  amount as *structured* subfields; `:86:` carries the narration;
* **ISO 20022 camt.053** — `<ValDt>`, `<Amt Ccy>`, `<CdtDbtInd>`,
  `<RmtInf>` as structured XML elements;
* a corporate banking API (ICICI / HDFC / Axis), each with its own schema.

Our five-field shape maps cleanly onto all of these. The important finding
is in the next section.

## 3. What this means for the structural evidence rules

`validator.v3` admits two structural relations, both parsed out of
narration text: an explicit `VALDT DDMONYY` field and an explicit
`RFND rupees.paise` field. `generator_v4/narration.py` labels every
template `SOURCE_INFORMED_SYNTHETIC` and claims no verbatim bank capture,
so this is a declared convention, not an observed one. Against real data:

* **Value date gets *stronger*, not weaker.** In MT940 `:61:` and camt.053
  `<ValDt>` the value date is a first-class structured field — exactly the
  "explicit, labelled, parsed, compared for equality" semantics v3
  declares, with no text parsing and no invented convention. Real bank
  statement CSVs also carry a Value Date column. The rule's *semantics*
  survive intact; only its *source* moves from narration to a column.
* **`RFND rupees.paise` does not survive.** No bank puts a labelled refund
  break-up amount in a settlement credit narration. Real narrations for a
  Razorpay credit look like `NEFT-<UTR>-RAZORPAY SOFTWARE PVT LTD` or an
  `MMT/IMPS/...` string, typically truncated to 35–100 characters. On real
  data this relation will simply never fire, and every case it currently
  resolves (10 of 48 in the v4 pilot) reverts to escalation. That is safe —
  it fails closed — but the pilot's 48/64 resolution rate is **not** a
  prediction of real-world coverage.
* **A third axis appears that we have never modelled**: `acquirer_data.rrn`
  on payments and the ARN/UTR on refunds. Real bank narrations frequently
  carry an acquirer reference rather than the settlement UTR. This is a new
  closed reference relation, and it is probably worth more than `RFND` was.

## 4. Adapter design

### 4.1 The one design decision that matters

Put the adapter **in front of `loader.py`**, emitting the same five JSONL
files, and change nothing downstream. The frozen v3 benchmark
(`f9eb877…4fc5b`), the 1635 tests, and the validator's safety properties
all stay valid, because the engine's input contract is unchanged. The
adapter then gets its own fixture corpus and its own tests, and a bug in it
can never invalidate a benchmark result.

The alternative — teaching the pipeline to read Razorpay shapes directly —
couples the freeze protocol to a third party's schema. Do not do it.

```
src/finrecon/adapters/
  base.py            # SourceAdapter protocol: bytes -> canonical records + IngestManifest
  razorpay/
    api.py           # /v1/orders, /v1/payments, /v1/refunds, /v1/settlements
    recon.py         # /v1/settlements/recon/combined -> group by settlement_id -> breakup
    report_csv.py    # offline path: dashboard CSV/XLSX, same output as recon.py
  bank/
    csv_profile.py   # declarative per-bank column mapping
    mt940.py
    camt053.py
  profiles/          # one file per bank: column names, date format, sign convention
  manifest.py        # per-field provenance + content fingerprint of the source bytes
```

### 4.2 Non-negotiable rules for the adapter

These come straight from invariants the codebase already enforces, and are
the places a naive adapter would quietly poison the financial path.

* **No float, ever.** Read CSV/XLSX as text (`csv` module, or pandas with
  `dtype=str`) and convert through `Paise.from_rupees(str)`. Excel and
  pandas both turn `"1234.50"` into a binary float by default, and
  `models/money.py` exists precisely to reject that. The API path is
  easier: `amount` is already integer subunits and maps to `Paise(int)`
  with no conversion at all.
* **Never produce a naive datetime from a rendered report.** Epoch integers
  are absolute — convert with `datetime.fromtimestamp(v, tz=timezone.utc)`.
  Rendered report timestamps carry no timezone in the string, and the
  dashboard data-schema page types them only as "Date/Time" with **no
  documented timezone convention**. Do not assume IST. Each input profile
  must *declare* its timestamp semantics, and an undeclared timezone is a
  configuration error that refuses the ingest rather than a default.
  `normalize_timestamp` assumes naive means UTC, so an unlocalised local
  string is a silent multi-hour shift landing directly on the value-date
  equality check.
* **Declare the date *format* too, not just the timezone.** `value_date` is
  a date, so no timezone applies — but `03/07/2026` is a valid date under
  both `DD/MM` and `MM/DD`, and both parse without error. This is worse
  than the timezone bug: a shifted timestamp is wrong by a fixed offset, an
  ambiguous date is wrong only sometimes and never raises. The profile
  states the format; the adapter never sniffs it.
* **Project explicitly; the canonical models are `extra="forbid"`.** Every
  Razorpay field that is dropped must be recorded in the ingest manifest,
  not silently discarded. Dropping `captured`, `amount_refunded` and
  `acquirer_data` is a decision, and it should be a written one.
* **Be byte-deterministic.** Sorted keys, stable record ordering, canonical
  JSON. `loader.py` fingerprints the visible files and batch identity is
  checked against it; a non-deterministic adapter makes that fingerprint
  meaningless.
* **Preserve narration byte-identically.** `normalize/records.py` holds it
  raw on purpose, and `validator.v3` records narration spans and offsets as
  provenance. An adapter that strips or re-cases narration invalidates
  every recorded offset.

### 4.3 The bank structured-field question

MT940 and camt.053 hand us a value date as a structured field. Two ways to
get it into the evidence engine:

* **Render it into the narration** as `VALDT 19JUL26` so the existing
  regex fires. Rejected: it fabricates source text, and `validator.v3`
  records raw spans and offsets as provenance. Those offsets would then
  point at characters no bank ever sent, which is a lie about the source.
* **Add `structured_fields: Mapping[str, str] = {}` to `BankRecord`**
  (additive, defaulted, so existing JSONL and existing fingerprints are
  unaffected), and let `evidence/structural.py` admit a value-date fact
  from *either* a narration token or a declared structured field, under the
  same closed-set semantics. **Recommended.** It keeps provenance honest
  and makes the real-data path stronger than the synthetic one.

This is the only change to the core models I would make, and it needs the
validator and Stage-4 tests re-run before it is trusted.

### 4.4 Suggested build order

1. `adapters/razorpay/api.py` + `recon.py` against **recorded fixtures**
   (checked-in sample JSON captured from the docs' response examples), with
   a schema-conformance test. No network in tests.
2. `adapters/bank/csv_profile.py` with one profile, plus `mt940.py`.
3. An `ingest` CLI: `python -m finrecon.adapters.ingest --split real-pilot`
   writing the five JSONL files, then run the existing
   `reconcile` / `investigate` / Stage-4 path unchanged.
4. A **conformance report** that measures, on the first real batch, the
   four things this document cannot answer.

Explicitly **not** now: MT940, camt.053, bank-specific corporate APIs, the
remaining Razorpay entity endpoints, spreadsheet column inference, and
RRN/ARN evidence. All are good future work and bad submission-week work.

### 4.5 Two places this design can quietly contradict itself

**Ingestion contradictions must not fail the batch.** A settlement group
with two distinct UTRs, a break-up that does not sum, or an unparseable row
is a fact about *one settlement*. Aborting the ingest means one malformed
adjustment row blocks reconciliation of every other settlement in the
period — a worse operational failure than the one being prevented. Record
the contradiction on the record, leave `utr` as `None`, and let the
existing escalation path do what it already does well. "Fail closed" in
this codebase has always meant *escalate this case*, not *stop the run*;
the adapter should not invent a second, harsher meaning.

**Row-level provenance is a sidecar, not engine input.** Preserving which
recon row produced a given ₹29 fee is right, and the aggregated canonical
line cannot answer it. But `loader.py` reads exactly five files and that
contract is the thing protecting the frozen engine. So the provenance
index is written *alongside* the five files, keyed by record id, for audit
and demo — and the engine never reads it. The moment provenance is needed
*inside* a decision, the honest move is to widen the canonical model
deliberately (as §4.3 proposes for `structured_fields`), not to widen the
loader.

## 5. What we cannot know until real data arrives

Each of these is currently true by construction in the generator and may
simply be false in production. Measure them before quoting any accuracy
number on real data.

* Does `sum(breakup) == settlement.amount` hold, once `on_hold`, reserve
  balance, disputes and negative settlements exist?
* What fraction of bank credits carry the settlement UTR intact in the
  narration? This single number determines whether the T1/T2/T3 difficulty
  tiers describe reality or only describe the generator.
* Is the settlement→bank-credit relation one-to-one? Banks aggregate
  same-day credits, and instant settlements arrive on a separate rail with
  their own UTR.
* How often does the narration carry an acquirer RRN instead of the UTR?

A caution on building fixtures from the docs: the recon sample's
`settlement_utr` is `1568176960vxp0rj`, which is a test-mode value and
looks nothing like the bank-issued UTR format the settlement docs describe
(`KKBKH14156891582`). A fixture copied from the documentation therefore
proves the *transformation* is correct; it does not prove the *shapes* are
real. That distinction is the same one this document makes about the
benchmark, and it applies to the adapter's own test corpus too.

## 6. Honest framing for the submission

The engine's safety properties — closed evidence sets, zero unsafe
auto-match, escalation on contradiction, integer-paise money, no float
anywhere — are properties of the *decision path* and transfer to real data
unchanged. The **coverage** numbers do not: they are measured against a
generator whose narration conventions we declared ourselves. The right
claim is "48/64 resolved with zero wrong answers on a benchmark whose
difficulty we control, and here is the adapter and the conformance report
that will tell us the real number on day one" — not "48/64 on Razorpay
data".

## Sources

- Settlement entity — https://razorpay.com/docs/api/settlements/entity/
- Fetch Settlement Recon Details — https://razorpay.com/docs/api/settlements/fetch-recon/
- Settlements API index — https://razorpay.com/docs/api/settlements/
- Orders entity — https://razorpay.com/docs/api/orders/entity/
- Refunds entity — https://razorpay.com/docs/api/refunds/entity/
- Payments API — https://razorpay.com/docs/api/payments/
- Dashboard reports — https://razorpay.com/docs/payments/dashboard/reports/
- Report data schema — https://razorpay.com/docs/payments/dashboard/reports/data-schema/
