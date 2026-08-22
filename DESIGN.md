# FinRecon — Design Document

**Razorpay AI Buildathon · Track 04 — AI Finance Controller**

> The goal isn't to maximize matches. It's to maximize safely automated reconciliation while minimizing incorrect financial decisions.

| | |
|---|---|
| Version | **v4 — frozen** |
| Build window | 14 days |
| Track bar | *"Close one finance-ops loop across a 50+ record batch of synthetic data, reporting its match rate and the exceptions it could not resolve."* |
| Judged on | Throughput + measured accuracy + an honest exception list |

**Changes from v1:** explicit track-required match rate (§5.3); UTR modelled as a first-class entity and used as the tier gradient (§5.2); the LLM is now a bounded investigation agent, not a single-shot parser (§4.1); determinism claim split between fixture and live runs (§4.6); evidence policy is predicates-first, scoring only if earned (§4.3).

**Changes from v3:** T1+T2 arithmetic corrected to 500 (§5.4); T0/T1 made mutually exclusive on direct-key survival, with disjointness asserted by the generator (§5.2); calibration retargeted from adjudication to extraction (§6.3); day-8 agent go/no-go gate encoded in the schedule (§9).

**Changes from v2:** the validator now receives the **complete deterministic candidate set**, closing the agent's fishing-by-omission channel (§4.1, §4.3); record / case / batch defined (§5.0); ablation arm C is a true single-shot extraction baseline, not a one-step agent (§5.5); match rate counts **automatic** reconciliations only (§5.3); the ₹1 delta tolerance is replaced with declared paise-level rounding rules (§4.3); base idempotency moves into Stage 2 (§9).

---

## 1. Problem

A merchant's money leaves a trail across four systems that never agree with each other:

```
ORDER            PAYMENT           SETTLEMENT        BANK CREDIT
ORD-123    →     pay_abc     →     setl_091    →     ₹4,150 CR
₹1,000           ₹1,000            net of fees       UTR 928392...
                                   + UTR             free-text narration
```

Four structural facts make this hard:

1. **Amounts legitimately don't match.** Gateway fee, GST on fee, TDS, adjustments and refunds sit between gross payment and bank credit. A ₹4,250 batch of payments arrives as a ₹4,150 credit, and that ₹100 gap is correct.
2. **Cardinality is not 1:1.** One bank credit covers many payments. One order can be paid in two attempts. One settlement can be split.
3. **The canonical join key is fragile.** Razorpay's settlement entity carries a UTR — the cross-bank reference intended for exactly this reconciliation. When the UTR survives intact, matching is trivial. It frequently doesn't: banks truncate it, embed it in narration, reorder tokens, or omit it.
4. **The bank's side is free text.** What's left is a narration string every bank mangles differently:
   ```
   RZPY*ORD293 UPI/98273192
   NEFT CR-RZRPAY-SET98372-MUM
   RZPY/SETL/8172 REF:PAY88/REV
   CR NEFT-RZPY-STLMNT/29X17/REV-8271
   ```

So a finance analyst spends their week in spreadsheets asking *"what is this ₹38,221?"* and *"why are my books short by ₹5,400?"*

**The failure mode that matters.** Naive automation here is worse than the spreadsheet. A system that confidently mismatches money issues a false clean bill of health, and the error surfaces at audit rather than at the point of failure. An escalated case costs an analyst five minutes; a wrong auto-match costs a restatement.

This asymmetry is the central design constraint of the entire system.

---

## 2. Solution

**FinRecon is a deterministic reconciliation controller with a bounded AI investigation agent attached to the cases rules cannot settle.**

The pitch is deliberately *not* "AI reconciles your books."

```
unresolved case → deterministic candidate set (immutable)
    → agent gathers ADDITIONAL evidence with read-only tools
    → validator sees the full candidate set + base facts + raw agent evidence
    → policy gate → resolve or escalate
```

**The agent decides what to look at. Deterministic code decides what it means and whether money moves.**

The agent has no financial authority. It cannot resolve, cannot score, cannot characterize its own findings to the decision layer, and — critically — **cannot narrow the candidate set**. It may only add evidence to an immutable case snapshot. Every resolution is made by reproducible code.

