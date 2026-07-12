SHELL := /usr/bin/env bash

PYTHON ?= python3
PROD_BASE_URL ?=

.DEFAULT_GOAL := help

.PHONY: help test-fast test-precommit test-security test-ui test-full test-prod-smoke test-all

help: ## Show available test targets.
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make <target>\n\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

test-fast: ## Cheap local checks: Python regression subset + frontend type check, no browser UI.
	SKIP_UI=1 ./scripts/precommit.sh

test-precommit: ## Local pre-commit suite: Python subset + frontend type check + staging UI smoke + thread switch.
	./scripts/precommit.sh

test-security: ## Security/static checks: security regressions, npm high audit, Terraform fmt, whitespace.
	$(PYTHON) -m pytest tests/test_security_regressions.py -q
	cd frontend && npm audit --audit-level=high
	terraform fmt -check terraform
	git diff --check

test-ui: ## Staging UI smoke: app shell, stats, thread switching. Requires local staging stack.
	./scripts/precommit.sh

test-full: ## Full staging workflow with LLM calls: fetch -> histories -> enrich -> score -> draft/revise -> approve -> delete.
	./scripts/ui_flow_test.sh

test-prod-smoke: ## Production-safe smoke: public routing and unauth sensitive routes only.
	@test -n "$(PROD_BASE_URL)" || { echo "Set PROD_BASE_URL to the deployment URL." >&2; exit 2; }
	@code=$$(curl -sS -o /tmp/outreach-prod-root.out -w '%{http_code}' "$(PROD_BASE_URL)/"); \
		[[ "$$code" == "200" ]] || { echo "$(PROD_BASE_URL)/ returned $$code, expected 200"; exit 1; }
	@code=$$(curl -sS -o /tmp/outreach-prod-health.out -w '%{http_code}' "$(PROD_BASE_URL)/health"); \
		[[ "$$code" == "404" ]] || { echo "$(PROD_BASE_URL)/health returned $$code, expected 404"; exit 1; }
	@code=$$(curl -sS -o /tmp/outreach-prod-auth.out -w '%{http_code}' "$(PROD_BASE_URL)/auth/me"); \
		[[ "$$code" == "401" ]] || { echo "$(PROD_BASE_URL)/auth/me returned $$code, expected 401"; exit 1; }
	BASE_URL="$(PROD_BASE_URL)" ./scripts/prod_auth_smoke.sh

test-all: test-fast test-security ## Default CI-safe suite. Does not run browser, LLM, or production checks.
