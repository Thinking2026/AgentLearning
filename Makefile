PYTHON ?= python3
PIP ?= $(PYTHON) -m pip

.PHONY: install install-chromadb install-dev run check compile

install:
	$(PIP) install -r requirements.txt

install-chromadb:
	$(PIP) install -r requirements-chromadb.txt

install-dev:
	$(PIP) install -r requirements-dev.txt

run:
	$(PYTHON) main.py

check:
	$(PYTHON) -m compileall .

compile: check
