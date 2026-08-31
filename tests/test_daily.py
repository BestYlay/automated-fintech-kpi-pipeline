from datetime import date
from pathlib import Path
import shutil

import fintech_pipeline.pipeline as pipeline
from fintech_pipeline.publisher import _checksum, publisher_table_definitions
from fintech_pipeline.config import Settings
import pytest


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args):
        return None

    @property
    def autocommit(self):
        return False

    @autocommit.setter
    def autocommit(self, _value):
        return None


def test_pending_start_finds_first_gap(monkeypatch):
    rows = [
        {"run_date": date(2026, 1, 1), "status": "success"},
        {"run_date": date(2026, 1, 2), "status": "success"},
        {"run_date": date(2026, 1, 3), "status": "raw_loaded"},
    ]
    monkeypatch.setattr(pipeline, "connection", lambda: _FakeConnection())
    monkeypatch.setattr(pipeline, "fetch_rows", lambda *_args, **_kwargs: rows)

    assert pipeline._pending_start(date(2026, 1, 4)) == date(2026, 1, 3)


def test_daily_is_idempotent_when_through_date_is_already_successful(monkeypatch):
    test_root = Path.cwd() / "tests" / "_daily_test_tmp"
    shutil.rmtree(test_root, ignore_errors=True)
    test_root.mkdir(parents=True)
    monkeypatch.setattr(pipeline, "ROOT", test_root)
    monkeypatch.setattr(pipeline, "_pending_start", lambda _through: None)
    monkeypatch.setattr(pipeline, "validate_day", lambda _report_date: [])
    monkeypatch.setattr(pipeline, "export_day", lambda _report_date: {"credit": 0})
    refreshed = []
    monkeypatch.setattr(
        pipeline,
        "_refresh_marts_parallel",
        lambda start, end, workers, log, **kwargs: refreshed.append((start, end, workers)),
    )
    monkeypatch.setattr(pipeline, "connection", lambda: _FakeConnection())

    result = pipeline.daily(date(2026, 8, 18), publish=False)

    assert result["status"] == "success"
    assert result["pending_dates"] == 0
    assert refreshed == [(date(2026, 7, 4), date(2026, 8, 18), 2)]
    assert (test_root / "logs" / "local_daily_2026-08-18.txt").exists()
    shutil.rmtree(test_root, ignore_errors=True)


def test_progress_writer_handles_empty_phase():
    path = Path.cwd() / "tests" / "_daily_progress_test.txt"
    with path.open("w", encoding="utf-8") as handle:
        pipeline._write_progress(handle, "raw", 0, 0, pipeline.time.perf_counter(), 1)
    assert "completed=0/0" in path.read_text(encoding="utf-8")
    assert "percent=100.0%" in path.read_text(encoding="utf-8")
    path.unlink()


def test_publisher_checksum_and_contract_are_stable():
    rows = [(date(2026, 1, 1), "A"), (date(2026, 1, 2), "B")]
    assert _checksum(rows) == _checksum(rows)
    assert set(publisher_table_definitions()) == {
        "daily_credit_kpi",
        "daily_portfolio_kpi",
        "daily_campaign_kpi",
        "vintage_kpi",
        "dq_summary",
    }


def test_publisher_url_is_required(monkeypatch):
    monkeypatch.delenv("MART_PUBLISH_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="MART_PUBLISH_DATABASE_URL"):
        Settings.publish_database_url()
