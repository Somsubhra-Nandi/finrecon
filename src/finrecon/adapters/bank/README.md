# Bank CSV ingestion adapter

```
RAW BANK CSV BYTES
        |
        v
BankCsvProfile          (csv_profile.py — one closed, explicit column
  - value_date_column     mapping per bank; nothing about a bank's CSV
  - value_date_format      shape is inferred at parse time)
  - narration_column
  - money_columns: DebitCreditColumns | AmountDirectionColumns
  - reference_id_column (optional)
  - currency / currency_column (optional)
  - thousands_separator (optional)
  - delimiter / encoding
        |
        v
parse_bank_csv()        (csv_parser.py)
  - decode + header/profile-column sanity check (fatal, whole-file, if
    the declared columns are not present in the CSV header at all)
  - per row: currency check -> narration -> value_date (exact declared
    strptime format, never re-attempted under another format) ->
    direction + amount (never guessed; see below)
  - row identity: reference_id_column when populated, else a
    deterministic content hash — never treated as proof of duplication
    (see "Row identity" below)
  - reference-identified rows: identical row content collapses; same
    reference with different content is excluded and recorded as a
    conflict. Fallback (no-reference) rows are never collapsed on content
    match — every one survives as its own record
  - bad row -> rejected with a reason, batch continues
        |
        v
CANONICAL FINRECON RECORDS         INGESTION MANIFEST
  result.records                    finrecon.adapters.bank.manifest
  (decision-eligible; validates     .BankIngestManifest
   against the unmodified           (row provenance, conflicts —
   BankRecord model)                 audit/debug only, never read by
                                      the decision engine)
        |
        v (separately)
result.rejected_rows   (ingestion review only: row_index, reason,
                         detail, raw_fields — never fed to the engine)
```

## Why a profile, not one parser

Every Indian bank's statement export uses different column names, a
different declared date format, and a different debit/credit convention.
None of that is safe to sniff from the file's own contents — see
`csv_parser.py`'s module docstring, "Absolutely no guessing", which is the
load-bearing design constraint of this whole adapter. A
`BankCsvProfile` makes every one of those choices an explicit, reviewable,
per-bank declaration instead of an inference.

## Row-scoped, not group-scoped

Unlike the Razorpay settlement recon adapter, one CSV row maps to at most
one canonical `BankRecord`, with no aggregation step. A bad row is
rejected and the batch continues (task brief §10) — there is no
blocking/non-blocking conflict classification to make, because nothing
here can contaminate a larger reconstructed object the way a bad recon row
can poison a settlement's breakup. The one multi-row case is conflicting
duplicate identity (see below), handled by excluding every row in the
conflicting group, never by guessing which copy is authoritative.

## Row identity

Two disjoint regimes (`csv_parser._resolve_row_identities`), deliberately
handled differently:

**Reference-identified rows.** `profile.reference_id_column`, when
declared and non-empty for a row, is a *trustworthy* source-provided
identity: `f"{profile_id}:ref:{reference}"`. Rows sharing one are assumed
to be the same real-world transaction.

**Fallback rows.** No reference column declared, or empty for this row.
Identity here is a SHA-256 over the row's *declared identity fields* —
value date, narration, and the money-column raw text —
(`f"{profile_id}:content:{digest}"`), but this is a **grouping key only,
never a transaction identity**. A content match proves two rows *look*
financially identical; it proves nothing about whether they are the same
physical transaction. Two legitimate, distinct bank rows can easily share
identical value date, narration, amount and direction — e.g. two separate
₹500 UPI payments posted the same day with the same narration — and
collapsing them would silently delete a real transaction. So a fallback
row is **never** collapsed or excluded on the basis of a content match:
every fallback row survives as its own `BankRecord`. Rows sharing a
content key are disambiguated only by an explicit, row-order-assigned
occurrence index — `f"{profile_id}:content:{digest}:{occurrence:04d}"` —
so ids stay unique within the statement without asserting equivalence. A
profile should still always declare `reference_id_column` when the source
provides one; content-hash identity is a fallback of last resort.

## Duplicate / conflict semantics

**Reference-identified rows only** — this is the one place duplicate
collapse or conflict can happen:

- Same reference, byte-identical row content (full raw row, not just the
  columns the profile projects) → collapsed to one record, the extra
  copies recorded in `manifest.duplicate_rows_dropped`.
- Same reference, *different* row content → every row in the group is
  excluded from `records`, each gets a `RejectedBankRow` with reason
  `conflicting_duplicate_bank_record_id`, and one
  `BankIngestConflict` names the whole group. Never resolved by
  preference.