### What "closing the loop" means here

Not "upload CSV → get report." The loop closes:

```
records arrive → normalize → match → investigate → auto-resolve what's provable
    → escalate what isn't → human resolves in UI with a reason
    → resolution persists → re-run does not re-raise it → audit trail
```

That last leg — persisted human resolution surviving a re-run — separates a controller from a report generator.

### Non-goals

Named explicitly so scope doesn't drift:

- Not a chatbot. No "ask anything about your finances" surface.
- Not multi-currency.
- Not the chargeback lifecycle.
- Not a production connector to real Razorpay APIs. Synthetic data only, modelled on the documented entity shapes.
- Not maximizing match rate. See the principle at the top.

---

## 3. Architecture

```
                    SYNTHETIC MERCHANT DATA
     orders · payments · settlements (+UTR) · refunds · bank
                            │
                            ▼
              ┌──────────────────────────┐
              │  NORMALIZATION           │
              │  integer paise, UTC,     │
              │  canonical record model  │
              └────────────┬─────────────┘
                           ▼
              ┌──────────────────────────┐
              │  TIER 1 · EXACT MATCH    │   deterministic
              │  settlement_id, intact   │   zero LLM cost
              │  UTR, clean references   │
              └────────────┬─────────────┘
                           │ residual
                           ▼
              ┌──────────────────────────┐
              │  TIER 2 · TOLERANCE      │   deterministic
              │  fee/GST/TDS derivation, │   still no LLM
              │  date windows, rounding  │
              └────────────┬─────────────┘
                           │ residual
                           ▼
              ┌──────────────────────────┐
              │  CANDIDATE GENERATOR     │   deterministic
              │  amount/time blocking    │   → IMMUTABLE
              │  → complete candidate    │     case snapshot
              │     set + base facts     │
              └──────┬──────────────┬────┘
                     │              │
                     │              └──────────────┐
                     ▼                             │

     ┌─────────────────────────────────────────────┐
     │  TIER 3 · INVESTIGATION AGENT               │
     │  bounded loop, read-only tools, max N steps │
     │                                             │
     │   parse_bank_narration()                    │
     │   lookup_candidate_records()                │
     │   inspect_settlement_breakup()              │
     │   inspect_refunds()                         │
     │   compute_expected_net()                    │
     │   compare_reference_tokens()                │
     │                                             │
     │  emits: RAW TOOL OUTPUTS + trajectory       │
     └────────────────────┬────────────────────────┘
                          ▼
              ┌──────────────────────────┐   ◄───────────┘
              │  DETERMINISTIC VALIDATOR │   inputs:
              │  predicates over the     │   1 complete candidate set
              │  COMPLETE candidate set  │   2 immutable base facts
              │  + raw agent evidence    │   3 raw agent evidence
              └────────────┬─────────────┘   never agent prose
                           ▼
              ┌──────────────────────────┐
              │  POLICY GATE             │   hard blockers,
              │  value-aware thresholds  │   value ceiling
              └───────┬──────────┬───────┘
                      │          │
                 provable    not provable
                      ▼          ▼
              AUTO-RESOLVE    EXCEPTION QUEUE
                      │          │
                      │          ▼
                      │    HUMAN RESOLVES
                      │    (reason recorded)
                      │          │
                      └────┬─────┘
                           ▼
                    AUDIT TRAIL + REPORT
                    persists across re-runs
```

### Component responsibilities

| Component | Owns | Never does |
|---|---|---|
| Normalizer | Integer paise, UTC, canonical schema | Any matching |
| Tier 1 / Tier 2 | Provable matches by ID, UTR, arithmetic | Guess |
| Candidate generator | Blocking, shortlist of counterparties | Choose between them |
| Investigation agent | Chooses which **additional** evidence to gather | Decide, score, resolve, summarize for the validator, or remove candidates |
| Validator | Predicates over the complete candidate set + **raw** tool outputs | Consume model confidence, model prose, or a filtered candidate list |
| Policy gate | Resolve / escalate, value-aware | Be overridden by the agent |
| Ledger store | Resolutions, audit trail, idempotency | Compute metrics |

---

## 4. Working principle

