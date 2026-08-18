# Power BI deliverable

`FinTech_KPI_Dashboard.pbix` is created with Power BI Desktop from the
read-only PostgreSQL marts. Use import mode and parameters for server and
database. Credentials are entered locally and are not stored in Git.

The model should expose:

- `mart.daily_credit_kpi`
- `mart.daily_portfolio_kpi`
- `mart.daily_campaign_kpi`
- `mart.vintage_kpi`
- `mart.dq_summary`

Refresh order: connect, refresh, verify the latest `report_date`, then export
the five pages to `powerbi/screenshots/` and PDF. Streamlit is the live public
dashboard; Power BI is a downloadable analyst-facing artifact.

