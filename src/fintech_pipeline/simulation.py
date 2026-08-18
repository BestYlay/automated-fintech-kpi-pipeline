from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np

from .finance import amortization_schedule


CHANNELS = ("organic", "paid_search", "referral", "partner", "sms")
REGIONS = ("Hong_Kong_Island", "Kowloon", "New_Territories")
PRODUCT = "personal_installment"
RISK_GRADES = ("A", "B", "C", "D", "E")


def _stable_int(*parts: object) -> int:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _rng(report_date: date, *parts: object) -> np.random.Generator:
    return np.random.default_rng(_stable_int(report_date, *parts) % (2**63 - 1))


def _utc_ingested(report_date: date, lag_hours: int = 0) -> datetime:
    return datetime.combine(report_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=lag_hours)


def _risk_grade(score: int) -> str:
    return "A" if score >= 760 else "B" if score >= 700 else "C" if score >= 640 else "D" if score >= 580 else "E"


def _monthly_income(rng: np.random.Generator) -> float:
    value = float(np.exp(rng.normal(np.log(21000), 0.55)))
    return round(float(np.clip(value, 6000, 120000)), 2)


def _event(table: str, business_id: int, report_date: date, values: dict, version: int = 1, lag_hours: int = 0) -> dict:
    return {
        "source_event_id": f"{table}:{business_id}:v{version}",
        "event_version": version,
        "event_time": datetime.combine(report_date, datetime.min.time(), tzinfo=timezone.utc),
        "ingested_at": _utc_ingested(report_date, lag_hours),
        "batch_id": f"batch:{report_date.isoformat()}",
        **values,
    }


@dataclass
class Batch:
    customers: list[dict] = field(default_factory=list)
    marketing_touches: list[dict] = field(default_factory=list)
    applications: list[dict] = field(default_factory=list)
    loans: list[dict] = field(default_factory=list)
    installments: list[dict] = field(default_factory=list)
    payments: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    @property
    def total_events(self) -> int:
        return sum(len(getattr(self, name)) for name in ("customers", "marketing_touches", "applications", "loans", "installments", "payments"))


def _payment_plan(installment: dict) -> tuple[date | None, float]:
    due = installment["due_date"]
    grade = installment.get("risk_grade", "C")
    on_time = {"A": 0.97, "B": 0.94, "C": 0.88, "D": 0.78, "E": 0.62}[grade]
    u = (_stable_int(installment["installment_id"], "payment") % 10_000) / 10_000
    if u < on_time:
        return due, 1.0
    if u < on_time + 0.22:
        delay = 1 + _stable_int(installment["installment_id"], "delay") % 30
        fraction = 0.50 + (_stable_int(installment["installment_id"], "fraction") % 41) / 100
        return due + timedelta(days=delay), fraction
    return None, 0.0


