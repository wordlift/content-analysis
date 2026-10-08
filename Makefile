.PHONY: setup test example lint clean

PY ?= python3
VENV ?= .venv

setup:            ## create a venv and install with dev extras
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install -q -e ".[dev]"
	@echo "ok: source $(VENV)/bin/activate"

test:             ## run the test suite (no network, no key)
	$(VENV)/bin/python -m pytest -q

example:          ## resolve a sentence against the live engine (needs WL_KEY)
	$(VENV)/bin/python examples/resolve_with_wordlift.py

lint:             ## byte-compile, then style (ruff) and static types (mypy)
	$(VENV)/bin/python -m compileall -q resolve_pipeline tests examples
	$(VENV)/bin/python -m ruff check .
	$(VENV)/bin/python -m mypy

clean:
	rm -rf $(VENV) .pytest_cache build dist *.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
