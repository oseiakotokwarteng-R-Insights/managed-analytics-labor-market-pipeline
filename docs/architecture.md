# How the pipeline is put together

```mermaid
flowchart LR
    A[BLS annual QCEW files<br/>one zip per year] --> B[collect.py<br/>download, hash, log]
    R[BLS code files<br/>areas, industries, ownership] --> B
    B --> C[transform.py<br/>filter to DC/MD/VA, rename,<br/>fix types, suppressed to NULL]
    C --> D{validate.py<br/>18 checks}
    D -- any error --> F[Stop. Record the failure.<br/>Nothing is loaded.]
    D -- pass or warnings only --> E[load.py<br/>upsert into SQLite]
    E --> G[(qcew_dmv.sqlite)]
    G --> H[export.py<br/>CSV files]
    G --> I[sql/example_queries.sql]
```

## What each file does

| File | Job |
|---|---|
| `qcew_pipeline/config.toml` | The settings: which years, states, ownership, tolerances, file locations. Ships inside the package. |
| `qcew_pipeline/schema.sql` | The database layout (also ships inside the package). |
| `pyproject.toml` | Packaging (installs `qcew-run` and `qcew-check-updates`), plus the settings for pytest, ruff and mypy. |
| `Dockerfile` | Runs the pipeline in a container. Built and smoke-tested in CI. |
| `qcew_pipeline/years.py` | Works out which years to pull. With `years = "auto"` it asks BLS (header requests only) for the newest published year and uses the most recent `window_years` years ending there. |
| `qcew_pipeline/collect.py` | Downloads the annual zips (with retries) and BLS lookup tables, and records each file's size and SHA-256 fingerprint. |
| `qcew_pipeline/transform.py` | Reads each national file in chunks, keeps only the rows I want, renames columns, fixes types, and turns suppressed values into NULL. |
| `qcew_pipeline/validate.py` | Runs the checks. Errors block the load; warnings get recorded. |
| `qcew_pipeline/load.py` | Creates the schema, loads the lookup tables, copies the new rows into a temporary staging table, then lets SQLite work out what is new, changed or removed and do the upsert, all in one transaction. Also computes a fingerprint of the data. |
| `qcew_pipeline/publish.py` | Writes the database out as Parquet files (one folder per year, plus the code lists and a manifest). Builds the new folder next to the old one and then swaps them, so nobody reads a half-written set. Needs DuckDB. |
| `qcew_pipeline/analytics.py` | Runs the dbt project in the `analytics` folder on the Parquet files. It sets the paths, passes in the limits from `config.toml`, and turns dbt's result into an exit code. Needs the analytics extras. |
| `analytics/` | The dbt project. Staging views clean the Parquet files, three summary tables are built from them, and tests check keys, codes, the hidden-values rule and the county, state and sector totals. |
| `qcew_pipeline/run.py` | Runs all steps in order with logging and timing, and decides what to do on failure. |
| `qcew_pipeline/export.py` | Writes CSVs from the database. |
| `qcew_pipeline/check_updates.py` | Asks BLS (header request only) whether a newer annual file exists. Exits with 2 if BLS can't be reached, so a broken check is never mistaken for 'no news'. |
| `.github/workflows/pipeline.yml` | Runs style, type and unit checks and a Docker build on every push, checks for new data monthly and opens a GitHub issue (once per year of data) when there is some, and can run the pipeline on demand. |

## Decisions worth knowing about

* **Fail the whole run if one year fails.** If a download or a check fails for one year, nothing is loaded. A multi-year table with a silent hole in it would be worse than no new data.
* **Validate before loading.** The database only ever contains data that passed the error-level checks.
* **Two stores, two jobs.** SQLite is where the pipeline keeps its books: the run history, the check results, the rows BLS removed. The Parquet files are what the analytics layer reads. Every Parquet row carries the fingerprint of the file it came from, and the manifest records which state of the database it was written from, so the two can always be matched up.
* **Check the published files again, in SQL.** The pipeline already checks the data before it loads it. The dbt tests check it again after it has been published, with different code, so a mistake in the publish step or in one of the two ways of checking can't hide. If the two ever disagree, I want to know why.
* **Hidden values never become numbers in the summary tables.** A change from one year to the next, or a sector's share of a county, is left empty when either number is hidden. Nothing is estimated or filled in.
* **Warn about drift, don't stop for it.** Two warnings compare each year with the one before: a big jump in the share of hidden values, and a big change in how many rows BLS published. Either could be a real change at BLS or a problem with the file, and I can't tell which from here, so they are recorded and I look into them. The limits are in `config.toml`. They only compare years inside the same run.
* **A rolling window of years.** By default the pipeline pulls the five most recent years and always includes the newest one BLS has published, so a new release is picked up without editing anything. Years that fall out of the window stay in the database and are simply no longer refreshed. Pin a fixed list in `config.toml` when you need a frozen, reproducible set.
* **Stage, then compare inside the database.** The new rows are copied into a temporary table and SQLite does the comparing and the upsert as set operations. The stored table is never read into pandas and there is no row-by-row Python loop. On 600,000 made-up rows (about four times the real DMV data) a rerun took 7.6 s instead of 22.5 s, and a typical revision 9.5 s instead of 21.3 s (`python evaluation/benchmark_load.py --rows 600000`). The new rows themselves are still held in memory, so there is a ceiling; the next step past it would be loading year by year.
* **Upsert, not append.** Re-running can't create duplicates, and changed values are counted so BLS revisions are visible.
* **Removed rows are archived, not ignored.** If BLS stops publishing a row, it moves to `qcew_annual_removed` and is counted on the run. Only rows for the years, states and sectors in the run are considered, so narrowing `config.toml` never looks like BLS deleting data.
* **A load that would remove too much stops.** If more than `max_removed_fraction` (5%) of the stored rows in scope would go, the run fails and changes nothing. A normal revision touches a handful of rows; a big loss means the new file is wrong.
* **One main source.** Only QCEW. The state-total rows in the same file are used as an independent cross-check on the county rows.
* **Private sector only.** Government employment is covered and paid differently, so mixing it in would make county comparisons harder to interpret.
