"""Append versioned historical corrections and atomically replace validated marts.

Dedicated project database only. Keeps raw and mart backups in business_fix_20260906.
Run with PIPELINE_DATABASE_URL; add --publish to update the configured Neon target.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import threading
import time

from psycopg import sql
from fintech_pipeline.db import connection, fetch_rows, insert_events
from fintech_pipeline.finance import amortization_schedule
from fintech_pipeline.pipeline import (BOOTSTRAP_LOCK_KEY, RAW_EVENT_TABLES, MART_SQL,
    _quality_checks, _record_quality, _write_progress, verify_funding, export_day)
from fintech_pipeline.publisher import MART_COLUMNS, publish_marts
from fintech_pipeline.simulation import cure_payment, _stable_int

BACKUP = 'business_fix_20260906'
VIEWS = ['customer_latest','application_latest','loan_latest','installment_latest','payment_latest','marketing_touch_latest']
ROOT = Path(__file__).resolve().parents[1]


def append_version(conn, table, row, changed):
    row = {k:v for k,v in row.items() if k!='rn'}
    row.update(changed)
    row['event_version'] += 1
    row['source_event_id'] += ':business-fix-v2'
    row['batch_id'] = 'business-fix-v2'
    row['ingested_at'] = datetime.now(timezone.utc)
    insert_events(conn, table, [row])


def correct_history(conn, end):
    loans = fetch_rows(conn, 'SELECT * FROM staging.loan_latest WHERE principal<5000 ORDER BY loan_id')
    count = 0
    for loan in loans:
        old_rows = fetch_rows(conn,'SELECT * FROM staging.installment_latest WHERE loan_id=%s ORDER BY installment_no',(loan['loan_id'],))
        new_rows = amortization_schedule(loan['loan_id'],5000,float(loan['annual_rate']),loan['term_months'],loan['disbursement_date'])
        payments = fetch_rows(conn,'SELECT * FROM staging.payment_latest WHERE loan_id=%s ORDER BY payment_id',(loan['loan_id'],))
        by_id = {r['installment_id']:r for r in old_rows}
        new_by_id = {r['installment_id']:r for r in new_rows}
        append_version(conn,'loan_events',loan,{'principal':Decimal('5000.00')})
        for old in old_rows:
            new = new_by_id[old['installment_id']]
            append_version(conn,'installment_events',old,{k:new[k] for k in ['scheduled_principal','scheduled_interest','amount_due']})
        for p in payments:
            old,new=by_id[p['installment_id']],new_by_id[p['installment_id']]
            fraction=float(p['amount']/old['amount_due'])
            amount=round(new['amount_due']*fraction,2)
            principal=round(new['scheduled_principal']*fraction,2)
            append_version(conn,'payment_events',p,dict(amount=amount,principal_paid=principal,interest_paid=round(amount-principal,2)))
        count+=1
    partial = fetch_rows(conn,"""
       SELECT i.*,p.amount_paid,p.principal_paid,p.first_payment_date FROM staging.installment_latest i
       JOIN (SELECT installment_id,sum(amount) amount_paid,sum(principal_paid) principal_paid,
             min(payment_date) first_payment_date FROM staging.payment_latest GROUP BY installment_id) p USING(installment_id)
       WHERE p.amount_paid>0 AND p.amount_paid<i.amount_due
    """)
    cures=[]
    for row in partial:
        cure_date=row['first_payment_date']+timedelta(days=15+_stable_int(row['installment_id'],'cure_delay')%46)
        if cure_date<=end:
            p=cure_payment(row,cure_date)
            if p:
                p['batch_id']='business-fix-v2'
                p['ingested_at']=datetime.now(timezone.utc)
                cures.append(p)
    insert_events(conn,'payment_events',cures)
    return count,len(cures)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    started=time.perf_counter()
    mutex=threading.Lock()
    with (ROOT/'logs'/'business_repair_progress.txt').open('a',encoding='utf-8',buffering=1) as log, connection() as guard:
        guard.autocommit=True
        guard.execute('SELECT pg_advisory_lock(%s)',(BOOTSTRAP_LOCK_KEY,))
        try:
            with connection() as conn:
                start,end=conn.execute('SELECT min(report_date),max(report_date) FROM mart.daily_credit_kpi').fetchone()
                assert start and end
                if not conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(BACKUP,)).fetchone():
                    conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(BACKUP)))
                    for schema,tables in [('raw',RAW_EVENT_TABLES),('mart',MART_COLUMNS),('audit',['pipeline_runs','quality_results'])]:
                        for table in tables:
                            conn.execute(sql.SQL('CREATE TABLE {} AS TABLE {}').format(sql.Identifier(BACKUP,schema+'_'+table),sql.Identifier(schema,table)))
                conn.commit()
                _write_progress(log,'backup',1,1,started,1)
                counts=correct_history(conn,end)
                checks=_quality_checks(conn,end)
                if any(c['status']=='failed' and c['severity']=='hard' for c in checks):
                    raise RuntimeError('Correction quality failure: '+str(checks))
                conn.commit()
                _write_progress(log,'corrections',1,1,started,1,error=str(dict(repriced_loans=counts[0],cure_payments=counts[1])))
                for table in MART_COLUMNS:
                    conn.execute(sql.SQL('CREATE TABLE IF NOT EXISTS {} (LIKE {} INCLUDING ALL)').format(sql.Identifier(BACKUP,'new_'+table),sql.Identifier('mart',table)))
                    conn.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier(BACKUP,'new_'+table)))
                conn.commit()
            dates=[start+timedelta(days=i) for i in range((end-start).days+1)]
            progress=[0]
            phase=time.perf_counter()
            def worker(assigned):
                with connection() as c:
                    c.execute("SET work_mem='32MB'")
                    for view in VIEWS:
                        c.execute(f'CREATE TEMP TABLE {view} AS SELECT * FROM staging.{view}')
                    for view,cols in [('customer_latest','customer_id'),('application_latest','application_date'),('application_latest','application_id'),('loan_latest','application_id'),('loan_latest','loan_id'),('installment_latest','loan_id'),('payment_latest','installment_id'),('marketing_touch_latest','customer_id,touch_date')]:
                        c.execute(f'CREATE INDEX ON {view} ({cols})')
                    c.execute('ANALYZE')
                    c.commit()
                    for day in assigned:
                        for table in list(MART_COLUMNS)[:4]:
                            query=(MART_SQL/(table+'.sql')).read_text(encoding='utf-8').replace('staging.','pg_temp.').replace('mart.'+table,BACKUP+'.new_'+table)
                            c.execute(query,{'report_date':day})
                        c.commit()
                        with mutex:
                            progress[0]+=1
                            _write_progress(log,'mart',progress[0],len(dates),phase,2)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(worker,dates[n::2]) for n in range(2)]
                for f in futures:f.result()
            with connection() as c:
                for table in list(MART_COLUMNS)[:4]:
                    c.execute(f'DELETE FROM mart.{table}')
                    c.execute(f'INSERT INTO mart.{table} SELECT * FROM {BACKUP}.new_{table}')
                for day in dates:
                    verify_funding(c,day)
                    _record_quality(c,day,[check for check in checks if check['severity']=='hard'])
                # Independent whole-history disbursement totals must match.
                actual=c.execute('SELECT count(*),sum(principal) FROM staging.loan_latest WHERE disbursement_date BETWEEN %s AND %s',(start,end)).fetchone()
                observed=c.execute('SELECT sum(funded_count),sum(funded_amount) FROM mart.daily_credit_kpi').fetchone()
                assert actual==observed,(actual,observed)
                print('funding_verified',actual,flush=True)
            _write_progress(log,'local_verified',1,1,started,2)
            export_day(end)
            if args.publish:
                print(publish_marts(start,end),flush=True)
            _write_progress(log,'finished',1,1,started,2)
        except Exception as exc:
            _write_progress(log,'repair',0,1,started,2,'failed',type(exc).__name__+': '+str(exc))
            raise
        finally:
            guard.execute('SELECT pg_advisory_unlock(%s)',(BOOTSTRAP_LOCK_KEY,))


if __name__=='__main__':
    main()
