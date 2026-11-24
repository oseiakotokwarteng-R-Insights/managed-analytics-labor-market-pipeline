-- The main table, straight from the Parquet files, with plain names and types.
-- One row is one area, one year, one ownership type and one industry.
-- A hidden (suppressed) value is NULL here, never zero.
select
    area_fips,
    cast(year as integer) as year,
    own_code,
    industry_code,
    agglvl_code,
    disclosure_code,
    is_suppressed = 1 as is_suppressed,
    establishments,
    employment,
    total_wages,
    avg_weekly_wage,
    avg_annual_pay,
    source_sha256
from {{ lake_table('qcew_annual') }}
