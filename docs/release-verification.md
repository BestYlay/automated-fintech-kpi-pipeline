# Release verification — 2026-09-06

Scope: local PostgreSQL pipeline, aggregate Neon publication and Streamlit.
Power BI remains deferred. Synthetic data only.

## Measured results

- Local history: 248 successful dates, 2026-01-01 through 2026-09-05.
- Historical mart hard-check failures: 0.
- Full history published twice; counts and order-independent SHA-256 checksums
  matched across repeated runs. Each publication read back all five remote tables
  inside the publishing transaction and reconciled them to the local snapshot.
- Forced exception after deleting one date in a remote transaction rolled back;
  all five table checksums remained unchanged.
- Reader: SELECT on all five marts, no INSERT/UPDATE/DELETE. Publisher: mart CRUD.
  Both lack schema USAGE on raw/staging/audit.
- Streamlit AppTest executed all five pages using the real Neon reader. Every page
  had no exception/error and displayed 2026-09-05. This is application execution
  testing, not visual inspection of the deployed Cloud instance.
- Verification script completed in 43.36 seconds (network/runtime dependent).
- Existing scheduler: StartWhenAvailable, IgnoreNew, three retries at 30 minutes.

| Mart | Rows | SHA-256 |
| --- | ---: | --- |
| daily_credit_kpi | 5374 | eb6ca46835ec5da1118a361345e1818725253227921428b3dad0d6b3d9b8643d |
| daily_portfolio_kpi | 1239 | d2aee5c641b25afe5bfca3ed326cd22896f836a45efe4e99d79e39bbd4f24b33 |
| daily_campaign_kpi | 36084 | 07d23f0f3eda10a83d976da64e386a6470cf505c2b56b3e1e59836e6923bd8f7 |
| vintage_kpi | 1999 | 0fba37a3f0cb41fc8e322d8fc6c08af6ac2dca57205e597ed7903bea6b1152fb |
| dq_summary | 1488 | d9882a9e218aea5dd1be7f7ee2903b72153b15f5e7e83ce9b1364f6193ba3bfb |

## Reproduction and incident recovery

Set PIPELINE_DATABASE_URL, MART_PUBLISH_DATABASE_URL and READ_ONLY_DATABASE_URL
securely, then run `python scripts/verify_release.py` from an installed checkout.
This script **writes** all local mart history to the configured publication target
twice and tests a rolled-back deletion; use only the dedicated project databases.
Progress is immediately flushed to `logs/release_verification.txt` with count,
percentage, elapsed time, remaining estimate and ETA. Raw logs remain local.

The September 4 and 5 nightly runs finished local computation but failed Neon DNS
resolution. Full history was republished on September 6 and reconciled successfully.
Connection establishment now has three bounded attempts, five seconds apart;
transaction errors still roll back and propagate to the scheduler.
For a prolonged outage, use publish-marts over the full available history after
network recovery. This also restores any older correction windows missed during
the outage without regenerating raw data.

## Metric contracts and remaining limits

- Credit applications/approvals/acceptances/funding are daily flows; recompute
  rates from summed numerators and denominators, never average row rates.
- Portfolio balances and campaign attribution tables are date snapshots. Do not
  sum snapshots across reporting dates. Amount due/paid in the portfolio table are
  cumulative through the reporting date, not daily collections flows.
- DPD balances represent unpaid principal of overdue installments, not the full
  outstanding balance of all loans with any delinquent installment. They must not
  be advertised as a regulatory loan-level portfolio delinquency ratio.
- Vintage age uses completed months since individual loan disbursement. The heatmap
  selects the latest available observation per origination-month/age cell. Cells
  may contain different loans and dates; it is an exploratory age-bucket view,
  not a fixed-cohort month-end vintage or default probability estimate.
- Income configuration records a reference median and synthetic assumptions; this
  release does not claim a statistically validated fit to the full HK population.
- Forced worker failure, every correction/late-event edge case, physical network
  disconnection during COMMIT, and the full original acceptance matrix are not
  comprehensively tested. The controlled transaction rollback is narrower evidence.
- Public Cloud browser visual verification and Power BI artifacts are separate
  acceptance items. Do not claim those are completed from AppTest alone.
