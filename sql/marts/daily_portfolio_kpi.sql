WITH paid AS (
    SELECT installment_id,
           SUM(amount) FILTER (WHERE payment_date <= %(report_date)s::DATE) AS amount_paid,
           SUM(principal_paid) FILTER (WHERE payment_date <= %(report_date)s::DATE) AS principal_paid
    FROM staging.payment_latest
    GROUP BY installment_id
), installment_state AS (
    SELECT
        i.loan_id,
        i.due_date,
        i.amount_due,
        i.scheduled_principal,
        COALESCE(p.amount_paid, 0) AS amount_paid,
        COALESCE(p.principal_paid, 0) AS principal_paid,
        GREATEST((%(report_date)s::DATE - i.due_date), 0) AS days_past_due,
        l.risk_grade
    FROM staging.installment_latest i
    JOIN staging.loan_latest l ON l.loan_id = i.loan_id
    LEFT JOIN paid p ON p.installment_id = i.installment_id
    WHERE l.disbursement_date <= %(report_date)s::DATE
), grouped AS (
    SELECT
        risk_grade,
        COUNT(DISTINCT loan_id) FILTER (WHERE scheduled_principal - principal_paid > 0) AS active_loans,
        SUM(GREATEST(scheduled_principal - principal_paid, 0)) AS outstanding_principal,
        SUM(amount_due) FILTER (WHERE due_date <= %(report_date)s::DATE) AS amount_due,
        SUM(LEAST(amount_paid, amount_due)) FILTER (WHERE due_date <= %(report_date)s::DATE) AS amount_paid,
        SUM(GREATEST(scheduled_principal - principal_paid, 0)) FILTER (WHERE days_past_due >= 1 AND amount_paid < amount_due) AS dpd_1_balance,
        SUM(GREATEST(scheduled_principal - principal_paid, 0)) FILTER (WHERE days_past_due >= 7 AND amount_paid < amount_due) AS dpd_7_balance,
        SUM(GREATEST(scheduled_principal - principal_paid, 0)) FILTER (WHERE days_past_due >= 30 AND amount_paid < amount_due) AS dpd_30_balance,
        SUM(GREATEST(scheduled_principal - principal_paid, 0)) FILTER (WHERE days_past_due >= 90 AND amount_paid < amount_due) AS dpd_90_balance
    FROM installment_state
    GROUP BY risk_grade
)
INSERT INTO mart.daily_portfolio_kpi (
    report_date, risk_grade, active_loans, outstanding_principal,
    amount_due, amount_paid, collection_rate, dpd_1_balance, dpd_7_balance,
    dpd_30_balance, dpd_90_balance
)
SELECT
    %(report_date)s::DATE, risk_grade, active_loans,
    COALESCE(outstanding_principal, 0), COALESCE(amount_due, 0),
    COALESCE(amount_paid, 0), amount_paid / NULLIF(amount_due, 0),
    COALESCE(dpd_1_balance, 0), COALESCE(dpd_7_balance, 0),
    COALESCE(dpd_30_balance, 0), COALESCE(dpd_90_balance, 0)
FROM grouped;

