from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg

from .db import connection, execute_sql_file, fetch_rows, insert_events
from .publisher import publish_marts
from .simulation import Batch, simulate_day


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "sql" / "001_init.sql"
MART_SQL = ROOT / "sql" / "marts"
LOCK_KEY = 517290104
BOOTSTRAP_LOCK_KEY = 517290105


class PipelineFailure(RuntimeError):
    pass


class QualityGateFailure(PipelineFailure):
    """A hard data-quality failure that must roll back the batch transaction."""

    def __init__(self, report_date: date, loaded: int, rejected: int, checks: list[dict]) -> None:
        self.report_date = report_date
        self.loaded = loaded
        self.rejected = rejected
        self.checks = checks
        failed = [
            f"{item['check_name']}={item['observed_value']}"
            for item in checks
            if item["severity"] == "hard" and item["status"] == "failed"
        ]
        super().__init__(f"hard quality checks failed on {report_date}: {', '.join(failed)}")


RAW_EVENT_TABLES = (
    "customer_events",
    "marketing_touch_events",
    "application_events",
    "loan_events",
    "installment_events",
    "payment_events",
)


def init_db() -> None:
    with connection() as conn:
        for migration in sorted((ROOT / "sql").glob("*.sql")):
            execute_sql_file(conn, migration)
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


def _record_quality(
    conn: psycopg.Connection,
    report_date: date,
    checks: list[dict],
    include_mart: bool = True,
) -> None:
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
            if include_mart:
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


def _remove_failed_batch_events(conn: psycopg.Connection, report_date: date) -> None:
    """Remove raw rows left by an older pre-rollback failed run.

    This is intentionally limited to a date whose run status is not success.
    It lets a corrected generator safely retry a batch that was committed by
    an earlier version of the pipeline.
    """
    batch_id = f"batch:{report_date.isoformat()}"
    for table in RAW_EVENT_TABLES:
        conn.execute(f"DELETE FROM raw.{table} WHERE batch_id = %s", (batch_id,))


def _refresh_marts(conn: psycopg.Connection, report_date: date, lookback_days: int = 45) -> None:
    affected_dates = [report_date - timedelta(days=offset) for offset in range(lookback_days + 1)]
    _delete_mart_dates(conn, affected_dates)
    for affected_date in affected_dates:
        for name in ("daily_credit_kpi", "daily_portfolio_kpi", "daily_campaign_kpi", "vintage_kpi"):
            query = (MART_SQL / f"{name}.sql").read_text(encoding="utf-8")
            conn.execute(query, {"report_date": affected_date})


def _delete_mart_dates(conn: psycopg.Connection, report_dates: list[date]) -> None:
    with conn.cursor() as cur:
        for report_date in report_dates:
            for table in ("daily_credit_kpi", "daily_portfolio_kpi", "daily_campaign_kpi", "vintage_kpi"):
                cur.execute(f"DELETE FROM mart.{table} WHERE report_date = %s", (report_date,))


