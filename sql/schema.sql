-- Database layout for the QCEW pipeline (SQLite).
-- Safe to run more than once: everything is CREATE ... IF NOT EXISTS.

-- One row per pipeline run, so I can see what happened and when.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    status              TEXT NOT NULL,     -- running / success / failed_validation / failed
    years               TEXT,
    rows_scanned        INTEGER,
    rows_kept           INTEGER,
    rows_inserted       INTEGER,
    rows_changed        INTEGER,           -- existing rows whose values were different this time
    rows_unchanged      INTEGER,
    validation_errors   INTEGER,
    validation_warnings INTEGER,
    analytic_hash       TEXT,              -- fingerprint of the data tables, for reproducibility checks
    notes               TEXT
);

-- One row per file I pulled from BLS (where it came from, when, and its fingerprint).
CREATE TABLE IF NOT EXISTS source_files (
    source_file_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES pipeline_runs(run_id),
    year            INTEGER NOT NULL,
    url             TEXT NOT NULL,
    local_name      TEXT,
    retrieved_at    TEXT,
    download_status TEXT,
    size_bytes      INTEGER,
    sha256          TEXT,
    rows_scanned    INTEGER,
    rows_kept       INTEGER
);

-- Every validation result from every run.
CREATE TABLE IF NOT EXISTS validation_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES pipeline_runs(run_id),
    check_name   TEXT NOT NULL,
    level        TEXT NOT NULL,            -- error / warn / info
    passed       INTEGER NOT NULL,
    failed_count INTEGER,
    detail       TEXT
);

-- Lookup tables that come straight from BLS's own code/title files.
CREATE TABLE IF NOT EXISTS dim_area (
    area_fips   TEXT PRIMARY KEY,
    area_title  TEXT NOT NULL,
    state_fips  TEXT NOT NULL,
    area_type   TEXT NOT NULL              -- state / county / undefined
);

CREATE TABLE IF NOT EXISTS dim_industry (
    industry_code  TEXT PRIMARY KEY,
    industry_title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_ownership (
    own_code  TEXT PRIMARY KEY,
    own_title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_agglevel (
    agglvl_code  TEXT PRIMARY KEY,
    agglvl_title TEXT NOT NULL
);

-- The main table. One row = one area + year + ownership + industry.
-- Suppressed values are stored as NULL (never zero) and flagged in is_suppressed.
CREATE TABLE IF NOT EXISTS qcew_annual (
    area_fips       TEXT NOT NULL REFERENCES dim_area(area_fips),
    year            INTEGER NOT NULL,
    own_code        TEXT NOT NULL REFERENCES dim_ownership(own_code),
    industry_code   TEXT NOT NULL REFERENCES dim_industry(industry_code),
    agglvl_code     TEXT NOT NULL,
    disclosure_code TEXT,
    is_suppressed   INTEGER NOT NULL CHECK (is_suppressed IN (0, 1)),
    establishments  REAL,
    employment      REAL,
    total_wages     REAL,
    avg_weekly_wage REAL,
    avg_annual_pay  REAL,
    source_file_id  INTEGER REFERENCES source_files(source_file_id),
    last_run_id     INTEGER REFERENCES pipeline_runs(run_id),
    PRIMARY KEY (area_fips, year, own_code, industry_code)
);

CREATE INDEX IF NOT EXISTS idx_qcew_year_industry ON qcew_annual (year, industry_code);

-- Handy views so people don't have to remember the codes.
CREATE VIEW IF NOT EXISTS v_county_totals AS
SELECT q.year, a.state_fips, q.area_fips, a.area_title,
       q.establishments, q.employment, q.avg_weekly_wage, q.is_suppressed
FROM qcew_annual q
JOIN dim_area a ON a.area_fips = q.area_fips
WHERE q.agglvl_code = '71' AND a.area_type = 'county';

CREATE VIEW IF NOT EXISTS v_county_sectors AS
SELECT q.year, a.state_fips, q.area_fips, a.area_title,
       q.industry_code, i.industry_title,
       q.employment, q.avg_weekly_wage, q.is_suppressed
FROM qcew_annual q
JOIN dim_area a ON a.area_fips = q.area_fips
JOIN dim_industry i ON i.industry_code = q.industry_code
WHERE q.agglvl_code = '74' AND a.area_type = 'county';
