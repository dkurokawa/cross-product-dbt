**All data in this repository is synthetic and unrelated to any real person or organization.**

# cross-product-dbt

A template for putting a shared data platform on top of products that were built
independently. Five sources with five different shapes are landed with data contracts,
matched to the same people and households, and modelled with dbt on DuckDB. The models are
written so that they also compile for BigQuery.

The business domain is a fictional fitness chain. This is work in progress: only the
ingest and bronze/silver layers exist so far (metrics, governance, catalog and gateway come
later).

## Quick start

```bash
uv sync --all-groups
make demo          # ingest -> dbt build (models + tests) -> source freshness, on DuckDB
make test          # pytest with coverage (>= 90%)
make lint          # ruff, mypy --strict, sqlfluff, dialect denylist
make bq-compile    # dbt compile --target bigquery, offline, with a throwaway fake key
```

`make demo` needs no credentials and no network beyond `dbt deps` (dbt_utils). It uses a
clearly fake demo salt for the member keys; set `PLATFORM_HMAC_SALT` yourself for anything else.

## The five sources

| | Product (fictional) | Shape | Deliberate inconsistencies |
|---|---|---|---|
| A | consumer app | [eventlake](https://github.com/dkurokawa/eventlake) events | UTC, UUID ids, tax-inclusive purchases, family accounts, re-delivered events |
| B | studio reservation SaaS | full-table snapshots (Parquet) | `tenant_id` + per-tenant member number, naive JST times, tax-exclusive prices, logical delete |
| C | gym back office | CDC log (`op` I/U/D) | two contract table generations (FY2025 / FY2026), repeated updates, deletes |
| D | acquired billing software | Shift_JIS CSV, daily | Japanese headers, `YYYY/MM/DD`, its own plan codes, refunds in a separate file, identified by kana name + phone |
| E | corporate-sales sheet | monthly CSV | header names drift between months, `—` and full-width digits in numeric cells |

The same people and households appear in several products (`--overlap`), each time written
the way that product writes them.

## Layout

```
src/cross_product_platform/
  generators/   synthetic data for A-E (Faker ja_JP, fixed seed)
  contracts/    pandera contract per dataset + contracts/aliases.yml (E's column names)
  identity.py   the ONE place that decides who is the same person (HMAC keys)
  household.py  the ONE place that decides who shares a household
  landing.py    contract check -> add keys -> land, or quarantine with a JSON reason
dbt/
  models/bronze   shape only: types, UTC, encoding; no rows dropped
  models/silver   dim_member, dim_household, dim_account, dim_plan (seed), facts, SCD2 contracts
  macros/portable.sql   every dialect-specific bit, behind adapter.dispatch
```

## Ingest

`platform ingest --root <dir>` generates the sources and lands them:

* every file is checked against its contract; a file that fails moves to
  `quarantine/<source>/<date>/` next to a JSON reason (column names and counts, never values);
* E's column names are canonicalized with `contracts/aliases.yml`; an unknown name quarantines
  the file;
* `member_key = HMAC-SHA256(salt, normalized e-mail)` (phone, then phone + kana as fallbacks)
  is added by Python at landing. The salt is read from `PLATFORM_HMAC_SALT` and is never
  logged, so it cannot end up in compiled SQL under `dbt/target/`.
* `--gap-day YYYY-MM-DD` drops a whole day of product A (input for the anomaly test),
  `--unmapped-plan` adds a plan code that is missing from the mapping seed.

## Checks that are themselves tested

* `make anomaly-check`: the `row_count_anomaly(window=28, z=3)` test is quiet on the clean lake
  and flags exactly the injected gap day.
* `make unmapped-check`: a plan code missing from `dim_plan` fails the build.
* `make bq-compile`: all models compile for BigQuery without any connection.