def _refresh_mart_date(report_date: date) -> dict[str, Any]:
    started = time.perf_counter()
    with connection() as conn:
        with conn.transaction():
            # The staging latest-version views contain several windowed sorts.
            # Keep the setting transaction-local so two workers can use more
            # memory without changing the PostgreSQL server configuration.
            conn.execute("SET LOCAL work_mem = '32MB'")
            _refresh_marts(conn, report_date, lookback_days=0)
            conn.execute(
                """
                UPDATE audit.pipeline_runs
                SET status = 'success', finished_at = NOW(), error_message = NULL
                WHERE run_date = %s AND status IN ('raw_loaded', 'failed_mart', 'success')
                """,
                (report_date,),
            )
    return {
        "report_date": report_date,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _mark_mart_failure(
    report_dates: list[date],
    error_message: str,
    delete_marts: bool = True,
) -> None:
    with connection() as conn:
        with conn.transaction():
            if delete_marts:
                _delete_mart_dates(conn, report_dates)
            with conn.cursor() as cur:
                for report_date in report_dates:
                    cur.execute(
                        """
                        UPDATE audit.pipeline_runs
                        SET status = 'failed_mart', finished_at = NOW(), error_message = %s
                        WHERE run_date = %s
                        """,
                        (error_message, report_date),
                    )


def _write_progress(
    log: Any,
    phase: str,
    completed: int,
    total: int,
    phase_started: float,
    workers: int,
    status: str = "success",
    error: str | None = None,
) -> None:
    elapsed = time.perf_counter() - phase_started
    rate = completed / elapsed if completed and elapsed else 0
    remaining = (total - completed) / rate if rate else 0
    eta = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + remaining,
        timezone.utc,
    ).isoformat()
    percent = completed / total * 100 if total else 100.0
    line = (
        f"phase={phase} workers={workers} completed={completed}/{total} "
        f"percent={percent:.1f}% elapsed_seconds={elapsed:.1f} "
        f"estimated_remaining_seconds={remaining:.1f} ETA_UTC={eta} status={status}"
    )
    if error:
        line += f" error={error}"
    log.write(line + "\n")
    log.flush()


def _refresh_marts_parallel(
    start: date,
    end: date,
    workers: int,
    log: Any,
    failed_status_dates: list[date] | None = None,
    delete_marts_on_failure: bool = True,
) -> None:
    report_dates = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    phase_started = time.perf_counter()
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mart-worker")
    futures = {executor.submit(_refresh_mart_date, report_date): report_date for report_date in report_dates}
    completed = 0
    try:
        for future in as_completed(futures):
            future.result()
            completed += 1
            # Write each completion so a long-running local schedule remains
            # observable even when one mart transaction takes several minutes.
            _write_progress(log, "mart", completed, len(report_dates), phase_started, workers)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        try:
            status_dates = report_dates if failed_status_dates is None else failed_status_dates
            if status_dates:
                _mark_mart_failure(
                    status_dates,
                    error,
                    delete_marts=delete_marts_on_failure,
                )
        except Exception as cleanup_exc:
            error = f"{error}; cleanup={type(cleanup_exc).__name__}: {cleanup_exc}"
        _write_progress(log, "mart", completed, len(report_dates), phase_started, workers, "failed", error)
        raise PipelineFailure(f"parallel mart rebuild failed after {completed}/{len(report_dates)} dates: {error}") from exc
    else:
        executor.shutdown(wait=True)


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


