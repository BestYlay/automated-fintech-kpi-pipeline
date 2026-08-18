from __future__ import annotations

from datetime import date


def amortization_schedule(
    loan_id: int,
    principal: float,
    annual_rate: float,
    term_months: int,
    disbursement_date: date,
) -> list[dict]:
    """Return a rounded monthly amortisation schedule with a corrected final row."""
    monthly_rate = annual_rate / 12
    if monthly_rate == 0:
        payment = principal / term_months
    else:
        payment = principal * monthly_rate / (1 - (1 + monthly_rate) ** (-term_months))

    balance = round(principal, 2)
    schedule: list[dict] = []
    for installment_no in range(1, term_months + 1):
        interest = round(balance * monthly_rate, 2)
        principal_part = round(payment - interest, 2)
        if installment_no == term_months:
            principal_part = balance
        amount_due = round(principal_part + interest, 2)
        balance = round(balance - principal_part, 2)
        due_month = disbursement_date.month - 1 + installment_no
        year = disbursement_date.year + due_month // 12
        month = due_month % 12 + 1
        day = min(disbursement_date.day, 28)
        schedule.append(
            {
                "installment_id": loan_id * 100 + installment_no,
                "loan_id": loan_id,
                "installment_no": installment_no,
                "due_date": date(year, month, day),
                "scheduled_principal": principal_part,
                "scheduled_interest": interest,
                "amount_due": amount_due,
            }
        )
    return schedule

