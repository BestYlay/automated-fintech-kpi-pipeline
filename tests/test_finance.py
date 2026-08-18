from datetime import date

from fintech_pipeline.finance import amortization_schedule


def test_amortization_principal_reconciles():
    schedule = amortization_schedule(100, 10000, 0.12, 12, date(2026, 1, 15))
    assert len(schedule) == 12
    assert abs(sum(row["scheduled_principal"] for row in schedule) - 10000) <= 0.02
    assert all(row["amount_due"] > 0 for row in schedule)


def test_final_installment_clears_balance():
    schedule = amortization_schedule(101, 12345.67, 0.083, 24, date(2026, 1, 31))
    assert schedule[-1]["scheduled_principal"] > 0
    assert abs(sum(row["scheduled_principal"] for row in schedule) - 12345.67) <= 0.02

