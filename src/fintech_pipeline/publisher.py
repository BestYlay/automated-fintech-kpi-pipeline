from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import psycopg
from psycopg import sql

from .config import Settings
from .db import connection, fetch_rows


PUBLISH_LOCK_KEY = 517290106

MART_COLUMNS: dict[str, tuple[str, ...]] = {
    "daily_credit_kpi": (
        "report_date",
        "channel",
        "product_code",
        "risk_grade",
        "unique_applicants",
        "applications",
        "approvals",
        "accepted",
        "funded_count",
        "requested_amount",
        "funded_amount",
        "approval_rate",
        "acceptance_rate",
    ),
    "daily_portfolio_kpi": (
        "report_date",
        "risk_grade",
        "active_loans",
        "outstanding_principal",
        "amount_due",
        "amount_paid",
        "collection_rate",
        "dpd_1_balance",
        "dpd_7_balance",
        "dpd_30_balance",
        "dpd_90_balance",
    ),
    "daily_campaign_kpi": (
        "report_date",
        "touch_date",
        "campaign_id",
        "channel",
        "touches",
        "opens",
        "clicks",
        "applications",
        "funded_count",
        "funded_amount",
    ),
    "vintage_kpi": (
        "report_date",
        "origination_month",
        "months_on_book",
        "active_loans",
        "outstanding_principal",
        "dpd_30_balance",
        "dpd_30_rate",
    ),
    "dq_summary": (
        "report_date",
        "check_name",
        "severity",
        "status",
        "observed_value",
        "details",
    ),
}


def _validate_range(start: date, end: date) -> None:
    if end < start:
        raise ValueError("publish end must not precede start")


def _rows_from_local(start: date, end: date) -> dict[str, list[tuple[Any, ...]]]:
    snapshots: dict[str, list[tuple[Any, ...]]] = {}
    with connection() as conn:
        for table, columns in MART_COLUMNS.items():
            column_sql = ", ".join(columns)
            rows = fetch_rows(
                conn,
                f"SELECT {column_sql} FROM mart.{table} "
                "WHERE report_date BETWEEN %(start)s AND %(end)s "
                "ORDER BY report_date",
                {"start": start, "end": end},
            )
            snapshots[table] = [tuple(row[column] for column in columns) for row in rows]
    return snapshots


def _checksum(rows: list[tuple[Any, ...]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(row, default=str, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _check_target_schema(conn: psycopg.Connection) -> None:
    expected = {table: list(columns) for table, columns in MART_COLUMNS.items()}
    actual_rows = fetch_rows(
        conn,
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'mart' AND table_name = ANY(%s)
        ORDER BY table_name, ordinal_position
        """,
        (list(MART_COLUMNS),),
    )
    actual: dict[str, list[str]] = {table: [] for table in MART_COLUMNS}
    for row in actual_rows:
        actual.setdefault(row["table_name"], []).append(row["column_name"])
    missing = [table for table, columns in expected.items() if actual.get(table) != columns]
    if missing:
        raise RuntimeError(
            "Neon mart schema is missing or incompatible: "
            + ", ".join(missing)
            + ". Run sql/001_init.sql on the target database as its owner."
        )


def publish_marts(start: date, end: date) -> dict[str, Any]:
    """Atomically replace an aggregate mart date range on the remote target."""
    _validate_range(start, end)
    target_url = Settings.publish_database_url()
    snapshots = _rows_from_local(start, end)
    counts = {table: len(rows) for table, rows in snapshots.items()}
    checksums = {table: _checksum(rows) for table, rows in snapshots.items()}

    with psycopg.connect(target_url) as remote:
        with remote.transaction():
            remote.execute("SELECT pg_advisory_xact_lock(%s)", (PUBLISH_LOCK_KEY,))
            _check_target_schema(remote)
            for table, columns in MART_COLUMNS.items():
                remote.execute(
                    sql.SQL("DELETE FROM {} WHERE report_date BETWEEN %s AND %s").format(
                        sql.Identifier("mart", table)
                    ),
                    (start, end),
                )
                rows = snapshots[table]
                if rows:
                    insert_query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier("mart", table),
                        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
                    )
                    with remote.cursor() as cur:
                        cur.executemany(insert_query, rows)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "counts": counts,
        "checksums": checksums,
    }


def publisher_table_definitions() -> dict[str, tuple[str, ...]]:
    return dict(MART_COLUMNS)