### 4.1 The agent investigates; it does not adjudicate

Given an unresolved bank credit, the agent runs a bounded loop over read-only tools and decides *what to look at next* — check the narration, then pull the settlement break-up, then check whether a refund explains the residual, then compare reference tokens.

That choice-of-investigation is where the agency genuinely lives, and it's a real capability: the sequence needed for a truncated-UTR case differs from the one needed for an unexplained-variance case, and hardcoding every path is exactly the brittleness the LLM is there to absorb.

**Hard bounds:**

| Bound | Value |
|---|---|
| Tools | Read-only. No writes, no resolution, no state mutation. |
| Step budget | Fixed max. Exhausted → escalate, never → best guess. |
| Output | Raw tool outputs + full trajectory, both logged |
| Schema | Pydantic-validated. Validation failure → escalate. |
| Candidate set | Immutable. The agent may add evidence; it can never remove or hide a candidate. |
| Scope | Only cases surviving Tier 1 and Tier 2 |

**The critical constraint: the validator reads raw tool outputs, not the agent's summary.**

This is not a stylistic preference. An agent with tool access and an ambiguous case will assemble a plausible-looking evidence bundle for the wrong candidate, because constructing a coherent story is what it's optimized to do. A single-shot parser can only misread text; an investigator can go fishing. Forcing the decision layer to read the primary evidence rather than the narrative removes most of that surface.

**But raw evidence alone is not enough — the agent can still fish by omission.** Given candidates A, B and C, an agent that investigates A thoroughly and simply never calls the tool that would surface B's contradiction has produced entirely truthful raw outputs that are nonetheless a biased case file. It can no longer lie about evidence; it can still *select* it.

So the candidate set is produced deterministically, before the agent runs, and handed to the validator directly:

```
deterministic pipeline
    ↓
immutable case snapshot  =  complete candidate set + base evidence
    ↓                                    │
  AGENT  ── additional raw evidence ──┐  │
                                      ▼  ▼
                                  VALIDATOR
```

The agent may enrich the case. It may not shrink it.

### 4.2 Model confidence is never an execution gate

**Rejected:**
```python
if agent.confidence >= 0.95:
    auto_resolve()          # ← model vibes control money
```

**Adopted:**
```python
evidence = agent.investigate(case)      # raw tool outputs only
if validator.is_provable(evidence) and not hard_blockers(case):
    auto_resolve()
else:
    escalate()
```

The agent emits a self-reported confidence. It is logged and measured, never acted upon. See §6.3.

### 4.3 Predicates first; scoring only if earned

Start interpretable:

```
AUTO-RESOLVE only if:
    exactly one surviving candidate
AND amount reconciles exactly under known fee/tax/refund rules
AND a strong reference link exists (intact UTR, recovered token, or entity match)
AND no hard blocker
AND value policy permits
```

A weighted score is introduced **only if** dev-set analysis shows predicates leave material coverage unclaimed. Do not build a scoring model because a design document contains one.

**Hard blockers** — no evidence overrides these:
- More than one candidate satisfies the predicates — evaluated against the **complete deterministic candidate set**, not only the candidates the agent chose to inspect
- Any unexplained delta greater than **0 paise**. A delta is "explained" only when a declared rounding or derivation rule accounts for it exactly. A 1-paise GST rounding delta under a stated rounding policy is explained; a 37-paise gap that is "probably rounding" is not.
- Value above the auto-resolution ceiling
- Schema validation failure
- Counterparty already resolved in this run
- Agent step budget exhausted

### 4.4 Abstention is a first-class output

```
Order A   ₹2,499   10:00:00
Order B   ₹2,499   10:00:00
Bank credit ₹2,499 · no UTR · no reference · no customer metadata
```

There is no correct automatic answer. The required output is escalation, and the benchmark scores it as a **pass**. A system that picks either order **fails that case**, regardless of which happens to be right.

### 4.5 Value-aware thresholds

The auto-resolution bar rises with the amount at stake. A ₹49 ambiguity and a ₹2,00,000 ambiguity do not deserve identical treatment.

### 4.6 Determinism — stated precisely

Two different guarantees, not one:

