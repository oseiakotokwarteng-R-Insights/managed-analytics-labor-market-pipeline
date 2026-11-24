-- One row per state and year: the state total that BLS published, next to the sum of its counties.
-- The two should agree. gap_pct is how far apart they are, as a share of the BLS total. A test fails if it is
-- bigger than state_gap_tolerance. This is the same cross-check the pipeline runs before loading, done again
-- here in SQL on the published files.
with state_totals as (
    select
        a.state_fips,
        a.state_name,
        q.year,
        q.employment as bls_state_employment
    from {{ ref('stg_qcew_annual') }} as q
    inner join {{ ref('stg_area') }} as a
        on a.area_fips = q.area_fips
    where q.agglvl_code = '51'          -- state totals
      and q.industry_code = '10'        -- all industries together
),

county_sums as (
    select
        state_fips,
        year,
        sum(employment) as county_sum_employment,
        count(*) as county_count,
        sum(case when is_suppressed then 1 else 0 end) as hidden_county_count
    from {{ ref('mart_county_year') }}
    group by state_fips, year
)

select
    t.state_fips,
    t.state_name,
    t.year,
    t.bls_state_employment,
    c.county_sum_employment,
    c.county_count,
    c.hidden_county_count,
    case
        when t.bls_state_employment > 0
            then (c.county_sum_employment - t.bls_state_employment) / t.bls_state_employment
    end as gap_pct
from state_totals as t
left join county_sums as c
    on c.state_fips = t.state_fips
    and c.year = t.year
