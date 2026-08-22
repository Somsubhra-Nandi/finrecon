.PHONY: install test

install:
	pip install -e ".[dev]"

test:
	pytest
