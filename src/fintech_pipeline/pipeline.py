from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg

from .db import connection, execute_sql_file, fetch_rows, insert_events
from .simulation import Batch, simulate_day


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "sql" / "001_init.sql"
MART_SQL = ROOT / "sql" / "marts"
LOCK_KEY = 517290104


class PipelineFailure(RuntimeError):
    pass


def init_db() -> None:
    with connection() as conn:
        execute_sql_file(conn, MIGRATION)
        conn.commit()


def _state(conn: psycopg.Connection, report_date: date) -> dict[str, Any]:
    customers = fetch_rows(
        conn,
        """
        SELECT customer_id, monthly_income::float8 AS monthly_income,
               existing_debt::float8 AS existing_debt, risk_score, risk_grade,
               acquisition_channel
        FROM staging.customer_latest
        ORDER BY customer_id
        """,
    )
    open_installments = fetch_rows(
        conn,
        """
        SELECT i.installment_id, i.loan_id, i.installment_no, i.due_date,
               i.scheduled_principal::float8 AS scheduled_principal,
               i.scheduled_interest::float8 AS scheduled_interest,
               i.amount_due::float8 AS amount_due, l.risk_grade
        FROM staging.installment_latest i
        JOIN staging.loan_latest l ON l.loan_id = i.loan_id
        LEFT JOIN (
            SELECT installment_id, SUM(amount)::float8 AS amount_paid
            FROM staging.payment_latest
            GROUP BY installment_id
        ) p ON p.installment_id = i.installment_id
        WHERE i.due_date <= %(report_date)s::date
          AND COALESCE(p.amount_paid, 0) < i.amount_due
        """,
        {"report_date": report_date},
    )
    paid = fetch_rows(
        conn,
        "SELECT DISTINCT installment_id FROM staging.payment_latest",
    )
    return {
        "customers": customers,
        "open_installments": open_installments,
        "paid_installment_ids": [row["installment_id"] for row in paid],
    }


def _write_rejections(conn: psycopg.Connection, report_date: date, batch: Batch) -> None:
    if not batch.rejected:
        return
    rows = [
        (
            report_date,
            item["source_event_id"],
            item["reason"],
            json.dumps(item.get("payload", {}), default=str),
        )
        for item in batch.rejected
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO audit.rejected_events(run_date, source_event_id, reason, payload)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            rows,
        )


def _quality_checks(conn: psycopg.Connection, report_date: date) -> list[dict]:
    checks: list[dict] = []

    def check(name: str, severity: str, query: str, params: tuple | dict = (), details: str = "") -> None:
        rows = fetch_rows(conn, query, params)
        value = rows[0]["value"] if rows else 0
        failed = bool(value and float(value) > 0)
        checks.append(
            {
                "check_name": name,
                "severity": severity,
                "status": "failed" if failed else "passed",
                "observed_value": value,
                "details": details,
            }
        )

    check(
        "orphan_applications",
        "hard",
        """
        SELECT COUNT(*)::numeric AS value
        FROM staging.application_latest a
        LEFT JOIN staging.customer_latest c ON c.customer_id = a.customer_id
        WHERE c.customer_id IS NULL
        """,
        details="Every application must reference a canonical customer.",
    )
    check(
        "orphan_loans",
        "hard",
        """
        SELECT COUNT(*)::numeric AS value
        FROM staging.loan_latest l
        LEFT JOIN staging.application_latest a ON a.application_id = l.application_id
        WHERE a.application_id IS NULL OR a.decision <> 'approved' OR NOT a.offer_accepted
        """,
        details="Every loan must be funded from an accepted approved application.",
    )
    check(
        "orphan_payments",
        "hard",
        """
        SELECT COUNT(*)::numeric AS value
        FROM staging.payment_latest p
        LEFT JOIN staging.installment_latest i ON i.installment_id = p.installment_id
        WHERE i.installment_id IS NULL
        """,
        details="Every payment must reference an installment.",
    )
    check(
        "negative_amounts",
        "hard",
        """
        SELECT (
            (SELECT COUNT(*) FROM staging.application_latest WHERE requested_amount < 0)
            + (SELECT COUNT(*) FROM staging.payment_latest
               WHERE amount < 0 OR principal_paid < 0 OR interest_paid < 0)
        )::numeric AS value
        """,
        details="Monetary amounts must be non-negative.",
    )
    check(
        "schedule_reconciliation",
        "hard",
        """
        SELECT COUNT(*)::numeric AS value
        FROM (
            SELECT l.loan_id,
                   ABS(l.principal - SUM(i.scheduled_principal)) AS difference
            FROM staging.loan_latest l
            JOIN staging.installment_latest i ON i.loan_id = l.loan_id
            GROUP BY l.loan_id, l.principal
            HAVING ABS(l.principal - SUM(i.scheduled_principal)) > 0.02
        ) x
        """,
        details="Installment principal must reconcile to the disbursed principal.",
    )
    check(
        "daily_application_volume_spike",
        "warning",
        """
        WITH daily AS (
            SELECT application_date, COUNT(*)::numeric AS n
            FROM staging.application_latest
            GROUP BY application_date
        ), baseline AS (
            SELECT AVG(n) AS average_n FROM daily
            WHERE application_date BETWEEN %(report_date)s::date - 7 AND %(report_date)s::date - 1
        )
        SELECT CASE WHEN COALESCE(b.average_n, 0) > 0 AND d.n > b.average_n * 5 THEN 1 ELSE 0 END::numeric AS value
        FROM daily d CROSS JOIN baseline b
        WHERE d.application_date = %(report_date)s::date
        """,
        {"report_date": report_date},
        details="A large volume spike is recorded as a warning, not a hard failure.",
    )
    return checks


