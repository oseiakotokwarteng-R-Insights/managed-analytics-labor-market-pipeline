-- One row per state and year: the state total that BLS published, next to what is in its counties.
--
-- BLS also keeps a bucket for jobs it can't place in any single county, filed under an area called
-- "unknown or undefined" (area codes ending in 999). In Maryland and Virginia that bucket holds a few
-- percent of all jobs. So the state total is made of three things: the named counties, that bucket, and nothing else.
-- gap_pct is how far counties + bucket are from the BLS total, as a share of the total, and a test fails if it is
-- bigger than state_gap_tolerance. share_not_in_a_county is how much of the state's jobs sit in the bucket, which is
-- worth knowing before reading anything into county numbers.
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
),

undefined_areas as (
    select
        a.state_fips,
        q.year,
        sum(q.employment) as undefined_area_employment
    from {{ ref('stg_qcew_annual') }} as q
    inner join {{ ref('stg_area') }} as a
        on a.area_fips = q.area_fips
    where q.agglvl_code = '71'          -- county-level rows
      and q.industry_code = '10'        -- all industries together
      and a.area_type = 'undefined'     -- the "unknown or undefined" bucket
    group by a.state_fips, q.year
),

joined as (
    select
        t.state_fips,
        t.state_name,
        t.year,
        t.bls_state_employment,
        c.county_sum_employment,
        c.county_count,
        c.hidden_county_count,
        -- no bucket row at all means no jobs in it; a bucket row that BLS hid stays empty rather than becoming zero
        case when u.state_fips is null then 0 else u.undefined_area_employment end as undefined_area_employment
    from state_totals as t
    left join county_sums as c
        on c.state_fips = t.state_fips
        and c.year = t.year
    left join undefined_areas as u
        on u.state_fips = t.state_fips
        and u.year = t.year
)

select
    *,
    case
        when bls_state_employment > 0
            then (county_sum_employment + undefined_area_employment - bls_state_employment) / bls_state_employment
    end as gap_pct,
    case
        when bls_state_employment > 0
            then undefined_area_employment / bls_state_employment
    end as share_not_in_a_county
from joined
