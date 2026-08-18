from __future__ import annotations

import os

import pandas as pd
import plotly.express as px
import psycopg
import streamlit as st


st.set_page_config(page_title="Hong Kong FinTech KPI", page_icon="📊", layout="wide")
st.title("Hong Kong FinTech KPI Reporting")
st.caption("Synthetic unsecured-loan portfolio • PostgreSQL marts • report dates in Asia/Hong_Kong")


@st.cache_resource
def get_connection():
    url = os.environ.get("READ_ONLY_DATABASE_URL")
    try:
        url = url or st.secrets["READ_ONLY_DATABASE_URL"]
    except Exception:
        pass
    if not url:
        raise RuntimeError("Set READ_ONLY_DATABASE_URL in the environment or Streamlit Secrets")
    return psycopg.connect(url)


@st.cache_data(ttl=900)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    with get_connection().cursor() as cur:
        cur.execute(sql, params)
        columns = [column.name for column in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


try:
    credit = query("SELECT * FROM mart.daily_credit_kpi ORDER BY report_date")
    portfolio = query("SELECT * FROM mart.daily_portfolio_kpi ORDER BY report_date")
    campaign = query("SELECT * FROM mart.daily_campaign_kpi ORDER BY report_date")
    vintage = query("SELECT * FROM mart.vintage_kpi ORDER BY report_date")
    dq = query("SELECT * FROM mart.dq_summary ORDER BY report_date DESC, check_name")
except Exception as exc:
    st.error(f"Dashboard data is unavailable: {exc}")
    st.info("Configure READ_ONLY_DATABASE_URL and run the pipeline once.")
    st.stop()


if credit.empty and portfolio.empty:
    st.warning("No reporting data is available yet.")
    st.stop()

latest_candidates = [
    value
    for value in [
        credit.report_date.max() if not credit.empty else None,
        portfolio.report_date.max() if not portfolio.empty else None,
    ]
    if value is not None
]
latest_date = max(latest_candidates)
st.sidebar.metric("Latest report date", str(latest_date))
page = st.sidebar.radio("View", ["Overview", "Credit funnel", "Portfolio risk", "Campaigns", "Data quality"])

if page == "Overview":
    if not credit.empty:
        latest_credit = credit[credit.report_date == latest_date]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Applications", f"{latest_credit.applications.sum():,.0f}")
        c2.metric("Approval rate", f"{latest_credit.approvals.sum() / max(latest_credit.applications.sum(), 1):.1%}")
        c3.metric("Funded amount", f"HK${latest_credit.funded_amount.sum():,.0f}")
        c4.metric("Funded loans", f"{latest_credit.funded_count.sum():,.0f}")
        trend = credit.groupby("report_date", as_index=False).agg(
            applications=("applications", "sum"), funded_amount=("funded_amount", "sum")
        )
        st.plotly_chart(
            px.line(trend, x="report_date", y=["applications", "funded_amount"], title="Daily acquisition and funding"),
            use_container_width=True,
        )
    if not portfolio.empty:
        risk = portfolio[portfolio.report_date == latest_date]
        st.plotly_chart(
            px.bar(risk, x="risk_grade", y="outstanding_principal", title="Outstanding principal by risk grade"),
            use_container_width=True,
        )

elif page == "Credit funnel":
    if credit.empty:
        st.info("No credit data")
    else:
        date_filter = st.date_input("Report date", value=latest_date)
        filtered = credit[credit.report_date == date_filter]
        if filtered.empty:
            st.info("No data for this date")
        else:
            funnel = filtered.groupby("channel", as_index=False).agg(
                applications=("applications", "sum"), approvals=("approvals", "sum"),
                accepted=("accepted", "sum"), funded_count=("funded_count", "sum"),
                funded_amount=("funded_amount", "sum")
            )
            st.dataframe(funnel, use_container_width=True, hide_index=True)
            st.plotly_chart(
                px.bar(funnel, x="channel", y=["applications", "approvals", "accepted", "funded_count"], barmode="group"),
                use_container_width=True,
            )

elif page == "Portfolio risk":
    if portfolio.empty:
        st.info("No portfolio data")
    else:
        trend = portfolio.groupby("report_date", as_index=False).agg(
            outstanding_principal=("outstanding_principal", "sum"),
            dpd_30_balance=("dpd_30_balance", "sum"),
            amount_due=("amount_due", "sum"), amount_paid=("amount_paid", "sum")
        )
        st.plotly_chart(
            px.line(trend, x="report_date", y=["outstanding_principal", "dpd_30_balance"], title="Portfolio and DPD 30+ balance"),
            use_container_width=True,
        )
        if not vintage.empty:
            latest_vintage = vintage[vintage.report_date == latest_date]
            heat = latest_vintage.pivot_table(index="origination_month", columns="months_on_book", values="dpd_30_rate")
            st.plotly_chart(
                px.imshow(heat, aspect="auto", color_continuous_scale="Reds", title="DPD 30+ vintage heatmap"),
                use_container_width=True,
            )

elif page == "Campaigns":
    if campaign.empty:
        st.info("No campaign data")
    else:
        campaign = campaign[campaign.report_date == latest_date]
        summary = campaign.groupby(["campaign_id", "channel"], as_index=False).agg(
            touches=("touches", "sum"), opens=("opens", "sum"), clicks=("clicks", "sum"),
            applications=("applications", "sum"), funded_amount=("funded_amount", "sum")
        )
        summary["click_rate"] = summary.clicks / summary.touches.replace(0, pd.NA)
        summary["application_rate"] = summary.applications / summary.touches.replace(0, pd.NA)
        st.dataframe(summary, use_container_width=True, hide_index=True)
        st.plotly_chart(
            px.bar(summary, x="campaign_id", y="funded_amount", color="channel", title="Funded amount by campaign"),
            use_container_width=True,
        )

else:
    st.subheader("Pipeline data quality")
    if dq.empty:
        st.info("No quality results")
    else:
        st.dataframe(dq.head(100), use_container_width=True, hide_index=True)
        st.metric("Latest failed checks", int(((dq.report_date == latest_date) & (dq.status == "failed")).sum()))