**Fallback rows are exempt from both.** A content match among fallback
rows is neither a duplicate-collapse trigger nor a conflict — every
fallback row always reaches `records` (subject only to the ordinary
per-row validation in "Direction semantics" / "Money" / "Value date"
below, which is unrelated to identity). Row-order permutation of the
input therefore always preserves the resulting *financial multiset* (same
count of records, same set of (value_date, narration, amount, direction)
facts), even though which physical row receives which occurrence-indexed
id can differ.

## Direction semantics

- `DebitCreditColumns`: exactly one of the two columns populated
  determines direction. Neither populated → `neither_amount_populated`
  (not a financial movement). Both populated → `both_debit_and_credit_populated`
  — rejected, since no profile shipped here documents a real row shape
  where that combination has a defined meaning; a future profile that can
  document one would need a deliberate code change, not a silent default.
- `AmountDirectionColumns`: the direction column's raw value must be
  exactly one of the profile's declared `credit_values`/`debit_values`
  (case-sensitive, compared verbatim). Never inferred from the amount's
  sign. A value in neither declared set, or degenerately in both (a
  profile misconfiguration), is rejected.

## Money

Rupee decimal text → `Paise.from_rupees` — the same exact-decimal boundary
conversion used everywhere else in this codebase. Never `float(text) *
100`; sub-paise precision is rejected exactly as it is elsewhere.
`profile.thousands_separator`, when declared, is stripped literally before
conversion — an explicit formatting declaration, not a guess; left `None`
by default.

## Currency

`BankRecord` itself carries no currency field — the canonical model
assumes single-currency reconciliation. `profile.currency` documents the
assumption; `profile.currency_column`, when declared, is checked against
it per row (`unsupported_currency` on mismatch) but never written into the
canonical record.

## Value date

Populated from the declared `value_date_column` using
`datetime.strptime(raw, profile.value_date_format)` — matched exactly,
once, never retried under a second format on failure. This task
deliberately does not change how Stage 2/Stage 3 consume `value_date` —
see `csv_parser.py`'s module docstring and the task brief's §6: promoting
this into new structural evidence, if warranted, is separate follow-up
work.

## Provenance

`BankIngestManifest` (in `manifest.py`) reuses
`finrecon.adapters.manifest.ManifestModel` — the same strict/frozen/
closed-schema base the Razorpay adapter's sidecar uses — but is otherwise
a purpose-built, narrower shape: no `ConformanceReport` (nothing here
reconstructs a total from parts) and no blocking/non-blocking conflict
distinction (see "Row-scoped, not group-scoped" above). Every row, kept or
rejected, gets a `BankRowProvenance` entry: `row_index`, `row_fingerprint`
(SHA-256 over the full raw row), `produced`, `source_fields_used`, and
`dropped_fields` (every CSV header column present in the file but outside
the profile's closed mapping — automatic, since the profile already
declares its columns exhaustively). Never read by the reconciliation path.

## ICICI: not shipped, and why

This task's brief was explicit: build the generic adapter first, then add
*one* concrete bank profile **only if its CSV schema can be verified from
a trustworthy source**, and otherwise **stop and report the gap** rather
than fabricate column names.

I could not establish a trustworthy ICICI CSV export schema in this
session:

- ICICI's own developer/API documentation
  (`developer.icicibank.com`, the `ixpress.icicibank.com` API-Banking
  product pages) was not reachable from this environment (DNS resolution
  failed for both hosts).
- ICICI's public help-center and corporate-banking marketing pages
  (`icicibank.com`/`icici.bank.in`) describe *that* statements can be
  downloaded as CSV/Excel/BAI from net banking, but do not publish the
  actual column names or date format anywhere I could fetch.
- Third-party statement-converter sites and blogs converge, inconsistently,
  on something like `Date, Narration, Chq./Ref.No., Value Dt., Withdrawal
  Amt., Deposit Amt., Closing Balance` — but these are not ICICI
  documentation, are not mutually consistent, and are exactly the kind of
  secondary source the task brief's "trustworthy source" bar is meant to
  exclude.
- One open-source project (`Shivapande25/Banking-Statement-Analyzer`) that
  claims to parse ICICI statements turned out to parse *PDF-extracted*
  tables (via pandas, with numeric column suffixes from table
  concatenation) using `pd.to_datetime(..., dayfirst=True)` — i.e. pandas'
  own date-format *inference*, not a declared format. That is precisely
  the guessing this task forbids (§2), so it cannot be used as evidence
  for a `value_date_format` string either.

So, per the brief's own contingency: the generic `BankCsvProfile` +
`parse_bank_csv` adapter is complete and tested against synthetic data,
and **no ICICI profile is shipped**. Building one would mean fabricating
column names this session could not verify. If a real ICICI CSV export
sample (or authoritative documentation) becomes available — checked into
`fixtures/bank/` and clearly labeled — a concrete `BankCsvProfile` for it
is a small, mechanical follow-up: declare the columns, the exact date
format, and the debit/credit convention it actually uses.
