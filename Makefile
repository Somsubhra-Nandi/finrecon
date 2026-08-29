.PHONY: install test generate-dev generate-frozen verify-frozen test-idempotency test-isolation reconcile-dev test-stage3 investigate-dev investigate-dev-replay test-stage4 eval eval-live eval-compare generate-v4-pilot verify-v4-pilot reconcile-v4-pilot baselines-v4-pilot conjunction-rules test-v4 test-validator-v2 test-validator-v3 generate-bounded-search verify-bounded-search oracle-bounded-search baseline-bounded-search test-bounded-search

install:
	pip install -e ".[dev]"

test:
	pytest

generate-dev:
	python -m finrecon.benchmark.generator.generate --split dev

generate-frozen:
	python -m finrecon.benchmark.generator.generate --split frozen-eval

verify-frozen:
	python -m finrecon.benchmark.generator.generate --verify-frozen

# Stage 2 --------------------------------------------------------------
# No `eval` / `eval-live` target: the evaluation harness is Stage 4 and
# does not exist. Nothing here reports accuracy.

test-idempotency:
	pytest tests/test_idempotency.py -v

test-isolation:
	pytest tests/test_benchmark_isolation.py -v

reconcile-dev:
	python -m finrecon.reconcile_cli --split dev

# Stage 3 --------------------------------------------------------------
# Nothing in this section reports accuracy. The Stage-3 controller has no
# ground truth by construction; accuracy lives in the Stage-4 targets below.

test-stage3:
	pytest -q -k "evidence_reference or agent_tools or agent_providers or agent_loop or trajectory_cache or validator or policy or stage3"

# Live: needs a provider credential in the environment. Deliberately capped
# at four cases -- burning a free-tier quota on 200 DEV cases before reading
# one trajectory is how a day disappears.
investigate-dev:
	python -m finrecon.investigate_cli --split dev --limit 4

# Replay: zero provider calls, no API key. The shape `make eval` will take.
investigate-dev-replay:
	python -m finrecon.investigate_cli --split dev --replay-only

# Stage 4 --------------------------------------------------------------
# Offline benchmark evaluation. This is the ONLY layer that reads hidden
# ground truth, it lives outside src/ (benchmark/eval/), and it makes zero
# provider calls: a missing trajectory fails the run rather than triggering
# a live one. Evaluation can never influence a reconciliation decision.

test-stage4:
	pytest -q tests/test_stage4_evaluator.py

# Authoritative submission evaluation: verifies both dataset fingerprints,
# records deterministic non-LLM trajectories from visible inputs, then replays
# them through the real Stage-3 validator/policy. No network or API key.
eval:
	python -m benchmark.final_eval

# Explicit live evaluation remains opt-in and is never called by `make eval`.
# It requires a populated trajectory cache or a separately-run provider command.
eval-live:
	@echo "Live evaluation is intentionally not automated. Run investigate_cli with explicit provider credentials, then score its recorded trajectory with python -m benchmark.eval evaluate."

eval-compare:
	@test -n "$(A)" -a -n "$(B)" || (echo "usage: make eval-compare A=report-a.json B=report-b.json"; exit 2)
	python -m benchmark.eval compare $(A) $(B)

# Benchmark v4 PILOT ---------------------------------------------------
# Additive. Nothing in this section reads, writes or regenerates benchmark
# v3: it owns `benchmark/datasets/v4-pilot/`, `ground_truth/v4-pilot.jsonl`
# and `manifests/v4-pilot.json`, and `make verify-frozen` above must keep
# passing before and after any of it.
#
# The pilot is NOT frozen. No match rate, precision or coverage number from
# this split may be reported as a benchmark result until a freeze decision
# is taken and recorded in benchmark/manifests/CHANGELOG.md.

generate-v4-pilot:
	python -m finrecon.benchmark.generator_v4.generate --write

verify-v4-pilot:
	python -m finrecon.benchmark.generator_v4.generate --verify

reconcile-v4-pilot:
	python -m finrecon.reconcile_cli --split v4-pilot

# Deterministic diagnostic baselines: how much of the pilot needs no model.
# Zero provider calls; ground truth is read only to score decisions that
# were already made. Exits non-zero if a *conservative* arm resolves a case
# incorrectly, which would mean the pilot misleads rather than tests.
baselines-v4-pilot:
	python -m benchmark.baselines --split v4-pilot --json-out benchmark/baselines/reports/v4-pilot.json

test-v4:
	pytest -q tests/test_benchmark_v4_pilot.py tests/test_v4_leakage.py tests/test_v4_baselines.py tests/test_v4_stage4_integration.py

# Validator v2 -------------------------------------------------------------
# The experimental harness that chose the conjunction rule: five candidate
# rules against the adversarial fixtures, the v4 pilot and the DEV residual.
# Zero provider calls. Exits non-zero if the rule the tree currently ships
# would not pass its own shippability criteria.
conjunction-rules:
	python -m benchmark.baselines.conjunction_report --json-out benchmark/baselines/reports/conjunction-rules.json

# The safety properties of the rule that shipped, through the production path.
test-validator-v2:
	pytest -q tests/test_evidence_closure.py tests/test_validator_conjunction.py

test-validator-v3:
	pytest -q tests/test_evidence_closure.py tests/test_validator_conjunction.py tests/test_validator_structural.py

# Synthetic bounded-search challenge ---------------------------------------
# Additive, not frozen, and never invokes a hosted provider from these targets.

generate-bounded-search:
	python -m finrecon.benchmark.generator_search.generate --write

verify-bounded-search:
	python -m finrecon.benchmark.generator_search.generate --verify

oracle-bounded-search:
	python -m benchmark.search_challenge oracle --json-out benchmark/reports/bounded-search-v1-oracle.json

baseline-bounded-search:
	python -m benchmark.search_challenge mechanical --json-out benchmark/reports/bounded-search-v1-mechanical.json

test-bounded-search:
	pytest -q tests/test_bounded_search_benchmark.py
