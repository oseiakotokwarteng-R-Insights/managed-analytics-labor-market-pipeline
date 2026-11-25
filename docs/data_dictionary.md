# Data dictionary

This describes every table and field in the database (`data/qcew_dmv.sqlite` for DC, Maryland and Virginia, and `data/qcew_california.sqlite` for California; the tables are the same). All numbers come from the Bureau of Labor Statistics' Quarterly Census of Employment and Wages (QCEW) annual files. I only reshape and check them; I don't estimate or adjust anything.

**What one row means:** one area (state or county), in one year, for one ownership group and one industry level. In this project the ownership group is always **private sector**.

## `qcew_annual` (the main table)

| Field | Type | Meaning |
|---|---|---|
| `area_fips` | text | 5-digit FIPS code. `11001` is DC, `24031` is Montgomery County MD, `51013` is Arlington VA. Codes ending in `000` are whole-state rows; codes ending in `999` are BLS's "undefined county" bucket. |
| `year` | integer | Calendar year of the annual averages. |
| `own_code` | text | BLS ownership code. Always `5` (private) here. |
| `industry_code` | text | NAICS code. `10` means all industries. Two-digit sector codes like `23` (Construction) and ranges like `31-33` (Manufacturing) are used for the sector rows. |
| `agglvl_code` | text | What kind of row this is: `51` state total, `71` county total, `74` county NAICS sector. |
| `disclosure_code` | text | `N` if BLS withheld the numbers to protect employer confidentiality, otherwise empty. |
| `is_suppressed` | 0/1 | 1 when `disclosure_code = 'N'`. |
| `establishments` | number | Average number of business locations covered during the year. |
| `employment` | number | Average monthly employment over the year (jobs, not people). |
| `total_wages` | number | Total wages paid during the year, in dollars. |
| `avg_weekly_wage` | number | Average weekly wage in dollars, as published by BLS. |
| `avg_annual_pay` | number | Average annual pay in dollars. About 52 times the weekly wage. |
| `source_file_id` | integer | Which downloaded file this row came from (see `source_files`). |
| `last_run_id` | integer | The last pipeline run that wrote this row. |

**Primary key:** `(area_fips, year, own_code, industry_code)`.

**Suppressed values.** When `is_suppressed = 1`, all five number columns are `NULL`. BLS puts zeros in the file for those rows; I replace them with NULL because the true value is not zero, it is just hidden. A real zero (for example, an industry that genuinely has no employment in a county) stays as `0` and has `is_suppressed = 0`.

**What these numbers can't tell you.** QCEW counts jobs at employers covered by unemployment insurance, grouped by where the employer is located. It says nothing about individual workers, where they live, their demographics, or why a number changed.

## `dim_area`, `dim_industry`, `dim_ownership`, `dim_agglevel`

These lookup tables come straight from BLS's own code files.

| Table | Fields |
|---|---|
| `dim_area` | `area_fips`, `area_title`, `state_fips`, `area_type` (state / county / undefined) |
| `dim_industry` | `industry_code`, `industry_title` |
| `dim_ownership` | `own_code`, `own_title` |
| `dim_agglevel` | `agglvl_code`, `agglvl_title` |

## The Parquet files

`qcew-run --publish` (or `qcew-publish`) writes the same data as Parquet files in the `lake_dir` folder, `data/lake` by default:

| File | What it holds |
|---|---|
| `qcew_annual/year=YYYY/data.parquet` | The main table, one folder per year. Same columns as `qcew_annual`, except that the year is in the folder name instead of in the file, and `source_sha256` (the fingerprint of the BLS file the row came from) replaces `source_file_id`. |
| `dim_area.parquet`, `dim_industry.parquet`, `dim_ownership.parquet`, `dim_agglevel.parquet` | The code lists with their names, the same as the database tables. |
| `manifest.json` | When it was published, the row count for each table and year, and the `analytic_hash` of the database state it was written from. |

Hidden values are empty in the files, not zero. Every publish replaces the whole folder, so the files always match the database exactly.

## `qcew_annual_removed`

Rows that used to be in `qcew_annual` but that BLS stopped publishing. I move them here instead of deleting them, so nothing is lost. It has the same columns as `qcew_annual`, plus `removed_run_id` (the run that noticed) and `removed_at` (UTC time). There's no primary key, because a row can be removed, come back, and be removed again.

## `pipeline_runs`

One row per run of the pipeline.

