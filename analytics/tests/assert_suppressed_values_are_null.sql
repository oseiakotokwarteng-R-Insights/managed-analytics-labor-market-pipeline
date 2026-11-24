-- The most important rule in this project: a value BLS hid is unknown, not zero.
-- This returns any hidden row that still carries a number. A passing test returns nothing.
select *
from {{ ref('stg_qcew_annual') }}
where is_suppressed
  and (
      establishments is not null
      or employment is not null
      or total_wages is not null
      or avg_weekly_wage is not null
      or avg_annual_pay is not null
  )
