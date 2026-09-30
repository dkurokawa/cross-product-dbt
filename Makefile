# `make demo` runs the whole pipeline on synthetic data: ingest -> dbt build -> tests.

SHELL := /bin/bash
LAKE ?= build/lake
WAREHOUSE ?= build/warehouse/platform.duckdb

# Demo-only salt for the member_key HMAC. It is deliberately obvious and is NOT a secret:
# the data it protects is synthetic. Real deployments must supply their own via the
# environment; the ingest step refuses to run without one.
export PLATFORM_HMAC_SALT ?= demo-only-salt-not-a-secret
export PLATFORM_LANDING_ROOT := $(abspath $(LAKE))
export PLATFORM_WAREHOUSE := $(abspath $(WAREHOUSE))
export PLATFORM_AUDIT_ROOT := $(abspath build/audit)

DBT := uv run dbt
DBT_ARGS := --project-dir dbt --profiles-dir dbt

.PHONY: demo ingest deps dbt-build roles audit catalog mcp-smoke freshness docs checks generate test lint lint-py lint-sql lint-dialect \
	bq-compile anomaly-check unmapped-check

demo: ingest deps dbt-build roles audit freshness docs checks catalog mcp-smoke

ingest:
	uv run platform ingest --root $(LAKE) --overwrite

deps:
	$(DBT) deps $(DBT_ARGS)

# The audit models read the gateway's audit events, which exist only after the gateway ran
# (target `audit`), so the first pass leaves them out.
dbt-build:
	@mkdir -p $(dir $(WAREHOUSE))
	$(DBT) build $(DBT_ARGS) --exclude tag:audit

# F6 layer 4: one database file per role, holding only that role's access tables.
roles:
	uv run platform build-roles --warehouse $(WAREHOUSE) --out-dir $(dir $(WAREHOUSE))

# Sample gateway calls -> audit lake -> dbt audit models -> assertions on gold.audit_access_log.
audit:
	uv run python scripts/audit_demo.py write
	$(DBT) build $(DBT_ARGS) --select tag:audit
	uv run python scripts/audit_demo.py check

catalog:
	uv run platform catalog build

# The real MCP server on the demo warehouse: every tool once, and the analyst is refused health_notes.
mcp-smoke:
	uv run python scripts/mcp_smoke.py

freshness:
	$(DBT) source freshness $(DBT_ARGS) --exclude source:audit

# The catalog (actual column lists) is what the metric-name guard scans.
docs:
	$(DBT) docs generate $(DBT_ARGS)

# Guards: generated files (metrics, access layer, gateway allowlist) up to date; no hand-written
# metric-named columns; every reported KPI is a known variant; every column declared for PII and
# nothing personal detected in the landed data that is declared none / undeclared.
checks:
	uv run platform metrics check --root $(LAKE)
	uv run platform access check --manifest dbt/target/manifest.json
	uv run platform scan --root $(LAKE)

generate:
	uv run platform metrics generate
	uv run platform access generate

test:
	uv run pytest --cov=cross_product_platform --cov-fail-under=90

lint: lint-py lint-sql lint-dialect

lint-py:
	uv run ruff check --no-cache .
	uv run ruff format --no-cache --check .
	uv run mypy --strict src tests scripts

lint-sql:
	uv run sqlfluff lint dbt/models

lint-dialect:
	uv run platform lint-dialect dbt/models

# Offline BigQuery compile (design section 9, spike 2): a throwaway fake service-account
# key, no connection, no introspection.
bq-compile: deps
	@tmp="$$(mktemp -d)"; \
	scripts/fake_bq_keyfile.sh "$$tmp/key.json" && \
	BQ_OFFLINE_KEYFILE="$$tmp/key.json" $(DBT) compile $(DBT_ARGS) --target bigquery \
		--no-introspect --no-populate-cache; \
	status=$$?; rm -f "$$tmp/key.json"; rmdir "$$tmp"; exit $$status

# Generates a second lake with a one-day gap in product A and checks that the
# row_count_anomaly test warns about exactly that day (and not on the clean lake).
anomaly-check: deps
	uv run python scripts/check_anomaly_detected.py

# An unmapped plan code must fail a test, not pass quietly.
unmapped-check: deps
	uv run python scripts/check_unmapped_plan_fails.py
