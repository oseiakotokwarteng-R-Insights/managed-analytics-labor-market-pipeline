{# Reads one of the Parquet files that qcew-publish wrote. The folder comes from QCEW_LAKE_DIR.
   The main table is split into one folder per year (year=2025 and so on), and the year is read
   from the folder name. #}
{% macro lake_table(name) -%}
    {%- set lake = env_var('QCEW_LAKE_DIR', '../data/lake') -%}
    {%- if name == 'qcew_annual' -%}
        read_parquet('{{ lake }}/qcew_annual/*/data.parquet', hive_partitioning = true)
    {%- else -%}
        read_parquet('{{ lake }}/{{ name }}.parquet')
    {%- endif -%}
{%- endmacro %}
