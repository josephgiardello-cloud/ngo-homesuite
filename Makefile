.PHONY: install test test-db run lint

VENV_PY := .venv/Scripts/python.exe

install:
	$(VENV_PY) -m pip install -r requirements-dev.txt

test:
	$(VENV_PY) -m pytest --maxfail=10 -v

test-db:
	$(VENV_PY) -m pytest ngo_homesuite/db --maxfail=10 -v

run:
	$(VENV_PY) ngo_homesuite/main.py --web

lint:
	$(VENV_PY) -m ruff check ngo_homesuite