def simulate_day(report_date: date, state: dict[str, Any]) -> Batch:
    """Generate one deterministic batch from the current database state.

    The state is intentionally plain dictionaries so the generator can be unit-tested
    without a database. The pipeline populates it from canonical staging views.
    """
    batch = Batch()
    rng = _rng(report_date, "daily")
    ordinal = (report_date - date(2025, 1, 1)).days
    weekday_factor = 0.72 if report_date.weekday() >= 5 else 1.0
    month_end_factor = 1.18 if report_date.day >= 25 else 1.0
    new_count = int(rng.poisson(120 * weekday_factor * month_end_factor))
    existing_customers: list[dict] = state.get("customers", [])

    customer_rows: list[dict] = []
    touched_customers: set[int] = set()
    for index in range(new_count):
        customer_id = (ordinal + 1) * 1_000_000 + index + 1
        income = _monthly_income(rng)
        existing_debt = round(float(income * rng.uniform(0.05, 1.35)), 2)
        tenure = int(rng.integers(6, 181))
        score = int(np.clip(590 + 0.0035 * income - 0.0005 * existing_debt + rng.normal(0, 55), 350, 850))
        grade = _risk_grade(score)
        channel = str(rng.choice(CHANNELS, p=[0.30, 0.24, 0.18, 0.16, 0.12]))
        region = str(rng.choice(REGIONS, p=[0.22, 0.30, 0.48]))
        row = _event(
            "customer",
            customer_id,
            report_date,
            {
                "customer_id": customer_id,
                "signup_date": report_date,
                "region": region,
                "acquisition_channel": channel,
                "monthly_income": round(income, 2),
                "existing_debt": existing_debt,
                "employment_tenure_months": tenure,
                "risk_score": score,
                "risk_grade": grade,
            },
        )
        customer_rows.append(row)
        batch.customers.append(row)
        if rng.random() < 0.42:
            touch_id = customer_id * 10 + 1
            campaign_id = f"CAMP-{report_date.strftime('%Y%m')}-{channel.upper()}"
            batch.marketing_touches.append(
                _event(
                    "marketing_touch",
                    touch_id,
                    report_date,
                    {
                        "touch_id": touch_id,
                        "customer_id": customer_id,
                        "campaign_id": campaign_id,
                        "channel": channel,
                        "touch_date": report_date,
                        "opened": bool(rng.random() < 0.34),
                        "clicked": bool(rng.random() < 0.13),
                    },
                )
            )
            touched_customers.add(customer_id)

    customers = existing_customers + [
        {
            "customer_id": row["customer_id"],
            "monthly_income": row["monthly_income"],
            "existing_debt": row["existing_debt"],
            "risk_score": row["risk_score"],
            "risk_grade": row["risk_grade"],
            "acquisition_channel": row["acquisition_channel"],
        }
        for row in customer_rows
    ]
    if customers:
        customer_count = min(len(customers), max(0, int(rng.poisson(len(customer_rows) * 0.55 + len(customers) * 0.0006))))
        chosen = rng.choice(len(customers), size=customer_count, replace=False)
    else:
        chosen = []

    loan_rows: list[dict] = []
    for index, selected in enumerate(np.atleast_1d(chosen)):
        customer = customers[int(selected)]
        application_id = (ordinal + 1) * 10_000_000 + index + 1
        requested = round(float(np.clip(rng.lognormal(np.log(18000), 0.65), 5000, 150000)), 2)
        income = float(customer["monthly_income"])
        score = float(customer["risk_score"])
        dti = (float(customer["existing_debt"]) + requested / 12) / max(income, 1)
        logit = -0.4 + (score - 650) / 220 - 1.5 * max(dti - 0.55, 0)
        approval_probability = 1 / (1 + math.exp(-logit))
        decision = "approved" if rng.random() < approval_probability else "rejected"
        accepted = decision == "approved" and rng.random() < 0.93
        channel = customer.get("acquisition_channel", "organic")
        campaign_id = (
            f"CAMP-{report_date.strftime('%Y%m')}-{str(channel).upper()}"
            if customer["customer_id"] in touched_customers
            else None
        )
        application = _event(
            "application",
            application_id,
            report_date,
            {
                "application_id": application_id,
                "customer_id": customer["customer_id"],
                "application_date": report_date,
                "product_code": PRODUCT,
                "channel": channel,
                "campaign_id": campaign_id,
                "requested_amount": requested,
                "decision": decision,
                "decision_date": report_date,
                "offer_accepted": accepted,
                "accepted_date": report_date if accepted else None,
            },
        )
        batch.applications.append(application)
        if not accepted:
            continue
        if rng.random() >= 0.90:
            continue
        loan_id = application_id * 10 + 1
        principal = round(requested * rng.uniform(0.85, 1.0), 2)
        term = int(rng.choice([6, 12, 24, 36], p=[0.28, 0.42, 0.24, 0.06]))
        annual_rate = round(float(np.clip(0.08 + (850 - score) / 3000 + rng.normal(0, 0.01), 0.06, 0.30)), 4)
        disbursement = report_date + timedelta(days=int(rng.integers(0, 3)))
        loan = _event(
            "loan",
            loan_id,
            report_date,
            {
                "loan_id": loan_id,
                "application_id": application_id,
                "customer_id": customer["customer_id"],
                "disbursement_date": disbursement,
                "principal": principal,
                "term_months": term,
                "annual_rate": annual_rate,
                "risk_grade": customer["risk_grade"],
                "status": "active",
            },
        )
        batch.loans.append(loan)
        schedule = amortization_schedule(loan_id, principal, annual_rate, term, disbursement)
        for installment in schedule:
            batch.installments.append(_event("installment", installment["installment_id"], report_date, installment))
        loan_rows.append({**loan, **customer})

    open_installments = state.get("open_installments", []) + [
        {**row, "risk_grade": loan["risk_grade"]}
        for row in batch.installments
        for loan in loan_rows
        if row["loan_id"] == loan["loan_id"]
    ]
    existing_payment_installments = set(state.get("paid_installment_ids", []))
    for installment in open_installments:
        if installment["due_date"] > report_date or installment["installment_id"] in existing_payment_installments:
            continue
        payment_date, fraction = _payment_plan(installment)
        if payment_date != report_date:
            continue
        amount = round(float(installment["amount_due"]) * fraction, 2)
        principal_paid = round(amount * float(installment["scheduled_principal"]) / float(installment["amount_due"]), 2)
        interest_paid = round(amount - principal_paid, 2)
        payment_id = installment["installment_id"] * 10 + 1
        batch.payments.append(
            _event(
                "payment",
                payment_id,
                report_date,
                {
                    "payment_id": payment_id,
                    "loan_id": installment["loan_id"],
                    "installment_id": installment["installment_id"],
                    "payment_date": report_date,
                    "amount": amount,
                    "principal_paid": principal_paid,
                    "interest_paid": interest_paid,
                    "payment_status": "paid" if fraction == 1 else "partial",
                },
            )
        )

    if batch.applications and rng.random() < 0.35:
        batch.applications.append(dict(batch.applications[0]))
    if batch.applications and rng.random() < 0.08:
        bad = dict(batch.applications[-1])
        bad["source_event_id"] = f"invalid:{report_date.isoformat()}:application"
        bad["requested_amount"] = -1.0
        batch.rejected.append({"source_event_id": bad["source_event_id"], "reason": "negative requested amount", "payload": bad})
        batch.applications.pop()
    return batch
