INSERT INTO mart.daily_credit_kpi (
    report_date, channel, product_code, risk_grade, unique_applicants,
    applications, approvals, accepted, funded_count, requested_amount,
    funded_amount, approval_rate, acceptance_rate
)
SELECT
    a.application_date,
    a.channel,
    a.product_code,
    c.risk_grade,
    COUNT(DISTINCT a.customer_id),
    COUNT(*),
    COUNT(*) FILTER (WHERE a.decision = 'approved'),
    COUNT(*) FILTER (WHERE a.offer_accepted),
    COUNT(l.loan_id),
    SUM(a.requested_amount),
    COALESCE(SUM(l.principal), 0),
    COUNT(*) FILTER (WHERE a.decision = 'approved')::NUMERIC / NULLIF(COUNT(*), 0),
    COUNT(*) FILTER (WHERE a.offer_accepted)::NUMERIC / NULLIF(COUNT(*) FILTER (WHERE a.decision = 'approved'), 0)
FROM staging.application_latest a
JOIN staging.customer_latest c ON c.customer_id = a.customer_id
LEFT JOIN staging.loan_latest l
    ON l.application_id = a.application_id
   AND l.disbursement_date <= %(report_date)s::DATE
WHERE a.application_date = %(report_date)s::DATE
GROUP BY a.application_date, a.channel, a.product_code, c.risk_grade;
