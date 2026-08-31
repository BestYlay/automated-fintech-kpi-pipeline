# Power BI deliverable

`FinTech_KPI_Dashboard.pbix` is authored with Power BI Desktop in import mode.
The report uses the same logical fields as the PostgreSQL marts.  A compact,
deterministic aggregate snapshot in `sample_data/kpi_snapshot.csv` is included
only so the report can be authored and reviewed before a database URL is
configured; it contains no customer-level or sensitive data.

The production refresh should replace the sample query with PostgreSQL queries
against the Neon aggregate mart tables listed below. The local scheduler keeps
raw generation local and publishes only these five tables. Set the
server/database parameters locally and enter credentials through Power BI's
data-source settings; no connection string or password belongs in Git.

The model should expose:

- `mart.daily_credit_kpi`
- `mart.daily_portfolio_kpi`
- `mart.daily_campaign_kpi`
- `mart.vintage_kpi`
- `mart.dq_summary`

Refresh order: connect, refresh, verify the latest `report_date`, then export
the five pages to `powerbi/screenshots/` and PDF. Streamlit is the live public
dashboard; Power BI is a downloadable analyst-facing artifact.

To regenerate the authoring snapshot:

```powershell
$env:PYTHONPATH = "src"
python powerbi/generate_sample.py
```

After the PostgreSQL database is provisioned, use **Transform data → Data
source settings** to point the five mart queries at the read-only account and
confirm that the latest successful `report_date` is displayed on page 1.
