SHELL := /bin/bash
.DEFAULT_GOAL := help
UV := uv

.PHONY: help bootstrap test lint format run image up contract

help:        ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

bootstrap:   ## Resolve and install the environment
	$(UV) sync

test:        ## pytest — dialect, pagination, changelogs, ledger coherence, failure modes
	$(UV) run pytest tests/ -W ignore::DeprecationWarning

lint:        ## ruff + strict mypy
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy src/pennylane_mock

format:      ## Format the code
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

run:         ## Run the mock locally (admin plane open)
	PENNYLANE_MOCK_ADMIN_ENABLED=true $(UV) run python -m pennylane_mock

image:       ## Build the container image
	docker build -t pennylane-mock:dev .

up:          ## Run the mock in a container (docker compose up --build)
	docker compose up --build

contract:    ## Regenerate contracts/pennylane.openapi.yaml from the app
	@# `openapi_contract()` rather than `app.openapi()`: the contract describes the
	@# PENNYLANE dialect. /__admin and /health are mock affordances — publishing
	@# them would pass off as vendor API what is not, and /__admin is only mounted
	@# conditionally, which would make the contract depend on the generation
	@# environment.
	$(UV) run python -c "import yaml, pennylane_mock as m; \
open('contracts/pennylane.openapi.yaml','w').write(yaml.safe_dump(m.openapi_contract(), sort_keys=False, allow_unicode=True))"
	@echo "✓ contract regenerated — REVIEW the diff: a changed response shape is a contract change for consumers"
