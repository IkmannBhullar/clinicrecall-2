# ClinicRecall — root Makefile.
#
# This file is the single entry point for the whole stack. SPEC constraint D5 requires that a
# cold machine reaches a running demo in exactly two commands:
#
#     make setup
#     make dev
#
# Everything else here exists to support those two, plus `make verify` (SPEC section 11), which
# is the executable definition of done.
#
# Note: written for GNU Make 3.81 (the version shipped with macOS), so no .ONESHELL and no
# fancy conditionals — plain, portable recipes only.

# Use bash rather than sh so `set -o pipefail` and [[ ]] behave predictably.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

REPO_ROOT := $(shell pwd)
API_DIR   := $(REPO_ROOT)/apps/api
WEB_DIR   := $(REPO_ROOT)/apps/web

# Prefer a system-wide `supabase` if the developer has one; otherwise use the pinned binary that
# scripts/install-supabase-cli.sh drops into .tools/bin/. This keeps `make setup` working on
# machines without Homebrew.
SUPABASE := $(shell command -v supabase 2>/dev/null || echo $(REPO_ROOT)/.tools/bin/supabase)

# All Python commands run through uv, which owns the virtualenv and the Python 3.12 pin.
UV := uv --directory $(API_DIR)

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------------------------

.PHONY: help
help: ## Show the available commands
	@echo ""
	@echo "  ClinicRecall"
	@echo "  ------------"
	@echo "  Cold start:   make setup   then   make dev"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ---------------------------------------------------------------------------------------------
# Setup — command 1 of 2
# ---------------------------------------------------------------------------------------------

.PHONY: setup
setup: ## Install every dependency and prepare a fresh database (run once)
	@echo "==> [1/7] Installing the Supabase CLI (skipped if already present)"
	@bash scripts/install-supabase-cli.sh
	@echo "==> [2/7] Creating .env files from .env.example (existing files are left alone)"
	@bash scripts/bootstrap-env.sh
	@echo "==> [3/7] Installing Python dependencies (uv creates apps/api/.venv on Python 3.12)"
	@$(UV) sync --all-extras
	@echo "==> [4/7] Installing Node dependencies"
	@pnpm install --frozen-lockfile || pnpm install
	@echo "==> [5/7] Starting the local Supabase stack (Postgres + Auth + Studio in Docker)"
	@$(MAKE) supabase-start
	@echo "==> [6/7] Applying database migrations"
	@$(MAKE) migrate
	@echo "==> [7/7] Regenerating the sample CSVs with dates relative to today"
	@$(MAKE) samples
	@echo ""
	@echo "Setup complete. Next:  make seed  (loads demo data), then  make dev"
	@echo ""

# ---------------------------------------------------------------------------------------------
# Run — command 2 of 2
# ---------------------------------------------------------------------------------------------

.PHONY: dev
dev: ## Start Supabase, the API, and the web app together
	@bash scripts/dev.sh

.PHONY: api
api: ## Start only the FastAPI backend (http://localhost:8000)
	@$(UV) run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

.PHONY: web
web: ## Start only the Next.js frontend (http://localhost:3000)
	@pnpm -C apps/web dev

# ---------------------------------------------------------------------------------------------
# Supabase local stack
# ---------------------------------------------------------------------------------------------

.PHONY: supabase-start
supabase-start: ## Start the local Supabase stack (requires Docker to be running)
	@bash scripts/supabase-up.sh

.PHONY: supabase-stop
supabase-stop: ## Stop the local Supabase stack
	@$(SUPABASE) stop

.PHONY: supabase-status
supabase-status: ## Show local Supabase stack status and connection details
	@$(SUPABASE) status

# ---------------------------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------------------------

.PHONY: migrate
migrate: ## Apply all Alembic migrations to the local database
	@$(UV) run alembic upgrade head

.PHONY: migration
migration: ## Create a new Alembic migration.  Usage: make migration m="add widgets table"
	@$(UV) run alembic revision --autogenerate -m "$(m)"

.PHONY: samples
samples: ## Regenerate docs/samples/*.csv with dates relative to today (SPEC D1)
	@python3 scripts/generate-sample-csvs.py

.PHONY: seed
seed: ## Load the deterministic demo data (idempotent — safe to re-run)
	@$(UV) run python -m app.seed

.PHONY: demo-reset
demo-reset: ## Wipe all app data and reload pristine demo data (SPEC D3, under 30 seconds)
	@bash scripts/demo-reset.sh

# ---------------------------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------------------------

.PHONY: lint
lint: ## Run all linters and formatters in check mode
	@echo "==> ruff (lint)"
	@$(UV) run ruff check .
	@echo "==> ruff (format check)"
	@$(UV) run ruff format --check .
	@echo "==> eslint"
	@pnpm -C apps/web lint

.PHONY: fmt
fmt: ## Auto-fix formatting and lint issues where possible
	@$(UV) run ruff check --fix .
	@$(UV) run ruff format .
	@pnpm -C apps/web lint --fix || true

.PHONY: typecheck
typecheck: ## Run mypy (strict on services + schemas) and the TypeScript compiler
	@echo "==> mypy"
	@$(UV) run mypy app/services app/schemas
	@echo "==> tsc"
	@pnpm -C apps/web tsc --noEmit

.PHONY: test
test: ## Run the Python test suite
	@$(UV) run pytest -q

.PHONY: e2e
e2e: ## Run the Playwright end-to-end demo walkthrough
	@pnpm -C apps/web test:e2e

# ---------------------------------------------------------------------------------------------
# Definition of done (SPEC section 11) — this must exit 0
# ---------------------------------------------------------------------------------------------

.PHONY: verify
verify: ## Run every gate in SPEC section 11. Exits 0 only if the product is demo-ready.
	@bash scripts/verify.sh

# ---------------------------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove build artifacts and caches (does NOT touch the database)
	@rm -rf $(WEB_DIR)/.next $(WEB_DIR)/node_modules $(REPO_ROOT)/node_modules
	@rm -rf $(API_DIR)/.venv $(API_DIR)/.pytest_cache $(API_DIR)/.mypy_cache $(API_DIR)/.ruff_cache
	@find $(API_DIR) -name '__pycache__' -type d -prune -exec rm -rf {} +
	@echo "Cleaned. Run 'make setup' to rebuild."
