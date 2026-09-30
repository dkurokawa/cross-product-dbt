# cross-product-dbt

**All data in this repository is synthetic and unrelated to any real person or organization.**

A template for putting a shared data platform on top of products that were built
independently: five sources with five different shapes (an event lake, daily snapshots,
a change-data-capture log, Shift_JIS CSV exports, a hand-maintained spreadsheet) are landed
with data contracts, matched to the same people and households, and modelled with dbt on
DuckDB. The models are written so that they also compile for BigQuery.

The business domain is a fictional fitness chain. Work in progress: see the commit history.

```bash
uv sync --all-groups
make demo
```
