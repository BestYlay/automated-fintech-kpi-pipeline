WITH paid AS (
 SELECT installment_id,sum(amount) amount_paid,sum(principal_paid) principal_paid
 FROM staging.payment_latest WHERE payment_date<=%(report_date)s::date GROUP BY installment_id
), loans AS (
 SELECT l.loan_id,date_trunc('month',l.disbursement_date)::date origination_month,
 sum(greatest(i.scheduled_principal-coalesce(p.principal_paid,0),0)) balance,
 max(CASE WHEN coalesce(p.amount_paid,0)<i.amount_due THEN greatest(%(report_date)s::date-i.due_date,0) ELSE 0 END) dpd
 FROM staging.loan_latest l JOIN staging.installment_latest i USING(loan_id)
 LEFT JOIN paid p USING(installment_id)
 WHERE l.disbursement_date<=%(report_date)s::date GROUP BY l.loan_id,l.disbursement_date
)
INSERT INTO mart.vintage_kpi
(report_date,origination_month,months_on_book,active_loans,outstanding_principal,dpd_30_balance,dpd_30_rate)
SELECT %(report_date)s::date,origination_month,
 (extract(year FROM age(date_trunc('month',%(report_date)s::date),origination_month))*12+
 extract(month FROM age(date_trunc('month',%(report_date)s::date),origination_month)))::integer,
 count(*) FILTER(WHERE balance>0),sum(balance),
 coalesce(sum(balance) FILTER(WHERE dpd>=30),0),
 coalesce(sum(balance) FILTER(WHERE dpd>=30),0)/nullif(sum(balance),0)
FROM loans GROUP BY origination_month;
