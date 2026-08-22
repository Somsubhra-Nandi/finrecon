.PHONY: install test generate-dev generate-frozen verify-frozen test-idempotency test-isolation reconcile-dev

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
