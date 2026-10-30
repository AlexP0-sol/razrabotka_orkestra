.PHONY: install dev lint format typecheck test coverage check demo doctor clean

PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
UV ?= $(shell if [ -x .venv/bin/uv ]; then echo .venv/bin/uv; elif command -v uv >/dev/null 2>&1; then echo uv; else echo ""; fi)

install:
	@if [ ! -d .venv ]; then python3 -m venv .venv; fi
	@if [ -n "$(UV)" ]; then \
		$(UV) pip install --python .venv/bin/python -r requirements-dev.txt -e .; \
	else \
		.venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements-dev.txt -e .; \
	fi

dev:
	$(PYTHON) -m orchestra serve

doctor:
	$(PYTHON) -m orchestra doctor

demo:
	$(PYTHON) -m orchestra demo

lint:
	$(PYTHON) -m ruff check orchestra tests scripts
	$(PYTHON) -m ruff format --check orchestra tests scripts

format:
	$(PYTHON) -m ruff format orchestra tests scripts

typecheck:
	$(PYTHON) -m mypy --strict orchestra

test:
	$(PYTHON) -m pytest -q

coverage:
	$(PYTHON) -m pytest --cov=orchestra --cov-fail-under=85 -q

check: lint typecheck test

clean:
	rm -rf .mypy_cache .pytest_cache .ruff_cache .hypothesis .coverage htmlcov build dist *.egg-info
