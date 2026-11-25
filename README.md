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

I also ran the analytics layer on the same real data. dbt built one seed, five views and three summary tables, and ran 40 tests. All 49 passed, once I'd fixed a mistake the tests found (more on that below). Then I compared the database, pandas and dbt on the real numbers: 16,262 rows, 790 county-years, 15,200 county-sector-years, 15 state-years and 790 year-over-year changes. Every one matched, with no differences at all.

## What I'm working on

1. **Make it sturdier.** Done. It now notices rows BLS removes, loads faster, follows the newest year by itself, warns when the data suddenly changes shape, runs in Docker, and has style and type checks.
2. **An analytics layer.** Done. The Parquet files, the dbt project with its tests, and a check on the real data that the dbt numbers match what the database says.
3. **Scheduling.** Dagster to run it on a schedule, and to show how the data flows from one step to the next.
4. **More sources.** A second source, checks between the two, and California added by changing a setting instead of the code.
5. **A small dashboard** for people who don't want to write SQL.
6. **A forecast** of employment, compared against simple baselines and tested on data it hasn't seen.

## Parquet files

The database is good for keeping track of what the pipeline did. For analysis I also publish the data as Parquet files, which analytics tools read much faster. This part needs DuckDB:

```bash
pip install -e ".[analytics]"
qcew-run --publish
```

You get a `data/lake` folder with one folder per year, the code lists with their names, and a `manifest.json` that says what's inside. Hidden values stay empty, never zero, and every row carries the fingerprint of the file it came from. You can also run `qcew-publish` on its own to publish whatever is already in the database.

## The analytics layer

On top of the Parquet files there's a small dbt project in the `analytics` folder. DuckDB reads the files, and dbt does two things with them. It cleans them into staging tables and builds three summary tables: jobs and pay by county and year (with the change from the year before), by county and sector (with each sector's share of the county's jobs), and by state (the BLS total next to the sum of its counties). Then it runs tests on all of it.

```bash
qcew-build
```

The tests include the ones I care most about. A hidden value must never be a zero. No two rows can share the same key. Every code must exist in BLS's own list. The counties must add up to the state total, and the sectors must never add up to more than the county. If one fails, the build fails. The limits come from `config.toml`, the same ones the pipeline uses. If a notebook or a DuckDB shell still has `data/analytics.duckdb` open, close it first, because DuckDB only lets one program write to a file at a time. `qcew-build` tells you when that's the problem.

### Something the tests caught

The first time I ran the dbt tests on the real data, one of them failed. In Maryland and Virginia the counties added up to about 4% to 7% less than the state total that BLS published. The pipeline's own check had said everything matched, so something was off.

The reason is a bucket BLS calls "unknown or undefined". It holds jobs that BLS can't place in a single county. The pipeline's check had always counted that bucket. My first dbt table only looked at real counties, so it left the bucket out, and the numbers didn't add up. The test was right and my table was wrong. The state table now shows the named counties, the bucket, and the state total side by side, and the test checks that they add up. It's worth knowing before you read anything into county numbers: in Maryland and Virginia a few percent of the state's jobs don't belong to any county.

On the real data, that bucket holds between 4.4% and 5.7% of Maryland's private-sector jobs and between 5.3% and 6.6% of Virginia's, depending on the year. In DC it's about 0.01% to 0.02%, because DC is a single county. With the bucket counted, the counties and the state total agree to within a few parts in a million, which is just rounding in BLS's own numbers.

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