| Mode | Guarantee |
|---|---|
| `make eval` (committed fixtures) | **Byte-identical.** Full pipeline reproducible from a clean clone, no API key. This is the source of every number in the README. |
| `make eval-live` (real model) | **Not guaranteed byte-identical.** Hosted models drift regardless of temperature. Run as a periodic *drift check*; report metric divergence from the fixture run as a finding. |

Independent of the model:
- All money is **integer paise**. No floats in the financial path. This matches Razorpay's own API representation, where settlement amounts are expressed in the smallest currency unit.
- Same input batch → no duplicate matches, no re-raised resolutions. Asserted by `make test-idempotency`.

---

## 5. Benchmark design

**The benchmark is the deliverable.** The system is what generates numbers for it.

### 5.0 Terminology — record, case, batch

Defined once, because the track bar is phrased in records and the benchmark reasons in cases.

| Term | Definition |
|---|---|
| **Record** | One row: an order, payment, refund, settlement, or bank line |
| **Case** | One reconciliation decision, potentially spanning several records |
| **Batch** | A set of records processed together in one run |

Reported as:

> *890 reconciliation cases derived from 2,740 financial records in a single batch.*

This removes any ambiguity about clearing the "50+ record batch" requirement — the batch is two orders of magnitude past it, and the tier tables below count cases, not records.

### 5.1 Freeze protocol

```
1. Define corruption taxonomy + UTR degradation ladder   → committed
2. Build narration format library from real-world formats → frozen FIRST
3. Generate DEV set          → tuning happens here, only here
4. Generate FROZEN EVAL set  → different seed, same taxonomy
5. SHA-256 the frozen set    → committed to README
6. Generator stops changing  → changes go in CHANGELOG.md with rationale
7. Build against DEV. Report against FROZEN.
```

```
benchmark/
├── generator/
│   ├── narration_library.py     ← sourced & frozen before the agent exists
│   ├── utr_degradation.py
│   └── corruptions.py
├── manifests/  v1.json · CHANGELOG.md
├── datasets/   dev/ · frozen-eval/        ← SHA-256 in README
└── ground_truth/
```

**Methodological honesty** (goes in the README, not buried here): freezing prevents overfitting to *instances*, not to the *distribution* — the taxonomy still came out of my head. Two partial mitigations: the narration library is sourced from real-world formats and frozen before the parser exists, and a subset of narration strings is generated adversarially by a separate model with no knowledge of the implementation.

### 5.2 Difficulty tiers — graded by reference survival

The UTR gives the tiers a principled gradient instead of an invented one.

| Tier | Cases | Reference state | What it tests | Headline? |
|---|---:|---|---|---|
| **T0 — Direct key** | 350 | A usable direct join key survives: intact UTR *or* a clean `settlement_id` link | Basic correctness. An ID join. | No. Footnote. |
| **T1 — Derived** | 300 | **No usable direct join key.** Structured financial evidence remains | Reconciliation by derivation: fee/GST/TDS arithmetic, settlement break-up, refund offsets, date relationships, duplicate disambiguation | Yes |
| **T2 — Degraded reference** | 200 | UTR truncated, embedded in garbage narration, tokens reordered, reference partially mangled | Unstructured evidence recovery — **the AI showcase** | **Yes** |
| **T3 — Truly ambiguous** | 40 | No usable reference at all, plus multiple plausible matches | Correct refusal | Yes, never alone |

The tiers are **mutually exclusive by construction**, gated on what survives of the canonical reference:

```
direct key survives          → T0
no key, structure survives   → T1
key survives only degraded   → T2
nothing distinguishing       → T3
```

A case with a clean `settlement_id` is T0 by definition, never T1 — if a direct join resolves it, it is not a derivation problem. The generator asserts tier disjointness and the eval harness fails loudly if any case satisfies two tiers.

T0 is not headlined — it measures `pandas.merge`. T3 is deliberately small: a system that escalates everything scores 100% there, so the number is meaningless without T1/T2 coverage in the same sentence.

Synthetic schema mirrors the documented Razorpay settlement break-up: payment, refund, adjustment, fee, tax, transfer.

### 5.3 Metric definitions

Defined once, used consistently.

