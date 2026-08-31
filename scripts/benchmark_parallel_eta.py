"""Benchmark parallel mart refreshes without changing the database.

Each date runs in its own PostgreSQL transaction and rolls back after the four
mart statements finish.  The benchmark therefore measures real query cost
while preserving the existing synthetic history.
"""

from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]
MART_SQL = ROOT / "sql" / "marts"
MART_TABLES = (
    "daily_credit_kpi",
    "daily_portfolio_kpi",
    "daily_campaign_kpi",
    "vintage_kpi",
)
DEFAULT_DATES = (
    "2025-01-01",
    "2025-04-01",
    "2025-07-01",
    "2025-10-01",
    "2026-01-01",
    "2026-04-01",
    "2026-07-01",
    "2026-08-18",
)


def refresh_and_rollback(report_date: date, database_url: str) -> float:
    started = time.perf_counter()
    with psycopg.connect(database_url) as conn:
        try:
            for table in MART_TABLES:
                conn.execute(f"DELETE FROM mart.{table} WHERE report_date = %s", (report_date,))
            for name in MART_TABLES:
                query = (MART_SQL / f"{name}.sql").read_text(encoding="utf-8")
                conn.execute(query, {"report_date": report_date})
            conn.rollback()
        except Exception:
            conn.rollback()
            raise
    return time.perf_counter() - started


def benchmark(dates: list[date], workers: int, log_path: Path) -> dict[str, float | int]:
    database_url = os.environ["PIPELINE_DATABASE_URL"]
    started = time.perf_counter()
    completed = 0
    durations: list[float] = []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(refresh_and_rollback, report_date, database_url) for report_date in dates]
        with log_path.open("a", encoding="utf-8") as log:
            for future in as_completed(futures):
                durations.append(future.result())
                completed += 1
                elapsed = time.perf_counter() - started
                rate = completed / elapsed if elapsed else 0
                remaining = (len(dates) - completed) / rate if rate else 0
                eta = datetime.fromtimestamp(
                    datetime.now(timezone.utc).timestamp() + remaining,
                    timezone.utc,
                ).isoformat()
                log.write(
                    f"workers={workers} completed={completed}/{len(dates)} "
                    f"percent={completed / len(dates) * 100:.1f}% "
                    f"elapsed_seconds={elapsed:.1f} "
                    f"estimated_remaining_seconds={remaining:.1f} ETA_UTC={eta}\n"
                )
                log.flush()
    wall_seconds = time.perf_counter() - started
    return {
        "workers": workers,
        "dates": len(dates),
        "wall_seconds": round(wall_seconds, 3),
        "sum_query_seconds": round(sum(durations), 3),
        "estimated_full_mart_rebuild_seconds": round(wall_seconds / len(dates) * 595, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", nargs="*", default=list(DEFAULT_DATES))
    parser.add_argument("--workers", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument("--log", default=str(ROOT / "logs" / "parallel_eta_benchmark.txt"))
    args = parser.parse_args()
    dates = [date.fromisoformat(value) for value in args.dates]
    log_path = Path(args.log)
    log_path.write_text("", encoding="utf-8")
    for workers in args.workers:
        result = benchmark(dates, workers, log_path)
        print(result)


if __name__ == "__main__":
    main()