def _record_quality(conn: psycopg.Connection, report_date: date, checks: list[dict]) -> None:
    with conn.cursor() as cur:
        for item in checks:
            cur.execute(
                """
                INSERT INTO audit.quality_results
                    (run_date, check_name, severity, status, observed_value, details)
                VALUES (%(run_date)s, %(check_name)s, %(severity)s, %(status)s, %(observed_value)s, %(details)s)
                """,
                {"run_date": report_date, **item},
            )
            cur.execute(
                """
                INSERT INTO mart.dq_summary
                    (report_date, check_name, severity, status, observed_value, details)
                VALUES (%(run_date)s, %(check_name)s, %(severity)s, %(status)s, %(observed_value)s, %(details)s)
                ON CONFLICT (report_date, check_name) DO UPDATE SET
                    severity = EXCLUDED.severity,
                    status = EXCLUDED.status,
                    observed_value = EXCLUDED.observed_value,
                    details = EXCLUDED.details
                """,
                {"run_date": report_date, **item},
            )


def _refresh_marts(conn: psycopg.Connection, report_date: date, lookback_days: int = 45) -> None:
    affected_dates = [report_date - timedelta(days=offset) for offset in range(lookback_days + 1)]
    with conn.cursor() as cur:
        for affected_date in affected_dates:
            cur.execute("DELETE FROM mart.daily_credit_kpi WHERE report_date = %s", (affected_date,))
            cur.execute("DELETE FROM mart.daily_portfolio_kpi WHERE report_date = %s", (affected_date,))
            cur.execute("DELETE FROM mart.daily_campaign_kpi WHERE report_date = %s", (affected_date,))
            cur.execute("DELETE FROM mart.vintage_kpi WHERE report_date = %s", (affected_date,))
    for affected_date in affected_dates:
        for name in ("daily_credit_kpi", "daily_portfolio_kpi", "daily_campaign_kpi", "vintage_kpi"):
            query = (MART_SQL / f"{name}.sql").read_text(encoding="utf-8")
            conn.execute(query, {"report_date": affected_date})


