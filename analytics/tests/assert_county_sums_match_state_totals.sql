-- The named counties plus the jobs BLS could not place in any county should add up to the state total BLS
-- published, within the tolerance. Returns any state and year where they don't. A missing gap counts as a
-- failure too, so a hidden number can't make this test pass by default.
select *
from {{ ref('mart_state_year') }}
where gap_pct is null
   or abs(gap_pct) > {{ var('state_gap_tolerance') }}
