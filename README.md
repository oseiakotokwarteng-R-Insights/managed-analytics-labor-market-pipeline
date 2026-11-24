# Managed Analytics: Labor Market Data Pipeline

A pipeline that takes public employment and wage data from the Bureau of Labor Statistics (BLS), cleans it, checks it, and saves it in a database anyone can query. It is a sample project from [Managed Analytics](https://managedanalytics.co), built on public data so the work can be inspected.

The numbers come from the Quarterly Census of Employment and Wages (QCEW), which counts jobs and wages at businesses, county by county.

## Where this came from

This project starts from a pipeline I built for a graduate capstone: [dmv_labor_market_pipeline](https://github.com/oseiakotokwarteng-R-Insights/dmv_labor_market_pipeline). That repository stays as it is. This one takes the same code and keeps building on it, so it can do what a client project would need.

## What it does today

The starting code does four things in order:

1. **Collect** the annual BLS files, with retries, and record where each file came from and its SHA-256 fingerprint.
2. **Transform** the huge national files into a small table for the chosen states. Values BLS hides for privacy are stored as blank, never as zero.
3. **Validate** with 16 checks. A real error stops the run and nothing is saved.
4. **Load** into SQLite. Running it twice does not create duplicates, and changed numbers are counted.

It is tested (43 automated tests plus a script that breaks the data on purpose to see whether the checks notice) and runs the same way on Windows and Linux.

## What I am adding

| Phase | What | Status |
|---|---|---|
| 1 | Harden it: catch rows BLS removes (done), alert when new data is published (done), handle bigger data (done), more tests, Docker, packaging | In progress |
| 2 | An analytics layer: Parquet files, then DuckDB and dbt models with tests | Planned |
| 3 | Orchestration with Dagster, and email alerts | Planned |
| 4 | More sources, including a check of one source against another, and a second region (California) set only by configuration | Planned |
| 5 | A small dashboard for non-technical users | Planned |
| 6 | An employment forecast, compared with simple baselines and tested on data it has not seen | Planned |

## Try it

You need Python 3.11 or newer.

```bash
pip install -r requirements.txt
```

```bash
python -m qcew_pipeline.run --export
```

The second command downloads five yearly files from BLS (about 390 MB in total) and builds the database. Set `QCEW_RAW_DIR` to keep the downloads outside the project folder. To run the tests:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Settings such as the years and states are in [`config.toml`](config.toml). There is a diagram and a list of design choices in [`docs/architecture.md`](docs/architecture.md), and every table is described in [`docs/data_dictionary.md`](docs/data_dictionary.md).

## Data

Bureau of Labor Statistics, Quarterly Census of Employment and Wages, from <https://www.bls.gov/cew/>. It is public government data and contains no personal information. The numbers in the database are the ones BLS published. This project only filters them, adds names to the codes, and checks them.

## License

MIT. See [`LICENSE`](LICENSE).