def _mark_run(conn: psycopg.Connection, report_date: date, status: str, batch_id: str | None, rows: int, rejected: int, error: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO audit.pipeline_runs
            (run_date, status, batch_id, started_at, finished_at, rows_loaded, rows_rejected, error_message)
        VALUES (%(run_date)s, %(status)s, %(batch_id)s, COALESCE((SELECT started_at FROM audit.pipeline_runs WHERE run_date = %(run_date)s), NOW()), NOW(), %(rows)s, %(rejected)s, %(error)s)
        ON CONFLICT (run_date) DO UPDATE SET
            status = EXCLUDED.status,
            batch_id = EXCLUDED.batch_id,
            finished_at = EXCLUDED.finished_at,
            rows_loaded = EXCLUDED.rows_loaded,
            rows_rejected = EXCLUDED.rows_rejected,
            error_message = EXCLUDED.error_message
        """,
        {
            "run_date": report_date,
            "status": status,
            "batch_id": batch_id,
            "rows": rows,
            "rejected": rejected,
            "error": error,
        },
    )


def run_day(report_date: date, lookback_days: int = 45) -> dict:
    started = time.perf_counter()
    batch: Batch | None = None
    try:
        with connection() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                conn.execute(
                    """
                    INSERT INTO audit.pipeline_runs(run_date, status, batch_id, started_at)
                    VALUES (%s, 'running', %s, NOW())
                    ON CONFLICT (run_date) DO UPDATE SET
                        status = 'running', batch_id = EXCLUDED.batch_id,
                        started_at = NOW(), finished_at = NULL,
                        rows_loaded = 0, rows_rejected = 0, error_message = NULL
                    """,
                    (report_date, f"batch:{report_date.isoformat()}"),
                )
                state = _state(conn, report_date)
                batch = simulate_day(report_date, state)
                loaded = 0
                loaded += insert_events(conn, "customer_events", batch.customers)
                loaded += insert_events(conn, "marketing_touch_events", batch.marketing_touches)
                loaded += insert_events(conn, "application_events", batch.applications)
                loaded += insert_events(conn, "loan_events", batch.loans)
                loaded += insert_events(conn, "installment_events", batch.installments)
                loaded += insert_events(conn, "payment_events", batch.payments)
                _write_rejections(conn, report_date, batch)
                checks = _quality_checks(conn, report_date)
                _record_quality(conn, report_date, checks)
                hard_failures = [item for item in checks if item["severity"] == "hard" and item["status"] == "failed"]
                if hard_failures:
                    _mark_run(
                        conn,
                        report_date,
                        "failed_quality",
                        f"batch:{report_date.isoformat()}",
                        loaded,
                        len(batch.rejected),
                        "; ".join(item["check_name"] for item in hard_failures),
                    )
                    return {
                        "status": "failed_quality",
                        "report_date": report_date.isoformat(),
                        "loaded": loaded,
                        "rejected": len(batch.rejected),
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                _refresh_marts(conn, report_date, lookback_days)
                _mark_run(conn, report_date, "success", f"batch:{report_date.isoformat()}", loaded, len(batch.rejected))
                return {
                    "status": "success",
                    "report_date": report_date.isoformat(),
                    "loaded": loaded,
                    "rejected": len(batch.rejected),
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        try:
            with connection() as failed_conn:
                _mark_run(failed_conn, report_date, "failed", f"batch:{report_date.isoformat()}", 0, len(batch.rejected) if batch else 0, message)
                failed_conn.commit()
        except Exception:
            pass
        raise PipelineFailure(message) from exc


def validate_day(report_date: date) -> list[dict]:
    with connection() as conn:
        return _quality_checks(conn, report_date)


def export_day(report_date: date) -> dict[str, int]:
    export_dir = ROOT / "exports"
    with connection() as conn:
        from .db import export_query

        return {
            "credit": export_query(
                conn,
                "SELECT * FROM mart.daily_credit_kpi WHERE report_date = %s ORDER BY channel, risk_grade",
                export_dir / f"daily_credit_kpi_{report_date.isoformat()}.csv",
                (report_date,),
            ),
            "portfolio": export_query(
                conn,
                "SELECT * FROM mart.daily_portfolio_kpi WHERE report_date = %s ORDER BY risk_grade",
                export_dir / f"daily_portfolio_kpi_{report_date.isoformat()}.csv",
                (report_date,),
            ),
            "campaign": export_query(
                conn,
                "SELECT * FROM mart.daily_campaign_kpi WHERE report_date = %s ORDER BY campaign_id",
                export_dir / f"daily_campaign_kpi_{report_date.isoformat()}.csv",
                (report_date,),
            ),
            "vintage": export_query(
                conn,
                "SELECT * FROM mart.vintage_kpi WHERE report_date = %s ORDER BY origination_month, months_on_book",
                export_dir / f"vintage_kpi_{report_date.isoformat()}.csv",
                (report_date,),
            ),
        }


def bootstrap(start: date, end: date) -> None:
    if end < start:
        raise ValueError("bootstrap end must not precede start")
    total = (end - start).days + 1
    log_path = ROOT / "logs" / "bootstrap_progress.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        for completed, offset in enumerate(range(total), start=1):
            report_date = start.fromordinal(start.toordinal() + offset)
            result = run_day(report_date, lookback_days=0)
            if result["status"] != "success":
                raise PipelineFailure(f"bootstrap stopped on {report_date}: {result}")
            if completed % 20 == 0 or completed == total:
                elapsed = time.perf_counter() - started
                rate = completed / elapsed if elapsed else 0
                remaining = (total - completed) / rate if rate else 0
                eta = datetime.now(timezone.utc).timestamp() + remaining
                eta_text = datetime.fromtimestamp(eta, timezone.utc).isoformat()
                line = (
                    f"completed={completed}/{total} percent={completed / total * 100:.1f}% "
                    f"elapsed_seconds={elapsed:.1f} estimated_remaining_seconds={remaining:.1f} "
                    f"ETA_UTC={eta_text} last_status={result['status']}\n"
                )
                log.write(line)
                log.flush()
        with connection() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                for completed, offset in enumerate(range(total), start=1):
                    report_date = start.fromordinal(start.toordinal() + offset)
                    _refresh_marts(conn, report_date, lookback_days=0)
                    if completed % 20 == 0 or completed == total:
                        elapsed = time.perf_counter() - started
                        rate = completed / elapsed if elapsed else 0
                        remaining = (total - completed) / rate if rate else 0
                        eta = datetime.now(timezone.utc).timestamp() + remaining
                        line = (
                            f"phase=mart_rebuild completed={completed}/{total} percent={completed / total * 100:.1f}% "
                            f"elapsed_seconds={elapsed:.1f} estimated_remaining_seconds={remaining:.1f} "
                            f"ETA_UTC={datetime.fromtimestamp(eta, timezone.utc).isoformat()}\n"
                        )
                        log.write(line)
                        log.flush()
