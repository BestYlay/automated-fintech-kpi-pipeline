from datetime import date

from fintech_pipeline.simulation import simulate_day


def test_same_date_is_deterministic():
    state = {"customers": [], "open_installments": [], "paid_installment_ids": []}
    first = simulate_day(date(2026, 8, 19), state)
    second = simulate_day(date(2026, 8, 19), state)
    assert first.customers == second.customers
    assert first.applications == second.applications
    assert first.loans == second.loans


def test_customer_ids_are_stable_and_unique():
    batch = simulate_day(date(2026, 8, 19), {"customers": [], "open_installments": [], "paid_installment_ids": []})
    ids = [row["customer_id"] for row in batch.customers]
    assert len(ids) == len(set(ids))
    assert all(row["risk_grade"] in {"A", "B", "C", "D", "E"} for row in batch.customers)


def test_loans_have_valid_schedule_and_business_links():
    batch = simulate_day(date(2026, 8, 19), {"customers": [], "open_installments": [], "paid_installment_ids": []})
    applications = {row["application_id"]: row for row in batch.applications}
    loans = {row["loan_id"]: row for row in batch.loans}
    assert all(applications[row["application_id"]]["offer_accepted"] for row in loans.values())
    assert all(row["amount_due"] > 0 for row in batch.installments)

