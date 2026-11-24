-- One row per county and year: private-sector jobs, businesses and pay, and how they changed from the year before.
--
-- A change is only worked out when the year before is really the previous calendar year, and when neither
-- number is hidden. Otherwise it is empty. A hidden value is never guessed or filled in.
with counties as (
    select
        q.area_fips,
        a.area_title,
        a.state_fips,
        a.state_name,
        q.year,
        q.establishments,
        q.employment,
        q.avg_weekly_wage,
        q.avg_annual_pay,
        q.is_suppressed
    from {{ ref('stg_qcew_annual') }} as q
    inner join {{ ref('stg_area') }} as a
        on a.area_fips = q.area_fips
    where q.agglvl_code = '71'          -- county totals
      and q.industry_code = '10'        -- all industries together
      and a.area_type = 'county'
),

with_previous_year as (
    select
        *,
        case when lag(year) over w = year - 1 then lag(employment) over w end as employment_prev_year,
        case when lag(year) over w = year - 1 then lag(avg_weekly_wage) over w end as avg_weekly_wage_prev_year
    from counties
    window w as (partition by area_fips order by year)
)

select
    *,
    employment - employment_prev_year as employment_change,
    case
        when employment_prev_year > 0
            then (employment - employment_prev_year) / employment_prev_year
    end as employment_change_pct,
    case
        when avg_weekly_wage_prev_year > 0
            then (avg_weekly_wage - avg_weekly_wage_prev_year) / avg_weekly_wage_prev_year
    end as avg_weekly_wage_change_pct
from with_previous_year
