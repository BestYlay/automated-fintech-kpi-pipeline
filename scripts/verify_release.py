"""Release verification using explicitly configured local and remote databases."""
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
from fintech_pipeline.publisher import publish_marts, MART_COLUMNS, _checksum
from streamlit.testing.v1 import AppTest


def main():
    started = time.perf_counter()
    root = Path(__file__).resolve().parents[1]
    total = 10
    with (root / 'logs' / 'release_verification.txt').open('w', encoding='utf-8', buffering=1) as log:
        def progress(n, detail):
            elapsed = time.perf_counter() - started
            remaining = elapsed / n * (total - n) if n else 0
            record = dict(completed=n, total=total, percent=100*n/total,
                          elapsed_seconds=round(elapsed, 2), estimated_remaining_seconds=round(remaining, 2),
                          ETA=(datetime.now(timezone.utc)+timedelta(seconds=remaining)).isoformat(), detail=detail)
            log.write(json.dumps(record, default=str)+'\n')
            log.flush()
            print(json.dumps(record, default=str), flush=True)
        progress(0, 'started')
        with psycopg.connect(os.environ['PIPELINE_DATABASE_URL']) as local:
            start, end = local.execute('SELECT min(report_date),max(report_date) FROM mart.daily_credit_kpi').fetchone()
            failures = local.execute("SELECT count(*) FROM mart.dq_summary WHERE severity='hard' AND status='failed'").fetchone()[0]
            assert failures == 0
        progress(1, dict(start=start, end=end, hard_failures=failures))
        first = publish_marts(start, end)
        progress(2, first)
        second = publish_marts(start, end)
        assert first == second
        progress(3, 'Full-range repeat publication verified identical')
        with psycopg.connect(os.environ['MART_PUBLISH_DATABASE_URL'], autocommit=True) as remote:
            def snapshot():
                return {t: _checksum(remote.execute(f'SELECT * FROM mart.{t}').fetchall()) for t in MART_COLUMNS}
            before = snapshot()
            class ForcedFailure(Exception): pass
            try:
                with remote.transaction():
                    remote.execute('SELECT pg_advisory_xact_lock(%s)', (517290106,))
                    remote.execute('DELETE FROM mart.daily_credit_kpi WHERE report_date=%s', (end,))
                    raise ForcedFailure()
            except ForcedFailure:
                pass
            assert snapshot() == before
        progress(4, 'Forced transaction failure rolled back; all five remote tables unchanged')
        for key, writable in [('READ_ONLY_DATABASE_URL', False), ('MART_PUBLISH_DATABASE_URL', True)]:
            with psycopg.connect(os.environ[key]) as conn:
                for schema in ['raw', 'staging', 'audit']:
                    assert not conn.execute('SELECT has_schema_privilege(current_user,%s,\'USAGE\')', (schema,)).fetchone()[0]
                for table in MART_COLUMNS:
                    assert conn.execute('SELECT has_table_privilege(current_user,%s,\'SELECT\')', ('mart.'+table,)).fetchone()[0]
                    for privilege in ['INSERT','UPDATE','DELETE']:
                        assert conn.execute('SELECT has_table_privilege(current_user,%s,%s)', ('mart.'+table, privilege)).fetchone()[0] == writable
        progress(5, 'Effective reader/publisher schema and five-table privileges verified')
        app = AppTest.from_file(str(root/'dashboard'/'app.py'), default_timeout=60).run()
        for index, page in enumerate(['Overview','Credit funnel','Portfolio risk','Campaigns','Data quality']):
            if index:
                app.sidebar.radio[0].set_value(page).run(timeout=60)
            assert not app.exception, page + ' raised an exception'
            assert not app.error, page + ' displayed an error'
            assert app.sidebar.metric[0].value == str(end)
            progress(6+index, page + ' rendered against Neon; latest date ' + str(end))


if __name__ == '__main__':
    main()
