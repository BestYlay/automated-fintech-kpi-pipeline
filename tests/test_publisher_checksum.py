from datetime import date
from decimal import Decimal

from fintech_pipeline.publisher import _checksum
import fintech_pipeline.publisher as publisher
import psycopg
import pytest


def test_checksum_ignores_database_row_order_but_preserves_duplicates():
    rows = [(date(2026, 9, 1), Decimal('10.00')), (date(2026, 9, 1), Decimal('20.00'))]
    assert _checksum(rows) == _checksum(list(reversed(rows)))
    assert _checksum(rows) != _checksum(rows + rows[:1])
    assert _checksum(rows) != _checksum(rows[:1])


def test_target_connection_retries_transient_failure(monkeypatch):
    attempts = []
    sentinel = object()
    def connect(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise psycopg.OperationalError('temporary DNS failure')
        return sentinel
    monkeypatch.setattr(publisher.psycopg, 'connect', connect)
    monkeypatch.setattr(publisher.time, 'sleep', lambda _: None)
    assert publisher._connect_target('test') is sentinel
    assert len(attempts) == 3


def test_target_connection_stops_after_three_failures(monkeypatch):
    attempts = []
    def connect(*args, **kwargs):
        attempts.append(1)
        raise psycopg.OperationalError('offline')
    monkeypatch.setattr(publisher.psycopg, 'connect', connect)
    monkeypatch.setattr(publisher.time, 'sleep', lambda _: None)
    with pytest.raises(psycopg.OperationalError):
        publisher._connect_target('test')
    assert len(attempts) == 3
