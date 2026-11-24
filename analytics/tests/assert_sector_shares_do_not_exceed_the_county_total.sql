-- The sectors in a county can add up to less than the county total (hidden values are left out),
-- but never to more than it, beyond a small rounding allowance.
select
    area_fips,
    year,
    sum(employment_share_of_county) as total_share
from {{ ref('mart_county_sector_year') }}
group by area_fips, year
having sum(employment_share_of_county) > 1 + {{ var('sector_sum_tolerance') }}