```
Match rate (track-required)  =  correct AUTOMATIC reconciliations
                                ────────────────────────────────────
                                cases with a uniquely resolvable
                                ground truth

Auto-resolution coverage     =  auto-resolved / total cases
Auto-resolution precision    =  correct auto-resolutions / all auto-resolutions
Escalation recall            =  correctly escalated / all truly-ambiguous cases
Unsafe auto-match rate       =  incorrect auto-resolutions / total cases
Value at risk                =  ₹ value of incorrect auto-resolutions
```

**Match rate is reported because the track bar explicitly asks for it.** It is not the headline. Two definitional points, both stated openly in the README rather than left for a reviewer to discover:

- T3 cases are excluded from the denominator by construction — they have no uniquely resolvable ground truth.
- The numerator counts **automatic** reconciliations only. A resolvable case the system escalates *lowers* match rate, and a human later resolving it in the exception queue does not repair the number. The human loop demonstrates operational closure; the benchmark measures the automated controller.

**Headline pair: auto-resolution precision and value at risk.** Coverage without precision is meaningless.

### 5.4 Results table (shape only — filled after the frozen run)

| Tier | Cases | Correct auto-resolve | Correct escalate | **Wrong auto-resolve** | ₹ at risk |
|---|---:|---:|---:|---:|---:|
| T0 Exact | 350 | TBD | TBD | TBD | TBD |
| T1 Structured | 300 | TBD | TBD | TBD | TBD |
| T2 Degraded ref | 200 | TBD | TBD | TBD | TBD |
| T3 Ambiguous | 40 | — | TBD | TBD | TBD |

```
Track-required match rate (T0–T2):  TBD%
```

Headline sentence form:

> Across **500 non-trivial cases** (T1+T2), FinRecon auto-resolved **X%** at **Y% precision**, with **N unsafe auto-matches** exposing **₹Z** of **₹Total** evaluated. On 40 intentionally unresolvable cases it correctly refused **M**. Overall match rate on resolvable cases: **R%**.

### 5.5 Ablation — four arms, three of them nearly free

| Arm | Configuration | Cost to build | Hard-set coverage | Precision |
|---|---|---|---:|---:|
| A · Rules only | LLM path disabled | free | TBD | TBD |
| B · Rules + regex parser | Timeboxed regex narration parser | 4h | TBD | TBD |
| C · Rules + single-shot extraction | Raw narration → one LLM call → structured claims. **No tool loop.** | ~2h | TBD | TBD |
| D · Rules + investigation agent | Bounded tool loop over settlement, refunds, references | — | TBD | TBD |

Arm C is a **genuine single-shot extraction baseline**, not the agent with its step budget clamped to 1 — those are not the same experiment. A one-step agent picks one tool and stops, which is a crippled agent; single-shot extraction is a different architecture that reads the narration and emits structured claims directly. Comparing D against a crippled version of itself would flatter it. Same model and same output schema across both, so the only variable is multi-step tool use.

**The C-vs-D delta is the most interesting number in the project** — the direct, measured answer to "does multi-step agency outperform one-shot extraction by enough to justify its latency and tokens?"

**The regex arm must be a good-faith baseline.** It is the one arm with an incentive to be a strawman, and any reviewer who has written bank-narration regex will spot a weak one immediately. Mitigation, stated in the README: *four hours of good-faith tuning against the dev set, timeboxed and logged.*

If D beats C by two points at 2.5× the cost, **report exactly that**, and let the recommendation follow the number — including the conclusion that the single-shot architecture is the one worth shipping. A measured negative result is a stronger submission than an unmeasured positive claim.

Full-policy coverage may be *lower* than rules-only because the policy layer deliberately escalates risky cases. That is the system working, and the README says so.

---

## 6. Instrumentation

### 6.1 Cost and call reduction

```
Records processed              1,000
Cases reaching the agent         TBD
Agent steps (mean / p95)         TBD
LLM calls (naive: 1,000)         TBD    → reduction: TBD%
Input / output tokens            TBD
Cost per 1,000 records          ₹TBD
Wall clock                       TBD s
```

Call reduction is the quantitative argument for deterministic-first architecture. It belongs in the video. Mean agent steps is the honest counterweight — agency costs tokens, and the number should be stated, not hidden.

