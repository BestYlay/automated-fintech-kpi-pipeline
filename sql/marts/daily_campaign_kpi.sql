WITH attribution AS (
 SELECT a.application_id,t.touch_id FROM staging.application_latest a
 CROSS JOIN LATERAL (
   SELECT touch_id FROM staging.marketing_touch_latest t
   WHERE t.customer_id=a.customer_id AND t.touch_date BETWEEN a.application_date-7 AND a.application_date
   ORDER BY t.touch_date DESC,t.touch_id DESC LIMIT 1
 ) t WHERE a.application_date<=%(report_date)s::date
)
INSERT INTO mart.daily_campaign_kpi
(report_date,touch_date,campaign_id,channel,touches,opens,clicks,applications,funded_count,funded_amount)
SELECT %(report_date)s::date,t.touch_date,t.campaign_id,t.channel,
 count(DISTINCT t.touch_id),count(DISTINCT t.touch_id) FILTER(WHERE t.opened),
 count(DISTINCT t.touch_id) FILTER(WHERE t.clicked),count(DISTINCT a.application_id),
 count(DISTINCT l.loan_id),coalesce(sum(l.principal),0)
FROM staging.marketing_touch_latest t
LEFT JOIN attribution a USING(touch_id)
LEFT JOIN staging.loan_latest l ON l.application_id=a.application_id AND l.disbursement_date<=%(report_date)s::date
WHERE t.touch_date BETWEEN %(report_date)s::date-30 AND %(report_date)s::date
GROUP BY t.touch_date,t.campaign_id,t.channel;
