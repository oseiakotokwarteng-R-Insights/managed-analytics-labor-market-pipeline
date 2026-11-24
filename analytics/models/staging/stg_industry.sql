-- BLS industry codes (NAICS) and their names. Code 10 means all industries together.
select
    industry_code,
    industry_title
from {{ lake_table('dim_industry') }}
