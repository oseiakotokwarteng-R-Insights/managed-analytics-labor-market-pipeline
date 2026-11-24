-- The counties in a state should add up to the state total BLS published, within the tolerance.
-- Returns any state and year where they don't.
select *
from {{ ref('mart_state_year') }}
where abs(gap_pct) > {{ var('state_gap_tolerance') }}
