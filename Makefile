# Everyday commands. `make help` lists them.
SHELL := /bin/bash
.DEFAULT_GOAL := help

PY      ?= .venv/bin/python
DATA    ?= data
SYN_DIR ?= .synthea
# The dataset was generated with this exact Synthea build; regeneration refuses any other jar.
SYN_URL ?= https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar
SYN_SHA ?= 20098ec70c24c715ea874b19140983634711cc4986196105b5b0a9ca72a4b644
SYN_ARGS = -s 20261029 -cs 20261029 -r 20261028 -e 20261029 -p 120 -a 55-89 \
  --exporter.fhir.export=true --exporter.ccda.export=false --exporter.csv.export=false \
  --exporter.hospital.fhir.export=false --exporter.practitioner.fhir.export=false \
  --exporter.years_of_history=5 --generate.append_numbers_to_person_names=false
# Two inputs Synthea would otherwise take from the machine: the simulation runs until today unless -e pins an end
# date, and timestamps are written in the local zone. Both are pinned (with the locale) so every machine, a laptop
# in Denver or a CI runner in UTC, produces the same bytes. Mountain time suits a New Mexico health system.
DATA_TZ   ?= America/Denver
JAVA_PINS  = -Duser.timezone=$(DATA_TZ) -Duser.language=en -Duser.country=US -Dfile.encoding=UTF-8
# Synthea bundles library versions with known flaws (README, "Known vulnerabilities"), so it runs with the
# network switched off.
OFFLINE    = $(abspath scripts/offline.sh)

.PHONY: help setup data seed dev test lint evals e2e check approve-policy image up clean

help: ## List the commands
	@grep -hE '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-15s %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install the API and UI dependencies
	python3 -m venv .venv && $(PY) -m pip install -q -e '.[dev]'
	cd ui && npm ci --no-audit --no-fund

data: ## Regenerate the synthetic dataset from Synthea (needs Java 17+); output is deterministic
	mkdir -p $(SYN_DIR)
	test -f $(SYN_DIR)/synthea.jar || curl -fsSL -o $(SYN_DIR)/synthea.jar $(SYN_URL)
	echo "$(SYN_SHA)  $(SYN_DIR)/synthea.jar" | sha256sum -c - || \
	  { echo "This is not the Synthea build the dataset was made with; see SYN_SHA in the Makefile."; exit 1; }
	rm -rf $(SYN_DIR)/out && cd $(SYN_DIR) && TZ=$(DATA_TZ) $(OFFLINE) java $(JAVA_PINS) -jar synthea.jar $(SYN_ARGS) \
	  --exporter.baseDirectory=$(abspath $(SYN_DIR))/out "New Mexico" "Santa Fe" > synthea.log
	rm -rf $(DATA)/fhir $(DATA)/cases
	TZ=$(DATA_TZ) $(PY) scripts/build_dataset.py --synthea $(SYN_DIR)/out/fhir --out $(DATA)

seed: ## Reset the demo database (stop the server first)
	CADUCEUS_AUTH_MODE=dev $(PY) -m caduceus.seed

dev: ## Run the API on :8000 and the UI dev server on :5173 (development sign-in)
	@test -n "$$CADUCEUS_DEID_KEY" || export CADUCEUS_DEID_KEY=$$($(PY) -c "import secrets; print(secrets.token_urlsafe(48))"); \
	trap 'kill 0' EXIT; \
	CADUCEUS_AUTH_MODE=dev .venv/bin/uvicorn caduceus.api:create_app --factory --port 8000 --reload & \
	cd ui && npm run dev

lint: ## Ruff for Python, the TypeScript compiler for the UI
	$(PY) -m ruff check src tests scripts
	cd ui && npx tsc --noEmit

test: ## Unit and API tests
	$(PY) -m pytest

evals: ## Criteria agreement, PHI leakage and red-team suites; exits non-zero if a gate fails
	$(PY) -m caduceus.evals.run

e2e: ## Build the UI, then walk every screen in Chromium with axe-core accessibility checks
	cd ui && npm run build
	PATH=$(abspath .venv/bin):$$PATH scripts/e2e.sh

check: lint test evals e2e ## Everything CI runs, locally

approve-policy: ## Approve a reviewed criteria set: make approve-policy KEY=lumbar-mri@2026-09
	@test -n "$(KEY)" || { echo "usage: make approve-policy KEY=<id>@<version>"; exit 1; }
	$(PY) -c "from caduceus.criteria.loader import approve; from pathlib import Path; approve(Path('$(DATA)/policies'), '$(KEY)')"

image: ## Build the container image
	docker build -t caduceus-api:local .

up: ## Run the stack with docker compose (reads .env)
	docker compose up --build

clean: ## Remove local databases, build output and test results
	rm -rf var ui/dist ui/test-results evals/reports/latest.json