### 6.2 Reproducibility without an API key

Reviewers will clone the repo. Most will not have credits or patience.

```bash
make eval        # committed fixtures, no API key, reproduces the exact README table
make eval-live   # real model; drift check only
```

Fixtures cache the **full agent trajectory** keyed by case ID plus prompt-chain hash, not just single completions. This is the multi-turn complication the agent introduces, and it is budgeted into Stage 3.

### 6.3 Calibration check — on extraction, not adjudication

The v3 agent doesn't pick winners, score, or adjudicate. So "agent confidence vs final reconciliation accuracy" compares two different things and isn't a coherent experiment.

Calibrate the one claim the model actually makes: **the extraction**.

```
Claim:  "the recovered reference token is UTR 8272910"

Model claims ≥ 95% confident  →  extraction actually correct TBD%   (n = TBD)
Model claims < 95% confident  →  extraction actually correct TBD%   (n = TBD)
```

Ground truth for this is free — the generator knows the true reference before degrading it — so the check costs ~20 minutes. Two numbers, not five buckets; there won't be enough T2 cases to support five bins.

Poor calibration *justifies* §4.2. A bad calibration result is a good engineering result. If it doesn't fit in the schedule, cut it outright: the C-vs-D ablation is the more interesting experiment and the architecture already never consumes model confidence.

---

## 7. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | One language end to end |
| Money | `int` paise | No float in the financial path; matches Razorpay's smallest-currency-unit representation |
| Data | pandas | Adequate at this scale |
| Schema | Pydantic v2 | Enforces tool I/O and agent output; validation failure → escalate |
| Agent | Claude via Anthropic SDK, tool use, bounded step loop | Read-only tools, full trajectory logged |
| Cache | Trajectory fixtures keyed by case + prompt-chain hash | `make eval` runs offline |
| Store | SQLite | Resolutions, audit trail, idempotency. Zero setup for reviewers. |
| API | FastAPI | Same language, fast |
| UI | Jinja2 + HTMX + Pico.css | Exception queue + summary header. No build step, no npm, ~6h. **Streamlit fallback if behind.** |
| CLI | Typer | `benchmark.py --records 1000` |
| Tests | pytest | Idempotency, policy gate, arithmetic invariants, tool read-only assertions |
| Repro | Makefile | `make eval`, `make eval-live`, `make test-idempotency` |

**There is no separate dashboard.** The exception queue carries a summary header showing operational facts only — ₹ reconciled, ₹ unresolved, auto-resolved count, pending count, exception age. It does **not** show accuracy: a production system has no ground truth. Accuracy lives in the benchmark. Getting this distinction right is itself a signal.

---

## 8. Repository layout

```
finrecon/
├── README.md                  ← metrics + architecture + repro command, first screen
├── Makefile
├── DESIGN.md
├── src/finrecon/
│   ├── normalize/
│   ├── matchers/              tier1_exact.py · tier2_tolerance.py
│   ├── candidates/
│   ├── agent/                 loop.py · tools.py · schemas.py · cache.py
│   ├── decide/                validator.py · policy.py
│   ├── ledger/                store.py · audit.py
│   ├── report/
│   └── api/
├── benchmark/                 (see §5.1)
├── eval/                      harness.py · ablation.py · calibration.py
├── fixtures/trajectories/
└── tests/
```

**README priority order.** A reviewer gives you ninety seconds:

1. One-line principle
2. Headline metrics sentence (§5.4)
3. Tiered results table + match rate
4. Architecture diagram
5. `make eval` — one command, no API key
6. What breaks in production (§10)
7. Everything else

---

## 9. Implementation plan

Two weeks, built around a hard cut line. The failure mode here is not building the wrong thing — it's building all the right things to 60%.

### Stage 0 · Day 1 — Foundations
- Repo, Makefile, canonical record schema, integer-paise money type
- **Narration format library sourced from real-world examples and frozen**
- Corruption taxonomy + UTR degradation ladder written down

*Exit:* taxonomy and narration library committed. Nothing else started.

