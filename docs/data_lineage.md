# Data lineage

```text
simulator
  -> raw.*_events (append-only, event version, ingestion metadata)
  -> staging.*_latest (latest business-key version using ROW_NUMBER)
  -> audit.quality_results / audit.rejected_events
  -> mart.daily_credit_kpi
  -> mart.daily_portfolio_kpi
  -> mart.daily_campaign_kpi
  -> mart.vintage_kpi
  -> Streamlit / Power BI
```

The pipeline consumes event time for business metrics and ingestion time for
watermarking. Late payments therefore update the affected historical cohort.

