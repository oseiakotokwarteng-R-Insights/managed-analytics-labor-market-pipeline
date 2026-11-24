{# Two small tests that dbt does not ship with. A test passes when its query returns no rows. #}

{# No two rows may share the same values in this set of columns. #}
{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as rows_sharing_the_key
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}

{# A count or a wage can be missing, but it can never be below zero. #}
{% test non_negative(model, column_name) %}
select *
from {{ model }}
where {{ column_name }} < 0
{% endtest %}
