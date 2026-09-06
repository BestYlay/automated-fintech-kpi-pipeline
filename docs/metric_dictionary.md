# Metric dictionary

| Metric | Definition |
|---|---|
| Approval rate | Approved applications divided by all decided applications in the cohort. |
| Acceptance rate | Accepted offers divided by approved applications. |
| Daily funding | Loans and principal disbursed on the reporting date, independent of application date. Do not divide this by same-day acceptances as a cohort conversion rate. |
| Collection rate | Capped paid amount divided by amount due as of the report date. |
| DPD 1+/7+/30+/90+ | Full outstanding principal of loans whose oldest unpaid installment is at least the threshold days overdue. Nested thresholds, not disjoint buckets. |
| Outstanding principal | Scheduled principal less cumulative principal paid, floored at zero. |
| Vintage | Fixed origination-month cohorts; MOB is the calendar-month difference. Heatmap uses month-end snapshots for closed cells and a provisional latest snapshot for the current month. DPD30 rate is delinquent loan balance / outstanding cohort balance, not default probability. |
| Campaign application | Each application is assigned to the customer's latest touch with touch date between application date minus 7 days and application date inclusive; ties use descending touch ID. The report includes touches in the inclusive 31-day window ending on the report date. Recent touches have incomplete follow-up. |

All dates are business dates in `Asia/Hong_Kong`; event timestamps are stored in
UTC. Synthetic outcome parameters are assumptions, not production credit-policy
recommendations.

Campaign attribution is recomputed from canonical touch/application dates; the
legacy source application's campaign_id is not authoritative. Attribution is not
causal uplift. Multiple applications can follow a touch, so applications/touches
is an activity ratio rather than a unique-customer conversion probability.

Partial repayment cure is a synthetic assumption: a deterministic 70% selection
settles the residual 15–60 days after the first payment. Unselected partial payers
and missed payments have no further recovery in this simplified model. There are
no write-offs, restructurings or prepayments. Risk levels are not market-calibrated.
