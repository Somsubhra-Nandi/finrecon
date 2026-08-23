.PHONY: install test generate-dev generate-frozen verify-frozen test-idempotency test-isolation reconcile-dev test-stage3 investigate-dev investigate-dev-replay

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
# Still no `eval` / `eval-live` target: the evaluation harness is Stage 4
# and does not exist. Nothing below reports accuracy.

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
