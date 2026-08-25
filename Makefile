.PHONY: install test generate-dev generate-frozen verify-frozen test-idempotency test-isolation reconcile-dev test-stage3 investigate-dev investigate-dev-replay test-stage4 eval eval-compare

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

# `eval` needs a recorded trajectory corpus. It deliberately does NOT fall
# back to a live run when one is absent -- see DESIGN.md 6.2. Point
# TRAJECTORIES at a warmed cache directory, or use --run-dump for a
# transcript.
TRAJECTORIES ?= fixtures/trajectories
EVAL_PROVIDER ?= gorouter
EVAL_MODEL ?= claude-opus-5-thinking

eval:
	python -m benchmark.eval evaluate --split dev --trajectories $(TRAJECTORIES) --provider $(EVAL_PROVIDER) --model $(EVAL_MODEL)

eval-compare:
	@test -n "$(A)" -a -n "$(B)" || (echo "usage: make eval-compare A=report-a.json B=report-b.json"; exit 2)
	python -m benchmark.eval compare $(A) $(B)
