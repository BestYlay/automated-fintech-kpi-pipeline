WITH paid AS (
 SELECT installment_id,sum(amount) amount_paid,sum(principal_paid) principal_paid
 FROM staging.payment_latest WHERE payment_date<=%(report_date)s::date GROUP BY installment_id
), loans AS (
 SELECT l.loan_id,l.risk_grade,
 sum(greatest(i.scheduled_principal-coalesce(p.principal_paid,0),0)) balance,
 coalesce(sum(i.amount_due) FILTER(WHERE i.due_date<=%(report_date)s::date),0) amount_due,
 coalesce(sum(least(coalesce(p.amount_paid,0),i.amount_due)) FILTER(WHERE i.due_date<=%(report_date)s::date),0) amount_paid,
 max(CASE WHEN coalesce(p.amount_paid,0)<i.amount_due THEN greatest(%(report_date)s::date-i.due_date,0) ELSE 0 END) dpd
 FROM staging.loan_latest l JOIN staging.installment_latest i USING(loan_id)
 LEFT JOIN paid p USING(installment_id)
 WHERE l.disbursement_date<=%(report_date)s::date GROUP BY l.loan_id,l.risk_grade
)
INSERT INTO mart.daily_portfolio_kpi
(report_date,risk_grade,active_loans,outstanding_principal,amount_due,amount_paid,collection_rate,
 dpd_1_balance,dpd_7_balance,dpd_30_balance,dpd_90_balance)
SELECT %(report_date)s::date,risk_grade,count(*) FILTER(WHERE balance>0),sum(balance),
 sum(amount_due),sum(amount_paid),sum(amount_paid)/nullif(sum(amount_due),0),
 coalesce(sum(balance) FILTER(WHERE dpd>=1),0),coalesce(sum(balance) FILTER(WHERE dpd>=7),0),
 coalesce(sum(balance) FILTER(WHERE dpd>=30),0),coalesce(sum(balance) FILTER(WHERE dpd>=90),0)
FROM loans GROUP BY risk_grade;
