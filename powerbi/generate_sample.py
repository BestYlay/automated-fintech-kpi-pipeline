"""Create a small, deterministic aggregate snapshot for Power BI authoring.

The live pipeline publishes the same logical marts from PostgreSQL.  This file
only creates a compact, synthetic fallback so the PBIX can be authored before
the user's database URL is configured; it contains no customer-level data.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from fintech_pipeline.simulation import simulate_day


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "powerbi" / "sample_data" / "kpi_snapshot.csv"
START = date(2026, 8, 12)
END = date(2026, 8, 18)


FIELDS = [
    "report_date",
    "metric_family",
    "channel",
    "risk_grade",
    "campaign_id",
    "origination_month",
    "months_on_book",
    "applications",
    "approvals",
    "accepted",
    "funded_count",
    "requested_amount",
    "funded_amount",
    "active_loans",
    "outstanding_principal",
    "amount_due",
    "amount_paid",
    "collection_rate",
    "dpd_1_balance",
    "dpd_7_balance",
    "dpd_30_balance",
    "dpd_90_balance",
    "touches",
    "opens",
    "clicks",
    "dpd_30_rate",
]


def _zero_row(report_date: date, metric_family: str) -> dict[str, object]:
    row = {field: None for field in FIELDS}
    row["report_date"] = report_date.isoformat()
    row["metric_family"] = metric_family
    for field in FIELDS[6:]:
        if field not in {"collection_rate", "dpd_30_rate"}:
            row[field] = 0
    row["collection_rate"] = 0.0
    row["dpd_30_rate"] = 0.0
    return row


def build_rows() -> list[dict[str, object]]:
    state: dict[str, object] = {"customers": [], "open_installments": [], "paid_installment_ids": []}
    all_customers: dict[int, dict] = {}
    all_loans: dict[int, dict] = {}
    all_installments: dict[int, dict] = {}
    all_payments: dict[int, dict] = {}
    rows: list[dict[str, object]] = []

    current = START
    while current <= END:
        batch = simulate_day(current, state)
        for item in batch.customers:
            all_customers[item["customer_id"]] = item
        for item in batch.loans:
            all_loans[item["loan_id"]] = item
        for item in batch.installments:
            all_installments[item["installment_id"]] = item
        for item in batch.payments:
            all_payments[item["installment_id"]] = item

        state["customers"] = [
            {
                "customer_id": item["customer_id"],
                "monthly_income": item["monthly_income"],
                "existing_debt": item["existing_debt"],
                "risk_score": item["risk_score"],
                "risk_grade": item["risk_grade"],
                "acquisition_channel": item["acquisition_channel"],
            }
            for item in all_customers.values()
        ]
        state["open_installments"] = [
            {**item, "risk_grade": all_loans[item["loan_id"]]["risk_grade"]}
            for item in all_installments.values()
            if item["due_date"] <= current
            and item["installment_id"] not in all_payments
        ]
        state["paid_installment_ids"] = list(all_payments)

        customer_grade = {item["customer_id"]: item["risk_grade"] for item in all_customers.values()}
        credit: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for app in batch.applications:
            key = (app["channel"], customer_grade[app["customer_id"]])
            credit[key]["applications"] += 1
            credit[key]["approvals"] += app["decision"] == "approved"
            credit[key]["accepted"] += bool(app["offer_accepted"])
            credit[key]["requested_amount"] += float(app["requested_amount"])
        loans_by_app = {item["application_id"]: item for item in batch.loans}
        for app_id, loan in loans_by_app.items():
            for app in batch.applications:
                if app["application_id"] == app_id:
                    key = (app["channel"], customer_grade[app["customer_id"]])
                    credit[key]["funded_count"] += 1
                    credit[key]["funded_amount"] += float(loan["principal"])
                    break
        for (channel, grade), values in sorted(credit.items()):
            row = _zero_row(current, "credit")
            row.update(
                channel=channel,
                risk_grade=grade,
                applications=int(values["applications"]),
                approvals=int(values["approvals"]),
                accepted=int(values["accepted"]),
                funded_count=int(values["funded_count"]),
                requested_amount=round(values["requested_amount"], 2),
                funded_amount=round(values["funded_amount"], 2),
            )
            rows.append(row)

        portfolio: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for installment in all_installments.values():
            loan = all_loans[installment["loan_id"]]
            if loan["disbursement_date"] > current:
                continue
            grade = loan["risk_grade"]
            paid = all_payments.get(installment["installment_id"], {})
            amount_paid = float(paid.get("amount", 0.0))
            principal_paid = float(paid.get("principal_paid", 0.0))
            outstanding = max(float(installment["scheduled_principal"]) - principal_paid, 0.0)
            values = portfolio[grade]
            values["active_loans"] += outstanding > 0
            values["outstanding_principal"] += outstanding
            if installment["due_date"] <= current:
                values["amount_due"] += float(installment["amount_due"])
                values["amount_paid"] += min(amount_paid, float(installment["amount_due"]))
                dpd = max((current - installment["due_date"]).days, 0)
                if amount_paid < float(installment["amount_due"]):
                    values["dpd_1_balance"] += outstanding if dpd >= 1 else 0
                    values["dpd_7_balance"] += outstanding if dpd >= 7 else 0
                    values["dpd_30_balance"] += outstanding if dpd >= 30 else 0
                    values["dpd_90_balance"] += outstanding if dpd >= 90 else 0
        for grade, values in sorted(portfolio.items()):
            row = _zero_row(current, "portfolio")
            row.update(
                risk_grade=grade,
                active_loans=int(values["active_loans"]),
                outstanding_principal=round(values["outstanding_principal"], 2),
                amount_due=round(values["amount_due"], 2),
                amount_paid=round(values["amount_paid"], 2),
                collection_rate=round(values["amount_paid"] / values["amount_due"], 4)
                if values["amount_due"]
                else 0.0,
                dpd_1_balance=round(values["dpd_1_balance"], 2),
                dpd_7_balance=round(values["dpd_7_balance"], 2),
                dpd_30_balance=round(values["dpd_30_balance"], 2),
                dpd_90_balance=round(values["dpd_90_balance"], 2),
            )
            rows.append(row)

        campaign: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for touch in batch.marketing_touches:
            key = (touch["campaign_id"], touch["channel"])
            campaign[key]["touches"] += 1
            campaign[key]["opens"] += bool(touch["opened"])
            campaign[key]["clicks"] += bool(touch["clicked"])
        for app in batch.applications:
            if app["campaign_id"]:
                key = (app["campaign_id"], app["channel"])
                campaign[key]["applications"] += 1
                if app["application_id"] in loans_by_app:
                    campaign[key]["funded_count"] += 1
                    campaign[key]["funded_amount"] += float(loans_by_app[app["application_id"]]["principal"])
        for (campaign_id, channel), values in sorted(campaign.items()):
            row = _zero_row(current, "campaign")
            row.update(
                campaign_id=campaign_id,
                channel=channel,
                touches=int(values["touches"]),
                opens=int(values["opens"]),
                clicks=int(values["clicks"]),
                applications=int(values["applications"]),
                funded_count=int(values["funded_count"]),
                funded_amount=round(values["funded_amount"], 2),
            )
            rows.append(row)

        vintage: dict[tuple[str, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for installment in all_installments.values():
            loan = all_loans[installment["loan_id"]]
            if loan["disbursement_date"] > current:
                continue
            month = loan["disbursement_date"].replace(day=1).isoformat()
            mob = (current.year - loan["disbursement_date"].year) * 12 + current.month - loan["disbursement_date"].month
            paid = all_payments.get(installment["installment_id"], {})
            outstanding = max(float(installment["scheduled_principal"]) - float(paid.get("principal_paid", 0.0)), 0.0)
            vintage[(month, mob)]["loans"] += 1
            vintage[(month, mob)]["outstanding"] += outstanding
            if max((current - installment["due_date"]).days, 0) >= 30 and not paid:
                vintage[(month, mob)]["dpd30"] += outstanding
        for (month, mob), values in sorted(vintage.items()):
            row = _zero_row(current, "vintage")
            row.update(
                origination_month=month,
                months_on_book=mob,
                active_loans=int(values["loans"]),
                outstanding_principal=round(values["outstanding"], 2),
                dpd_30_balance=round(values["dpd30"], 2),
                dpd_30_rate=round(values["dpd30"] / values["outstanding"], 4)
                if values["outstanding"]
                else 0.0,
            )
            rows.append(row)

        current += timedelta(days=1)
    return rows


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    rows = build_rows()
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} aggregate rows to {OUTPUT}")


if __name__ == "__main__":
    main()
