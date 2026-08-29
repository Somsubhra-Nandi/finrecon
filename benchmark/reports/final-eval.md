# FinRecon Evaluation

## Reproducibility

Command: `uv run python -m benchmark.final_eval` (or `make eval` where Make is available)
Frozen benchmark hash: `f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b`
Mode: deterministic trajectory recording followed by offline replay; zero network and zero live provider calls.
Replay validates orchestration, validator, and policy against recorded investigator outputs. It is not a fresh measurement of hosted-model quality.

## Frozen benchmark

| Tier | Cases | Auto resolved | Correct auto | Wrong auto | Escalated |
|---|---:|---:|---:|---:|---:|
| T0 | 350 | 350 | 350 | 0 | 0 |
| T1 | 300 | 300 | 300 | 0 | 0 |
| T2 | 200 | 173 | 173 | 0 | 27 |
| T3 | 40 | 0 | 0 | 0 | 40 |

## Resolution outcomes

- Deterministic (Stage 2): 650
- AI-assisted (Stage 3 evidence search + validator/policy acceptance): 173
- Escalated: 67

## Safety

- Unsafe auto-resolutions: 0
- Unsafe auto-match rate: 0.0
- Value at risk: 0 paise (sum of `value_at_stake_paise` for incorrect automatic resolutions).

## Stage-3 replay evaluation

Exact frozen residual cohort: 240 cases; correct auto 173, wrong auto 0, escalated 67.
Provider/model metadata: deterministic non-LLM `mechanical:mechanical-investigator-v1` (requested and reported); no token/cost telemetry is fabricated.

## Experimental adversarial pilot — NON-PRODUCTION

v4 pilot: 64 cases; correct auto 48, wrong auto 0, escalated 16. This is synthetic safety/capability coverage, not Razorpay production coverage.

## Limitations

All benchmark data is synthetic. Replay is reproducible but is not current hosted-model quality. Razorpay/bank adapter fixtures are conformance tests, not production-accuracy benchmarks.
