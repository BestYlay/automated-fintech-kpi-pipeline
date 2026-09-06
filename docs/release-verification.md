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
- Post-correction verification script completed in 32.83 seconds (network/runtime dependent).
- All 19 tests passed, including PostgreSQL fixtures for delayed funding,
  last-touch attribution, whole-loan DPD and calendar-month vintage.
- Existing scheduler: StartWhenAvailable, IgnoreNew, three retries at 30 minutes.

| Mart | Rows | SHA-256 |
| --- | ---: | --- |
| daily_credit_kpi | 5584 | 3149b7d2915e16207dcc9e323a64e104619a566d95ab808488b7d5ff57b0da48 |
| daily_portfolio_kpi | 1239 | 098a5702eb742e7c7896ef35450bfa295dbb4bcfb91c9e605ee615bee70b53c1 |
| daily_campaign_kpi | 36084 | f4b58d6549ee304942797157dba1a16210138a8e26bc2e506412c67f32951bf7 |
| vintage_kpi | 1145 | b9bd47afa8f6c791472b538efdbb989cee33a0bc783587776c33932d62486189 |
| dq_summary | 2232 | 18bfd31e8d38e8eccc96b86be460f6966ad133f48903020e18613b099fabd12a |

## Business correction applied September 6

`scripts/repair_business_metrics.py --publish` backed up six raw tables, five marts
and run/quality audit tables in local schema `business_fix_20260906` before any
correction. It appended source versions for loans below HK$5,000 and their
dependent schedules/payments, added 804 deterministic residual cure payments
(HK$465,435.07), rebuilt 248 dates in two workers into shadow tables, and replaced
the local marts transactionally before publishing to Neon. Backups remain local;
restoration must be coordinated with any later daily runs, not blindly replayed.
Progress is retained in `logs/business_repair_progress.txt`.

Measured repair duration: 42.1 seconds including publication; mart phase 20.2
seconds. Workers used indexed temporary canonical snapshots to avoid repeatedly
evaluating raw-event window views. This is not a benchmark of the regular daily job.

- Cumulative disbursed loans: 4,665; principal HK$94,706,950.43, reconciled to staging.
- September 5: 44 applications, 18 approvals, 16 acceptances; 20 disbursements,
  HK$377,772.52. These are different daily flows, not a four-step single cohort.
- Canonical loan principal bounds: HK$5,000.00–144,393.35.
- Latest outstanding principal: HK$67,088,863.73; loan-level DPD30 balance:
  HK$8,190,389.38 (12.21%). This is a synthetic scenario, not a validated market rate.
- Original report snapshots and their old installment-level DPD ratios are superseded.

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
- DPD balances now represent full outstanding principal of delinquent loans.
  These are internal synthetic definitions, not regulatory reporting certification.
- Vintage uses calendar-month MOB and fixed origination cohorts; completed cells
  use month-end observations, while the current month remains provisional.
- Campaigns use canonical seven-day last-touch attribution, not the legacy source
  campaign_id. Recent touches have incomplete follow-up; attribution is not uplift.
- Partial-payment cure selection (70%, 15–60 days) is a synthetic assumption.
  Uncured/missed installments have no later recovery, and write-offs, restructuring
  and prepayments remain unmodeled. No claim of realistic portfolio calibration.
- Income configuration records a reference median and synthetic assumptions; this
  release does not claim a statistically validated fit to the full HK population.
- Forced worker failure, every correction/late-event edge case, physical network
  disconnection during COMMIT, and the full original acceptance matrix are not
  comprehensively tested. The controlled transaction rollback is narrower evidence.
- Public Cloud browser visual verification and Power BI artifacts are separate
  acceptance items. Do not claim those are completed from AppTest alone.