### Stage 1 · Days 2–3 — Benchmark generator
- Orders / payments / settlements (with UTR) / refunds / bank across T0–T3
- Settlement break-up modelled on the documented entity shape
- Ground truth emitted alongside, hidden from the system
- Dev and frozen sets from different seeds; frozen set hashed and committed

*Exit:* SHA-256 in README. Generator stops changing.

> Load-bearing stage. If the data is trivially matchable, everything downstream is worthless. Do not rush it to reach the "interesting" part.

### Stage 2 · Days 4–5 — Deterministic core
- Normalization → Tier 1 (settlement_id, intact UTR) → Tier 2 (fee/GST/TDS derivation, date windows, rounding)
- Candidate generator with blocking, emitting the **immutable case snapshot** (complete candidate set + base facts)
- SQLite ledger + structured audit record for every decision
- **Base idempotency test lands here, not on day 12.** Process a batch, process it again, assert identical resolutions, no duplicate links, no duplicate audit rows. Idempotency is a storage-layer invariant, and retrofitting it after the agent exists is far more painful than asserting it the day the store is written.

*Exit:* T0 fully resolved, T1 partially, every decision auditable, `make test-idempotency` green.

### Stage 3 · Days 6–9 — Investigation agent and policy *(one day longer than v1)*

**This is the danger zone.** Trajectory caching in particular looks like two hours and takes a day.
- Read-only tool implementations, Pydantic I/O
- Bounded agent loop with step budget; exhaustion → escalate
- **Trajectory cache** — the multi-turn caching work the agent adds
- Validator over raw tool outputs; predicates first
- Policy gate: hard blockers, value ceiling, abstention path

#### END OF DAY 8 — AGENT GO/NO-GO GATE

A scheduled decision, taken deliberately while there is still time to execute the alternative well. Continue with arm D on day 9 **only if all four hold**:

- [ ] Bounded loop runs end to end on dev T2
- [ ] Trajectory caching works — `make eval` reproduces without an API key
- [ ] Validator consumes raw evidence plus the complete candidate set
- [ ] Step-budget exhaustion escalates correctly

**Otherwise, take the fallback:** ship arm C as the production architecture, demote D to an optional experimental ablation, and spend day 9 on evaluation robustness instead. Arm C plus the deterministic core, policy gate, benchmark, exception queue, human persistence and audit trail is already a complete Track 4 submission.

This gate exists to prevent the standard failure: *day 9 "maybe tomorrow" → day 10 "almost there" → day 11 evaluation isn't done → day 12 dead.*

*Exit:* T2 flowing end to end; nothing auto-resolves without passing the deterministic gate.

### Stage 4 · Days 10–11 — Evaluation harness
- Frozen run producing the tiered table
- All six metrics from §5.3, including track-required match rate
- Cost, tokens, agent steps, call reduction, wall clock
- `make eval` works from a clean clone with no API key

*Exit:* README table is real. **Feature freeze on the pipeline.**

### Stage 5 · Day 12 — Closing the loop, then whatever fits
Strict priority order. Stop when time runs out:

1. **Exception queue UI + persisted human resolution + re-run does not re-raise** *(the "closed loop" the track bar asks for — ~3h, do it first)*
2. **Ablation, four arms** *(arm C ≈ 2h; A is free)*
3. Extend idempotency: human resolves → re-run → exception stays resolved
4. Calibration two-number check
5. ~~Summary dashboard~~ — **cut to fund the agent.** The exception queue carries a summary header; a separate dashboard was decoration.

### Stage 6 · Days 13–14 — Submission. No code.
- README in priority order · architecture diagram · "what breaks in production" · 5-minute video

**Day 13 is a hard code freeze.** A polished submission of a smaller system beats a rushed submission of a bigger one, every time.

### Cut list — decided now, not under pressure

| Cut | Reason |
|---|---|
| Finance Q&A chatbot | Second product; makes it look like an LLM wrapper |
| Summary dashboard | Funds the agent's extra day; exception queue carries the loop |
| Multi-currency | Scope |
| Chargeback lifecycle | Scope |
| T3 at 150 → 40 cases | Gameable; those cases are worth more in T2 |
| Five-bucket calibration → two numbers | Not enough samples to be meaningful |

---

## 10. What breaks in production

