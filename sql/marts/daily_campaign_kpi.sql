INSERT INTO mart.daily_campaign_kpi (
    report_date, campaign_id, channel, touches, opens, clicks,
    applications, funded_count, funded_amount
)
SELECT
    t.touch_date,
    t.campaign_id,
    t.channel,
    COUNT(DISTINCT t.touch_id),
    COUNT(DISTINCT t.touch_id) FILTER (WHERE t.opened),
    COUNT(DISTINCT t.touch_id) FILTER (WHERE t.clicked),
    COUNT(DISTINCT a.application_id),
    COUNT(DISTINCT l.loan_id),
    COALESCE(SUM(l.principal), 0)
FROM staging.marketing_touch_latest t
LEFT JOIN staging.application_latest a
    ON a.customer_id = t.customer_id
   AND a.campaign_id = t.campaign_id
   AND a.application_date BETWEEN t.touch_date AND t.touch_date + 7
LEFT JOIN staging.loan_latest l
    ON l.application_id = a.application_id
   AND l.disbursement_date <= %(report_date)s::DATE
WHERE t.touch_date = %(report_date)s::DATE
GROUP BY t.touch_date, t.campaign_id, t.channel;
