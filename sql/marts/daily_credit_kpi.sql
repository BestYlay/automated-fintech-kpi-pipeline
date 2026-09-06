-- Acquisition and funding are independent daily flows.
WITH applications AS (
 SELECT a.channel,a.product_code,c.risk_grade,
 count(DISTINCT a.customer_id) unique_applicants,count(*) applications,
 count(*) FILTER(WHERE a.decision='approved') approvals,
 count(*) FILTER(WHERE a.offer_accepted) accepted,sum(a.requested_amount) requested_amount
 FROM staging.application_latest a JOIN staging.customer_latest c USING(customer_id)
 WHERE a.application_date=%(report_date)s::date GROUP BY 1,2,3
), funding AS (
 SELECT a.channel,a.product_code,l.risk_grade,count(*) funded_count,sum(l.principal) funded_amount
 FROM staging.loan_latest l JOIN staging.application_latest a USING(application_id)
 WHERE l.disbursement_date=%(report_date)s::date GROUP BY 1,2,3
)
INSERT INTO mart.daily_credit_kpi
(report_date,channel,product_code,risk_grade,unique_applicants,applications,approvals,accepted,
 funded_count,requested_amount,funded_amount,approval_rate,acceptance_rate)
SELECT %(report_date)s::date,channel,product_code,risk_grade,
 coalesce(unique_applicants,0),coalesce(applications,0),coalesce(approvals,0),coalesce(accepted,0),
 coalesce(funded_count,0),coalesce(requested_amount,0),coalesce(funded_amount,0),
 approvals::numeric/nullif(applications,0),accepted::numeric/nullif(approvals,0)
FROM applications FULL JOIN funding USING(channel,product_code,risk_grade);