Stated plainly in the README, unprompted:

> This benchmark does not reproduce every bank's narration format, partial file delivery, schema drift, late correction files, settlement reversals, the chargeback lifecycle, multi-currency accounting, bank holidays, or adversarial real-world data. The synthetic benchmark establishes reproducible system behaviour, not production equivalence.
>
> The corruption taxonomy was authored by the same person who built the matcher. Freezing the eval set prevents overfitting to instances, not to the distribution. The narration library was sourced from real-world formats and frozen before the agent existed, and a subset was generated adversarially by a separate model, but this only partially mitigates the concern.
>
> Reported numbers come from cached model trajectories. A live run against a hosted model may diverge; measured drift is reported separately.

Naming your own limitations is the cheapest credibility available, and "honest exception list" in the track bar is testing for exactly this instinct.

---

## 11. Risk register

| Risk | Signal | Mitigation |
|---|---|---|
| **Agent fishes by fabrication** — narrates evidence that isn't there | Unsafe auto-matches in T2 | Validator reads raw tool outputs, never agent prose |
| **Agent fishes by omission** — investigates one candidate, never surfaces the contradicting one | Unsafe auto-matches that look well-evidenced | Candidate set built deterministically before the agent runs and passed to the validator directly; agent can add evidence, never remove candidates |
| Generator too easy → meaningless metrics | T1 resolving near 100% on first run | Stage 1 is 2 full days; adversarial narration subset |
| Agent loop eats the schedule | Any of the four day-8 gate conditions unmet | **Day-8 go/no-go gate (§9, Stage 3).** Arm C is a complete shippable product; descope D to an ablation-only result and say so in the README |
| Regex baseline is a strawman | Arm B collapses implausibly | Timeboxed 4h good-faith tuning, logged |
| Agency buys nothing | Arm D ≈ Arm C | Report it honestly; the finding is itself a result |
| Scope creep eats days 13–14 | Still coding on day 12 | Hard freeze; Streamlit fallback on UI |
| Tool-use flakiness | Malformed tool calls in T2 | Pydantic validation → escalate path, not retry-forever |
| Reviewer can't run the repo | — | `make eval` with committed trajectories, no API key |

---

## 12. Demo script (5 minutes)

| Time | Beat |
|---|---|
| 0:00–0:30 | The problem. Four systems, one truth, ₹8,420 unexplained. |
| 0:30–1:15 | Architecture — **the agent investigates, deterministic code adjudicates.** Model confidence never gates money. |
| 1:15–2:45 | Live frozen-set run. Land on the tiered metrics table. |
| 2:45–3:15 | One agent trajectory, end to end: which tools it chose, in what order, and why that sequence recovered a truncated UTR. |
| 3:15–4:00 | Two more exceptions: one correctly refused; **one the system got wrong, and how the harness caught it.** |
| 4:00–4:30 | Human resolves an exception → re-run → it does not re-raise. The loop closes. |
| 4:30–5:00 | Cost per 1,000, call reduction, C-vs-D ablation delta, what breaks in production. |

The deliberate failure at 3:15 is the highest-signal thirty seconds in the video. Every track's bar asks for honest failure handling, and almost nobody volunteers one on camera.

---

## 13. Definition of done

- [ ] Frozen eval set generated, hashed, hash in README
- [ ] `make eval` reproduces the README table from a clean clone with no API key
- [ ] Tiered results table with T0 explicitly de-emphasized
- [ ] Track-required match rate reported, with its denominator stated
- [ ] Auto-resolution precision and ₹ value at risk both headlined
- [ ] Ablation, four arms, good-faith regex baseline
- [ ] Agent tools verifiably read-only (asserted in tests)
- [ ] Validator provably reads raw tool outputs, not agent prose
- [ ] Validator provably receives the complete candidate set, not the agent's selection
- [ ] Record / case / batch counts both reported
- [ ] Zero unexplained paise in any auto-resolution
- [ ] Human resolution persists across re-runs
- [ ] Audit record for every decision, including full agent trajectory
- [ ] "What breaks in production" written
- [ ] Architecture diagram in README
- [ ] 5-minute video including one honest failure
- [ ] Public repo