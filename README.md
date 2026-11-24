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

It is tested (91 automated tests plus a script that breaks the data on purpose to see whether the checks notice) and runs the same way on Windows and Linux.

## What I am adding

| Phase | What | Status |
|---|---|---|
| 1 | Harden it: catch rows BLS removes (done), alert when new data is published (done), handle bigger data (done), fill the test gaps (done), follow the newest year automatically (done), packaging, Docker, style and type checks in CI | In progress |
| 2 | An analytics layer: Parquet files, then DuckDB and dbt models with tests | Planned |
| 3 | Orchestration with Dagster, and email alerts | Planned |
| 4 | More sources, including a check of one source against another, and a second region (California) set only by configuration | Planned |
| 5 | A small dashboard for non-technical users | Planned |
| 6 | An employment forecast, compared with simple baselines and tested on data it has not seen | Planned |

## Try it

You need Python 3.11 or newer.

```bash
pip install -e .
```

```bash
qcew-run --export
```

The second command works out the newest year BLS has published, then downloads the five most recent yearly files (2021 to 2025 today, about 390 MB in total) and builds the database. The `data/` and `outputs/` folders appear in the folder you run it from. Set `QCEW_RAW_DIR` to keep the downloads somewhere else.

### With Docker

```bash
docker build -t qcew-pipeline .
docker run --rm -v "$PWD/data:/app/data" -v "$PWD/outputs:/app/outputs" qcew-pipeline
```

The database ends up in `./data` and the logs and CSV files in `./outputs`. Anything you add after the image name goes to the program, for example `--years 2025`.

## For developers

```bash
pip install -e ".[dev]"
python -m pytest -q        # the tests
ruff check .               # style and common mistakes
mypy                       # type checks
```

GitHub runs all of these, and checks that the Docker image builds and starts, on every push, so you do not have to run them yourself.

## Settings

Settings such as the states and the length of the year window are in [`qcew_pipeline/config.toml`](qcew_pipeline/config.toml). By default the pipeline follows the newest published year: the window is always the most recent five years, ending with the newest, and it moves forward by itself when BLS publishes a new year. Years that drop out of the window stay in the database. There is a diagram and a list of design choices in [`docs/architecture.md`](docs/architecture.md), and every table is described in [`docs/data_dictionary.md`](docs/data_dictionary.md).

## Data

Bureau of Labor Statistics, Quarterly Census of Employment and Wages, from <https://www.bls.gov/cew/>. It is public government data and contains no personal information. The numbers in the database are the ones BLS published. This project only filters them, adds names to the codes, and checks them.

## License

MIT. See [`LICENSE`](LICENSE).
