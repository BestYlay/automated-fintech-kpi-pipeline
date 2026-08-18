from __future__ import annotations

import csv
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

import psycopg
from psycopg import sql

from .config import Settings


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    settings = Settings.from_env()
    with psycopg.connect(settings.pipeline_database_url) as conn:
        yield conn


def _split_sql(script: str) -> list[str]:
    """Split the simple migration files without adding a SQL parser dependency."""
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in script:
        if char in "'\"" and (not current or current[-1] != "\\"):
            quote = None if quote == char else (char if quote is None else quote)
        if char == ";" and quote is None:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)
    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


def execute_sql_file(conn: psycopg.Connection, path: Path) -> None:
    for statement in _split_sql(path.read_text(encoding="utf-8")):
        conn.execute(statement)


def insert_events(
    conn: psycopg.Connection,
    table: str,
    rows: Iterable[dict],
) -> int:
    rows = list(rows)
    if not rows:
        return 0
    columns = list(rows[0])
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (source_event_id) DO NOTHING").format(
        sql.Identifier("raw", table),
        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    values = [tuple(row[c] for c in columns) for row in rows]
    with conn.cursor() as cur:
        cur.executemany(query, values)
        return cur.rowcount


def fetch_rows(conn: psycopg.Connection, query: str, params: tuple | dict = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        names = [column.name for column in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def export_query(conn: psycopg.Connection, query: str, path: Path, params: tuple | dict = ()) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with conn.cursor() as cur:
        cur.execute(query, params)
        names = [column.name for column in cur.description]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(names)
            count = 0
            for row in cur:
                writer.writerow(row)
                count += 1
    return count

