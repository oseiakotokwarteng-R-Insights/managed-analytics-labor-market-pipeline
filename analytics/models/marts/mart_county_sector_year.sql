-- One row per county, sector and year: jobs and pay for each broad industry, and its share of the county's jobs.
-- The share is empty when either number is hidden. Hidden values are never filled in, so for a small county
-- the shares of the sectors that are shown can add up to less than 1.
with sectors as (
    select
        q.area_fips,
        a.area_title,
        a.state_fips,
        a.state_name,
        q.year,
        q.industry_code,
        i.industry_title,
        q.establishments,
        q.employment,
        q.avg_weekly_wage,
        q.is_suppressed
    from {{ ref('stg_qcew_annual') }} as q
    inner join {{ ref('stg_area') }} as a
        on a.area_fips = q.area_fips
    inner join {{ ref('stg_industry') }} as i
        on i.industry_code = q.industry_code
    where q.agglvl_code = '74'          -- county, broken out by sector
      and a.area_type = 'county'
),

county_totals as (
    select
        area_fips,
        year,
        employment as county_employment
    from {{ ref('mart_county_year') }}
)

select
    s.*,
    t.county_employment,
    case
        when t.county_employment > 0
            then s.employment / t.county_employment
    end as employment_share_of_county
from sectors as s
left join county_totals as t
    on t.area_fips = s.area_fips
    and t.year = s.year