| Field | Meaning |
|---|---|
| `run_id` | Run number. |
| `started_at`, `finished_at` | UTC timestamps. |
| `status` | `success`, `failed` (download or processing problem) or `failed_validation` (data didn't pass checks, nothing loaded). |
| `years` | Years requested. |
| `rows_scanned` | Rows read from the national files. |
| `rows_kept` | Rows left after filtering to DC/MD/VA, private, and the chosen aggregation levels. |
| `rows_inserted`, `rows_changed`, `rows_unchanged` | What the load did. `rows_changed` counts existing rows whose values differ from what was already stored, which is how a BLS revision would show up. |
| `rows_removed` | Stored rows (same years, states and sectors) that BLS no longer publishes. They are moved to `qcew_annual_removed`. |
| `validation_errors`, `validation_warnings` | Counts of failed checks. |
| `analytic_hash` | Fingerprint of the data in `qcew_annual` (ignores run ids and timestamps). Two runs with the same hash produced the same data. |
| `notes` | Error message if the run failed. |

## `source_files`

Where every file came from.

| Field | Meaning |
|---|---|
| `year`, `url` | The BLS file requested. |
| `retrieved_at` | When it was downloaded (UTC). |
| `download_status` | `downloaded` or `reused_existing`. |
| `size_bytes`, `sha256` | Fingerprint of the file, so anyone can confirm they have the same one. |
| `rows_scanned`, `rows_kept` | Rows read and kept from this file. |

## `validation_results`

One row per check per run: `check_name`, `level` (error / warn / info), `passed`, `failed_count`, `detail`.

| Check | Level | What it looks for |
|---|---|---|
| `all_years_present_with_enough_rows` | error | A year missing, or suspiciously small. |
| `required_fields_not_null` | error | Missing key fields. |
| `no_duplicate_keys` | error | Two rows for the same area, year, ownership and industry. |
| `valid_area_codes`, `valid_industry_codes`, `valid_ownership_codes` | error | Codes that aren't in BLS's own lookup files. |
| `areas_inside_requested_states` | error | Rows outside DC, Maryland and Virginia. |
| `years_inside_requested_range` | error | Years that weren't requested. |
| `suppressed_values_are_null_not_zero` | error | A suppressed row that still has numbers. |
| `no_negative_measures` | error | Negative counts or wages. |
| `annual_pay_matches_weekly_wage` | warn | Annual pay far from 52 x weekly wage. |
| `expected_county_counts_each_year` | warn | Wrong number of counties for a state. |
| `same_counties_in_every_year` | warn | A county that appears in some years but not others. |
| `county_totals_agree_with_state_total` | warn | County totals not adding up to BLS's published state total (within 2%). |
| `sector_sum_not_above_county_total` | warn | Sector employment adding up to more than the county total. |
| `suppression_rate` | info | How many rows are suppressed. |
| `suppression_rate_stable` | warn | A year whose share of hidden values is more than 5 points higher than the year before. |
| `source_rows_stable` | warn | A year whose national BLS file has more than 10% more or fewer rows than the year before. |

Errors stop the load. Warnings are written down for me to look at.

## `cbp_county` (the second source)

County Business Patterns from the Census Bureau: one row per county, year and industry. It's kept apart from `qcew_annual` and compared with it by the `v_qcew_vs_cbp` view. Only the states in the region are kept, and only all industries and two-digit sectors.

| Column | Meaning |
|---|---|
| `area_fips` | Five-digit county code (state plus county), the same as in `qcew_annual`. |
| `year` | The data year. |
| `naics_code` | Census's own industry code: `------` for all industries, or `23----` for a sector. |
| `industry_code` | The matching QCEW code: `10` for all industries, `23`, `31-33` and so on. |
| `employment` | Employees in the week of March 12. Census adds noise. Empty if Census withheld it, never zero. |
| `employment_flag` | Census's noise flag (G is under 2% noise, H is 2% to under 5%, J is 5% or more), or a withheld flag (D or S). |
| `annual_payroll` | Annual payroll in dollars. Census publishes thousands of dollars, so I multiply by 1,000. Empty if withheld. |
| `payroll_flag` | The same kind of flag, for payroll. |
| `establishments` | Number of establishments (business locations). |
| `is_withheld` | 1 if employment was withheld. |
| `cbp_file_id` | Which downloaded file the row came from (see `cbp_files`). |

## `cbp_files` and `cross_source_results`

`cbp_files` has one row each time a Census file is loaded: the year, where it came from, when, its size and SHA-256 fingerprint, and how many rows were read and kept. `cross_source_results` keeps every result of every comparison: the check name, its level (`warn` or `info`), whether it passed, and a plain-words detail.

| Check | Level | What it means |
|---|---|---|
| `cbp_rows_matched` | info | How many QCEW county rows have a Census row for the same county and industry. |
| `cbp_state_employment_ratio` | warn | A state whose employment, added up from its counties, is more than 15% away from QCEW. |
| `cbp_state_establishment_ratio` | warn | The same for the number of businesses (25%). |
| `cbp_county_outliers` | warn | A county with at least 1,000 jobs whose Census-to-QCEW ratio is outside 0.5 to 1.5. |
| `cbp_sector_ratios` | info | The typical Census-to-QCEW ratio for each industry. |
| `cbp_withheld_share` | info | How much of the employment data each source hides. |

## Views

* `v_county_totals`: county-level all-industry rows with area names, ready to query.
* `v_county_sectors`: county-level sector rows with area and industry names.
* `v_qcew_vs_cbp`: QCEW and County Business Patterns side by side for every county and industry both publish (private sector only).
