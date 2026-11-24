# Managed Analytics: Labor Market Data Pipeline

This project takes job and wage numbers that the Bureau of Labor Statistics (BLS) gives away for free, cleans them up, checks them, and saves them in one small database that anyone can ask questions of. It's a sample project from [Managed Analytics](https://managedanalytics.co). I built it on public data so you can look at every step.

The numbers come from a BLS program called the Quarterly Census of Employment and Wages, or QCEW for short. It counts jobs, businesses and wages, county by county. This project covers Washington DC, Maryland and Virginia, and private-sector jobs only.

## Where it came from

I first built this pipeline for a graduate school capstone: [dmv_labor_market_pipeline](https://github.com/oseiakotokwarteng-R-Insights/dmv_labor_market_pipeline). That repository stays the way it is. Here I'm taking the same code and making it do what a real client project would need.

## What it does

It does four things, one after the other:

1. **Collect.** It downloads each year's file from BLS and tries again if a download fails. It writes down where each file came from, when it was downloaded, and a short code (a SHA-256 hash) that's unique to that file, so anyone can check they have the exact same one.
2. **Transform.** The national files are huge, about 3.6 million rows a year. It reads them in pieces and keeps only the rows I need. When BLS hides a number to protect a business's privacy, I leave it blank. I never turn it into a zero.
3. **Validate.** It runs 18 checks. If one finds a real error, the run stops and nothing gets saved. Two of the checks only warn, and they compare each year with the one before it (more on that below).
4. **Load.** It saves everything into a SQLite database in one go. Running it twice doesn't create duplicates. If BLS changes a number it already published, I count it. If BLS stops publishing a row, that row moves to a separate table, so nothing quietly disappears.

By default it works out the newest year BLS has published and pulls that year plus the four before it. Right now that means 2021 to 2025. When BLS publishes 2026, it will move to 2022 to 2026 on its own. Years that drop out of the window stay in the database. They just stop being refreshed.

## A real run

I ran it in Google Colab from a fresh copy of this repository. It downloaded the five files, read about 17.7 million rows, and kept 16,262 of them. That took about 140 seconds, and all 16 of the checks it had then passed. I ran it a second time right after. It found nothing to insert, nothing changed, and nothing removed, and it took 82 seconds because the files were already on disk. Both runs ended with the same data fingerprint, so the second run rebuilt exactly what the first one saved. All 92 automated tests passed there too.

One thing I noticed: the share of values BLS hides keeps going up. It was about 24% in 2021 and 2022, 28% in 2024, and 36% in 2025. The 2025 file also has about 16% fewer rows nationally than 2024. I don't know why yet. The pipeline recorded these numbers but didn't say anything about them, so I added two warnings. One is for a big jump in the share of hidden values from one year to the next, and the other is for a big change in how many rows BLS published. Both would have flagged 2025. They don't stop a run. They show up in the log and in the database so I can go and look.

## What I'm working on

1. **Make it sturdier.** Done. It now notices rows BLS removes, loads faster, follows the newest year by itself, warns when the data suddenly changes shape, runs in Docker, and has style and type checks.
2. **An analytics layer.** Parquet files, then DuckDB and dbt models with tests.
3. **Scheduling and alerts.** Dagster to run it on a schedule, and an email when something needs attention.
4. **More sources.** A second source, checks between the two, and California added by changing a setting instead of the code.
5. **A small dashboard** for people who don't want to write SQL.
6. **A forecast** of employment, compared against simple baselines and tested on data it hasn't seen.

## Try it

You need Python 3.11 or newer. Then run these two commands:

```bash
pip install -e .
```

```bash
qcew-run --export
```

The second one works out the newest year, downloads the five most recent files (about 390 MB in total), and builds the database. A `data` folder and an `outputs` folder show up in the folder you ran it from. If you'd rather keep the big downloads somewhere else, set `QCEW_RAW_DIR` to that folder.

### With Docker

```bash
docker build -t qcew-pipeline .
docker run --rm -v "$PWD/data:/app/data" -v "$PWD/outputs:/app/outputs" qcew-pipeline
```

The database ends up in `./data` and the logs and CSV files in `./outputs`. Anything you type after the image name goes to the program, like `--years 2025`.

### Checking for new data

```bash
qcew-check-updates
```

That asks BLS which year is the newest. GitHub also runs this check once a month and opens an issue when there's a year it hasn't told me about yet. I haven't seen that part run on GitHub yet.

### If you want to work on the code

```bash
pip install -e ".[dev]"
python -m pytest -q
ruff check .
mypy
```

GitHub runs all of these on every push, and it checks that the Docker image builds and starts, so you don't have to run them yourself.

The settings, like the states and how many years to pull, are in [`qcew_pipeline/config.toml`](qcew_pipeline/config.toml). There's a diagram and a list of the choices I made in [`docs/architecture.md`](docs/architecture.md), and [`docs/data_dictionary.md`](docs/data_dictionary.md) explains every table in plain words.

## Where the data comes from

The Bureau of Labor Statistics, Quarterly Census of Employment and Wages, from <https://www.bls.gov/cew/>. It's public government data and has no personal information in it. The numbers in my database are the ones BLS published. All I do is filter them, add names to the codes, and check them.

## License

MIT. See [`LICENSE`](LICENSE).
