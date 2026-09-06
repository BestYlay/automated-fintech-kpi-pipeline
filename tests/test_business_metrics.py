import os
from datetime import date, timedelta
from pathlib import Path
from decimal import Decimal

import psycopg
import pytest
from fintech_pipeline.simulation import simulate_day, cure_payment, _stable_int


def test_product_floor_over_many_batches():
    for offset in range(60):
        batch=simulate_day(date(2026,1,1)+timedelta(days=offset),{})
        assert all(5000<=loan['principal']<=150000 for loan in batch.loans)


def test_partial_payment_can_cure_without_overallocation():
    iid=next(i for i in range(100) if _stable_int(i,'cure')%100<70)
    row=dict(installment_id=iid,loan_id=1,first_payment_date=date(2026,1,1),
             amount_due=110,scheduled_principal=100,amount_paid=55,principal_paid=50)
    day=row['first_payment_date']+timedelta(days=15+_stable_int(iid,'cure_delay')%46)
    assert cure_payment(row,day-timedelta(days=1)) is None
    payment=cure_payment(row,day)
    assert payment['amount']==55 and payment['principal_paid']==50 and payment['interest_paid']==5
    row.update(amount_paid=110,principal_paid=100)
    assert cure_payment(row,day) is None


@pytest.mark.skipif(not os.environ.get('PIPELINE_DATABASE_URL'),reason='PostgreSQL required')
def test_sql_delayed_funding_last_touch_and_loan_level_dpd():
    # Session-local fixtures only; never write persistent schemas or user data.
    root=Path(__file__).resolve().parents[1]
    with psycopg.connect(os.environ['PIPELINE_DATABASE_URL']) as c:
        c.execute('CREATE TEMP TABLE customer_latest(customer_id bigint,risk_grade text)')
        c.execute('CREATE TEMP TABLE application_latest(application_id bigint,customer_id bigint,application_date date,channel text,product_code text,decision text,offer_accepted boolean,requested_amount numeric,campaign_id text)')
        c.execute('CREATE TEMP TABLE loan_latest(loan_id bigint,application_id bigint,disbursement_date date,risk_grade text,principal numeric)')
        c.execute('CREATE TEMP TABLE installment_latest(installment_id bigint,loan_id bigint,due_date date,scheduled_principal numeric,amount_due numeric)')
        c.execute('CREATE TEMP TABLE payment_latest(installment_id bigint,payment_date date,amount numeric,principal_paid numeric)')
        c.execute('CREATE TEMP TABLE marketing_touch_latest(touch_id bigint,customer_id bigint,touch_date date,campaign_id text,channel text,opened boolean,clicked boolean)')
        for table in ['daily_credit_kpi','daily_campaign_kpi','daily_portfolio_kpi','vintage_kpi']:
            c.execute(f'CREATE TEMP TABLE {table} (LIKE mart.{table} INCLUDING ALL)')
        c.execute("INSERT INTO customer_latest VALUES(1,'A')")
        c.execute("INSERT INTO application_latest VALUES(1,1,'2026-01-03','sms','personal','approved',true,10000,NULL)")
        c.execute("INSERT INTO loan_latest VALUES(1,1,'2026-01-05','A',10000)")
        c.execute("INSERT INTO marketing_touch_latest VALUES(1,1,'2026-01-01','old','sms',true,false),(2,1,'2026-01-02','new','sms',true,true)")
        c.execute("INSERT INTO installment_latest VALUES(1,1,'2026-02-05',5000,5100),(2,1,'2026-12-05',5000,5100)")
        def model(name,day):
            query=(root/'sql'/'marts'/f'{name}.sql').read_text().replace('staging.','pg_temp.').replace('mart.','pg_temp.')
            c.execute(query,{'report_date':date.fromisoformat(day)})
        model('daily_credit_kpi','2026-01-03')
        model('daily_credit_kpi','2026-01-05')
        assert c.execute("SELECT applications,funded_count,funded_amount FROM daily_credit_kpi WHERE report_date='2026-01-05'").fetchone()==(0,1,Decimal('10000'))
        assert c.execute('SELECT sum(funded_count) FROM daily_credit_kpi').fetchone()[0]==1
        model('daily_campaign_kpi','2026-01-05')
        assert c.execute('SELECT sum(applications),sum(funded_amount) FROM daily_campaign_kpi').fetchone()==(1,Decimal('10000'))
        assert c.execute("SELECT applications FROM daily_campaign_kpi WHERE campaign_id='new'").fetchone()[0]==1
        model('daily_portfolio_kpi','2026-03-07')
        model('vintage_kpi','2026-03-07')
        assert c.execute('SELECT dpd_30_balance FROM daily_portfolio_kpi').fetchone()[0]==10000
        assert c.execute('SELECT dpd_30_balance,months_on_book FROM vintage_kpi').fetchone()==(Decimal('10000'),2)
        c.rollback()
