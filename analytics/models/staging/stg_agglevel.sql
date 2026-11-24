-- What kind of row it is: 51 is a state total, 71 is a county total, 74 is a county broken out by sector.
select
    agglvl_code,
    agglvl_title
from {{ lake_table('dim_agglevel') }}
