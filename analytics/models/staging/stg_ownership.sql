-- Who owns the business (5 is private sector, which is all this project uses).
select
    own_code,
    own_title
from {{ lake_table('dim_ownership') }}