def run_day(
    report_date: date,
    lookback_days: int = 45,
    refresh_marts: bool = True,
    bootstrap_mode: bool = False,
) -> dict:
    started = time.perf_counter()
    batch: Batch | None = None
    try:
        with connection() as conn:
            with conn.transaction():
                if not bootstrap_mode:
                    conn.execute("SELECT pg_advisory_xact_lock(%s)", (BOOTSTRAP_LOCK_KEY,))
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                previous = fetch_rows(
                    conn,
                    "SELECT status, rows_loaded, rows_rejected FROM audit.pipeline_runs WHERE run_date = %s",
                    (report_date,),
                )
                if previous and previous[0]["status"] == "success":
                    if refresh_marts:
                        _refresh_marts(conn, report_date, lookback_days)
                    return {
                        "status": "success",
                        "report_date": report_date.isoformat(),
                        "loaded": previous[0]["rows_loaded"],
                        "rejected": previous[0]["rows_rejected"],
                        "idempotent_replay": True,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                if previous and previous[0]["status"] in {"raw_loaded", "failed_mart"}:
                    if refresh_marts:
                        _refresh_marts(conn, report_date, lookback_days)
                        _mark_run(
                            conn,
                            report_date,
                            "success",
                            f"batch:{report_date.isoformat()}",
                            previous[0]["rows_loaded"],
                            previous[0]["rows_rejected"],
                        )
                        return {
                            "status": "success",
                            "report_date": report_date.isoformat(),
                            "loaded": previous[0]["rows_loaded"],
                            "rejected": previous[0]["rows_rejected"],
                            "idempotent_replay": True,
                            "elapsed_seconds": round(time.perf_counter() - started, 3),
                        }
                    return {
                        "status": "raw_loaded",
                        "report_date": report_date.isoformat(),
                        "loaded": previous[0]["rows_loaded"],
                        "rejected": previous[0]["rows_rejected"],
                        "idempotent_replay": True,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                if previous and previous[0]["status"] in {"failed", "failed_quality", "running"}:
                    _remove_failed_batch_events(conn, report_date)
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
                    raise QualityGateFailure(report_date, loaded, len(batch.rejected), checks)
                status = "success"
                if refresh_marts:
                    _refresh_marts(conn, report_date, lookback_days)
                else:
                    status = "raw_loaded"
                _mark_run(conn, report_date, status, f"batch:{report_date.isoformat()}", loaded, len(batch.rejected))
                return {
                    "status": status,
                    "report_date": report_date.isoformat(),
                    "loaded": loaded,
                    "rejected": len(batch.rejected),
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
    except QualityGateFailure as exc:
        message = str(exc)
        try:
            with connection() as failed_conn:
                _record_quality(failed_conn, report_date, exc.checks, include_mart=False)
                _mark_run(
                    failed_conn,
                    report_date,
                    "failed_quality",
                    f"batch:{report_date.isoformat()}",
                    exc.loaded,
                    exc.rejected,
                    message,
                )
                failed_conn.commit()
        except Exception:
            pass
        return {
            "status": "failed_quality",
            "report_date": report_date.isoformat(),
            "loaded": exc.loaded,
            "rejected": exc.rejected,
            "failed_checks": [
                {"check_name": item["check_name"], "observed_value": item["observed_value"]}
                for item in exc.checks
                if item["severity"] == "hard" and item["status"] == "failed"
            ],
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


def _pending_start(through: date) -> date | None:
    """Return the first date that is missing or not successfully completed."""
    with connection() as conn:
        rows = fetch_rows(
            conn,
            "SELECT run_date, status FROM audit.pipeline_runs WHERE run_date <= %s ORDER BY run_date",
            (through,),
        )
    if not rows:
        raise PipelineFailure("no pipeline history exists; run bootstrap before daily processing")
    statuses = {row["run_date"]: row["status"] for row in rows}
    first_date = min(statuses)
    current = first_date
    while current <= through:
        if statuses.get(current) != "success":
            return current
        current += timedelta(days=1)
    return None


def _hard_quality_failures(checks: list[dict]) -> list[dict]:
    return [
        item
        for item in checks
        if item["severity"] == "hard" and item["status"] == "failed"
    ]


def daily(
    through: date,
    workers: int = 2,
    lookback_days: int = 45,
    publish: bool = False,
) -> dict[str, Any]:
    """Catch up local dates, refresh correction-window marts, and optionally publish them."""
    if workers < 1:
        raise ValueError("daily workers must be at least 1")
    if lookback_days < 0:
        raise ValueError("daily lookback_days must not be negative")

    log_path = ROOT / "logs" / f"local_daily_{through.isoformat()}.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    pending_start = _pending_start(through)
    pending_dates: list[date] = []
    if pending_start is not None:
        pending_dates = [
            pending_start + timedelta(days=offset)
            for offset in range((through - pending_start).days + 1)
        ]
    mart_start = through - timedelta(days=lookback_days)
    if pending_dates:
        mart_start = pending_dates[0] - timedelta(days=lookback_days)

    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"run_started={datetime.now(timezone.utc).isoformat()} through={through.isoformat()}\n")
        log.flush()
        try:
            raw_started = time.perf_counter()
            total_raw = len(pending_dates)
            if total_raw == 0:
                _write_progress(log, "raw", 0, 0, raw_started, 1, status="success")
            else:
                with connection() as lock_conn:
                    lock_conn.autocommit = True
                    lock_conn.execute("SELECT pg_advisory_lock(%s)", (BOOTSTRAP_LOCK_KEY,))
                    try:
                        for completed, report_date in enumerate(pending_dates, start=1):
                            result = run_day(
                                report_date,
                                lookback_days=0,
                                refresh_marts=False,
                                bootstrap_mode=True,
                            )
                            if result["status"] not in {"raw_loaded", "success"}:
                                raise PipelineFailure(
                                    f"daily raw phase stopped on {report_date}: {result}"
                                )
                            _write_progress(log, "raw", completed, total_raw, raw_started, 1)
                    finally:
                        lock_conn.execute("SELECT pg_advisory_unlock(%s)", (BOOTSTRAP_LOCK_KEY,))

            # Always rebuild the correction window: late-arriving payments and
            # source corrections can affect an already-successful report date.
            # Keep normal single-day runs out while the window is rebuilt.
            with connection() as mart_lock:
                mart_lock.autocommit = True
                mart_lock.execute("SELECT pg_advisory_lock(%s)", (BOOTSTRAP_LOCK_KEY,))
                try:
                    _refresh_marts_parallel(
                        mart_start,
                        through,
                        workers,
                        log,
                        failed_status_dates=pending_dates,
                        delete_marts_on_failure=False,
                    )
                finally:
                    mart_lock.execute("SELECT pg_advisory_unlock(%s)", (BOOTSTRAP_LOCK_KEY,))

            checks = validate_day(through)
            hard_failures = _hard_quality_failures(checks)
            if hard_failures:
                raise QualityGateFailure(through, 0, 0, checks)

            export_result = export_day(through)
            publish_result: dict[str, Any] | None = None
            if publish:
                publish_started = time.perf_counter()
                _write_progress(log, "publish", 0, 5, publish_started, 1, status="running")
                publish_result = publish_marts(mart_start, through)
                _write_progress(log, "publish", 5, 5, publish_started, 1)

            _write_progress(log, "daily", 1, 1, started, workers)
            return {
                "status": "success",
                "through": through.isoformat(),
                "pending_dates": len(pending_dates),
                "mart_start": mart_start.isoformat(),
                "export": export_result,
                "publish": publish_result,
                "log_path": str(log_path),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        except Exception as exc:
            _write_progress(
                log,
                "daily",
                0,
                1,
                started,
                workers,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise


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
            "dq": export_query(
                conn,
                "SELECT * FROM mart.dq_summary WHERE report_date = %s ORDER BY check_name",
                export_dir / f"dq_summary_{report_date.isoformat()}.csv",
                (report_date,),
            ),
        }


def bootstrap(start: date, end: date, workers: int = 2) -> None:
    if end < start:
        raise ValueError("bootstrap end must not precede start")
    if workers < 1:
        raise ValueError("bootstrap workers must be at least 1")
    total = (end - start).days + 1
    log_path = ROOT / "logs" / "bootstrap_progress.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with connection() as lock_conn:
        lock_conn.autocommit = True
        lock_conn.execute("SELECT pg_advisory_lock(%s)", (BOOTSTRAP_LOCK_KEY,))
        try:
            with log_path.open("w", encoding="utf-8") as log:
                raw_started = time.perf_counter()
                for completed, offset in enumerate(range(total), start=1):
                    report_date = start.fromordinal(start.toordinal() + offset)
                    result = run_day(
                        report_date,
                        lookback_days=0,
                        refresh_marts=False,
                        bootstrap_mode=True,
                    )
                    if result["status"] not in {"raw_loaded", "success"}:
                        raise PipelineFailure(f"bootstrap raw phase stopped on {report_date}: {result}")
                    if completed % 20 == 0 or completed == total:
                        _write_progress(log, "raw", completed, total, raw_started, 1)
                _refresh_marts_parallel(start, end, workers, log)
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (BOOTSTRAP_LOCK_KEY,))
