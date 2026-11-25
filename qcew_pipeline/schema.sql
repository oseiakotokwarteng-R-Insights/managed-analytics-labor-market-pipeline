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
    rows_removed        INTEGER,           -- stored rows BLS no longer published; moved to qcew_annual_removed
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

-- Rows that were in the database but that BLS stopped publishing. They are moved here,
-- not deleted outright, so the history is never lost. No primary key: a row can be
-- removed, come back, and be removed again.
CREATE TABLE IF NOT EXISTS qcew_annual_removed (
    area_fips       TEXT NOT NULL,
    year            INTEGER NOT NULL,
    own_code        TEXT NOT NULL,
    industry_code   TEXT NOT NULL,
    agglvl_code     TEXT NOT NULL,
    disclosure_code TEXT,
    is_suppressed   INTEGER NOT NULL,
    establishments  REAL,
    employment      REAL,
    total_wages     REAL,
    avg_weekly_wage REAL,
    avg_annual_pay  REAL,
    source_file_id  INTEGER,
    last_run_id     INTEGER,
    removed_run_id  INTEGER NOT NULL REFERENCES pipeline_runs(run_id),
    removed_at      TEXT NOT NULL
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


-- A second source: the Census Bureau's County Business Patterns (CBP). It is kept apart from the BLS
-- numbers on purpose, so the two can be compared without one changing the other.
-- Withheld values are stored as NULL (never zero), the same rule as for QCEW.
CREATE TABLE IF NOT EXISTS cbp_files (
    cbp_file_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    year           INTEGER NOT NULL,
    url            TEXT NOT NULL,
    local_name     TEXT,
    retrieved_at   TEXT,
    download_status TEXT,
    size_bytes     INTEGER,
    sha256         TEXT,
    rows_scanned   INTEGER,
    rows_kept      INTEGER,
    loaded_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cbp_county (
    area_fips       TEXT NOT NULL,
    year            INTEGER NOT NULL,
    naics_code      TEXT NOT NULL,         -- CBP's own code: '------' (all industries) or '23----' (a sector)
    industry_code   TEXT NOT NULL,         -- the matching QCEW code: '10' (all industries), '23', '31-33'
    employment      REAL,                  -- employees in the week of March 12. Census adds noise. NULL if withheld.
    employment_flag TEXT,                  -- Census noise flag (G, H, J) or withheld flag (D, S)
    annual_payroll  REAL,                  -- dollars (Census reports thousands of dollars). NULL if withheld.
    payroll_flag    TEXT,
    establishments  INTEGER,
    is_withheld     INTEGER NOT NULL CHECK (is_withheld IN (0, 1)),
    cbp_file_id     INTEGER REFERENCES cbp_files(cbp_file_id),
    PRIMARY KEY (area_fips, year, naics_code)
);

CREATE INDEX IF NOT EXISTS idx_cbp_year_industry ON cbp_county (year, industry_code);

-- The results of comparing the two sources, from every comparison.
CREATE TABLE IF NOT EXISTS cross_source_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    compared_at  TEXT NOT NULL,
    check_name   TEXT NOT NULL,
    level        TEXT NOT NULL,            -- warn / info
    passed       INTEGER NOT NULL,
    failed_count INTEGER,
    detail       TEXT
);

-- QCEW and CBP side by side for every county and industry that both publish. Private sector only, because
-- that is what CBP covers. The two don't measure exactly the same thing (see docs/architecture.md).
CREATE VIEW IF NOT EXISTS v_qcew_vs_cbp AS
SELECT q.year, a.state_fips, q.area_fips, a.area_title, q.industry_code,
       q.employment AS qcew_employment, c.employment AS cbp_employment,
       q.establishments AS qcew_establishments, c.establishments AS cbp_establishments,
       q.is_suppressed AS qcew_suppressed, c.is_withheld AS cbp_withheld
FROM qcew_annual q
JOIN dim_area a ON a.area_fips = q.area_fips AND a.area_type = 'county'
JOIN cbp_county c ON c.area_fips = q.area_fips AND c.year = q.year AND c.industry_code = q.industry_code
WHERE q.own_code = '5' AND q.agglvl_code IN ('71', '74');
