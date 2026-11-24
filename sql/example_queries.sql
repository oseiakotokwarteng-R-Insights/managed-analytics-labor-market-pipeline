-- A few example questions you can ask the finished database.
-- Open data/qcew_dmv.sqlite in any SQLite tool (DB Browser for SQLite, DBeaver, the sqlite3 command line)
-- and paste these in. NULL in a number column means BLS did not publish it, not zero.

-- 1. Private-sector employment and average weekly wage for every county, one year.
SELECT area_title, employment, avg_weekly_wage
FROM v_county_totals
WHERE year = 2024 AND is_suppressed = 0
ORDER BY employment DESC;

-- 2. How did private employment change from 2020 to 2024 in each state?
SELECT state_fips,
       SUM(CASE WHEN year = 2020 THEN employment END) AS jobs_2020,
       SUM(CASE WHEN year = 2024 THEN employment END) AS jobs_2024
FROM v_county_totals
GROUP BY state_fips;

-- 3. The five highest-paying sectors in Arlington County, VA (area 51013) in 2024.
SELECT industry_title, employment, avg_weekly_wage
FROM v_county_sectors
WHERE area_fips = '51013' AND year = 2024 AND is_suppressed = 0
ORDER BY avg_weekly_wage DESC
LIMIT 5;

-- 4. How much of the data is hidden by suppression, by year?
SELECT year,
       COUNT(*) AS rows,
       SUM(is_suppressed) AS suppressed_rows,
       ROUND(100.0 * SUM(is_suppressed) / COUNT(*), 1) AS pct_suppressed
FROM qcew_annual
GROUP BY year;

-- 5. Where did a number come from? Trace one row back to the file that supplied it.
SELECT q.area_fips, q.year, q.employment, s.url, s.retrieved_at, s.sha256
FROM qcew_annual q
JOIN source_files s ON s.source_file_id = q.source_file_id
WHERE q.area_fips = '11001' AND q.industry_code = '10' AND q.year = 2024;

-- 6. What happened in each pipeline run?
SELECT run_id, started_at, status, rows_inserted, rows_changed, rows_unchanged,
       validation_errors, validation_warnings
FROM pipeline_runs
ORDER BY run_id DESC;

-- 7. Which rows did BLS stop publishing, and when did we notice?
SELECT removed_run_id, removed_at, area_fips, year, industry_code, employment
FROM qcew_annual_removed
ORDER BY removed_at DESC;
