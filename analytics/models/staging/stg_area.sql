-- Every area BLS uses (a state, a county, or an "undefined county" bucket), with the state's name added.
select
    a.area_fips,
    a.area_title,
    a.state_fips,
    s.state_name,
    s.state_abbr,
    a.area_type
from {{ lake_table('dim_area') }} as a
left join {{ ref('state_fips') }} as s
    on s.state_fips = a.state_fips
