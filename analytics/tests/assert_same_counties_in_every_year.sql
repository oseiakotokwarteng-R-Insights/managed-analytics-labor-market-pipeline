{{ config(severity='warn') }}

-- A county that shows up in some years but not others is worth a look, but it is a warning, not a failure:
-- BLS does sometimes add or retire a county.
select
    area_fips,
    count(*) as years_present
from {{ ref('mart_county_year') }}
group by area_fips
having count(*) <> (select count(distinct year) from {{ ref('mart_county_year') }})
