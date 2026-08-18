WITH paid AS (
    SELECT installment_id,
           SUM(amount) FILTER (WHERE payment_date <= %(report_date)s::DATE) AS amount_paid,
           SUM(principal_paid) FILTER (WHERE payment_date <= %(report_date)s::DATE) AS principal_paid
    FROM staging.payment_latest
    GROUP BY installment_id
), states AS (
    SELECT
        l.loan_id,
        DATE_TRUNC('month', l.disbursement_date)::DATE AS origination_month,
        (DATE_PART('year', AGE(%(report_date)s::DATE, l.disbursement_date)) * 12
         + DATE_PART('month', AGE(%(report_date)s::DATE, l.disbursement_date)))::INTEGER AS months_on_book,
        i.due_date,
        i.amount_due,
        i.scheduled_principal,
        COALESCE(p.amount_paid, 0) AS amount_paid,
        COALESCE(p.principal_paid, 0) AS principal_paid,
        GREATEST((%(report_date)s::DATE - i.due_date), 0) AS dpd,
        l.loan_id AS active_loan
    FROM staging.loan_latest l
    JOIN staging.installment_latest i ON i.loan_id = l.loan_id
    LEFT JOIN paid p ON p.installment_id = i.installment_id
    WHERE l.disbursement_date <= %(report_date)s::DATE
)
INSERT INTO mart.vintage_kpi (
    report_date, origination_month, months_on_book, active_loans,
    outstanding_principal, dpd_30_balance, dpd_30_rate
)
SELECT
    %(report_date)s::DATE,
    origination_month,
    months_on_book,
    COUNT(DISTINCT active_loan),
    SUM(GREATEST(scheduled_principal - principal_paid, 0)),
    COALESCE(SUM(GREATEST(scheduled_principal - principal_paid, 0)) FILTER (WHERE dpd >= 30 AND amount_paid < amount_due), 0),
    COALESCE(
      SUM(GREATEST(scheduled_principal - principal_paid, 0)) FILTER (WHERE dpd >= 30 AND amount_paid < amount_due)
      / NULLIF(SUM(GREATEST(scheduled_principal - principal_paid, 0)), 0),
      0
    )
FROM states
GROUP BY origination_month, months_on_book;
