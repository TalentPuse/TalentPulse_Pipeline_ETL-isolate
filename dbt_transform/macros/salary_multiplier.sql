{% macro salary_multiplier() %}
{#-
  Period -> months multiplier for a salary figure, with the hourly sanity check
  folded in. Repeated in three near-identical expressions in silver_job_detail
  (min / max / avg) and previously copy-pasted; one copy drifting from the others
  is exactly how a salary bug hides.

  The hourly test is done on the VND-converted figure. Comparing the raw number
  against 10,000,000 assumed the currency was VND, so a posting priced in USD and
  mislabelled Hourly (2000 USD "per hour") sailed through and got multiplied by
  172. Convert first and the threshold means the same thing in every currency.
-#}
case when r.salary_period_id = 2
          and r.salary_min * coalesce(fx.vnd_rate, 1) > 10000000
     then 1.0
     else coalesce(spm.months_multiplier, 1.0)
end
{%- endmacro %}
