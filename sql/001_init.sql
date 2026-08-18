CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS raw.customer_events (
    source_event_id TEXT PRIMARY KEY,
    event_version INTEGER NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    batch_id TEXT NOT NULL,
    customer_id BIGINT NOT NULL,
    signup_date DATE NOT NULL,
    region TEXT NOT NULL,
    acquisition_channel TEXT NOT NULL,
    monthly_income NUMERIC(12,2) NOT NULL,
    existing_debt NUMERIC(12,2) NOT NULL,
    employment_tenure_months INTEGER NOT NULL,
    risk_score INTEGER NOT NULL,
    risk_grade TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.marketing_touch_events (
    source_event_id TEXT PRIMARY KEY,
    event_version INTEGER NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    batch_id TEXT NOT NULL,
    touch_id BIGINT NOT NULL,
    customer_id BIGINT NOT NULL,
    campaign_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    touch_date DATE NOT NULL,
    opened BOOLEAN NOT NULL,
    clicked BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.application_events (
    source_event_id TEXT PRIMARY KEY,
    event_version INTEGER NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    batch_id TEXT NOT NULL,
    application_id BIGINT NOT NULL,
    customer_id BIGINT NOT NULL,
    application_date DATE NOT NULL,
    product_code TEXT NOT NULL,
    channel TEXT NOT NULL,
    campaign_id TEXT,
    requested_amount NUMERIC(12,2) NOT NULL,
    decision TEXT NOT NULL,
    decision_date DATE NOT NULL,
    offer_accepted BOOLEAN NOT NULL,
    accepted_date DATE
);

CREATE TABLE IF NOT EXISTS raw.loan_events (
    source_event_id TEXT PRIMARY KEY,
    event_version INTEGER NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    batch_id TEXT NOT NULL,
    loan_id BIGINT NOT NULL,
    application_id BIGINT NOT NULL,
    customer_id BIGINT NOT NULL,
    disbursement_date DATE NOT NULL,
    principal NUMERIC(12,2) NOT NULL,
    term_months INTEGER NOT NULL,
    annual_rate NUMERIC(8,5) NOT NULL,
    risk_grade TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.installment_events (
    source_event_id TEXT PRIMARY KEY,
    event_version INTEGER NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    batch_id TEXT NOT NULL,
    installment_id BIGINT NOT NULL,
    loan_id BIGINT NOT NULL,
    installment_no INTEGER NOT NULL,
    due_date DATE NOT NULL,
    scheduled_principal NUMERIC(12,2) NOT NULL,
    scheduled_interest NUMERIC(12,2) NOT NULL,
    amount_due NUMERIC(12,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.payment_events (
    source_event_id TEXT PRIMARY KEY,
    event_version INTEGER NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    batch_id TEXT NOT NULL,
    payment_id BIGINT NOT NULL,
    loan_id BIGINT NOT NULL,
    installment_id BIGINT NOT NULL,
    payment_date DATE NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    principal_paid NUMERIC(12,2) NOT NULL,
    interest_paid NUMERIC(12,2) NOT NULL,
    payment_status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customer_business ON raw.customer_events(customer_id, event_version DESC);
CREATE INDEX IF NOT EXISTS idx_marketing_business ON raw.marketing_touch_events(touch_id, event_version DESC);
CREATE INDEX IF NOT EXISTS idx_application_business ON raw.application_events(application_id, event_version DESC);
CREATE INDEX IF NOT EXISTS idx_loan_business ON raw.loan_events(loan_id, event_version DESC);
CREATE INDEX IF NOT EXISTS idx_installment_business ON raw.installment_events(installment_id, event_version DESC);
CREATE INDEX IF NOT EXISTS idx_payment_business ON raw.payment_events(payment_id, event_version DESC);

CREATE OR REPLACE VIEW staging.customer_latest AS
SELECT * FROM (
    SELECT e.*, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY event_version DESC, ingested_at DESC) AS rn
    FROM raw.customer_events e
) x WHERE rn = 1;

CREATE OR REPLACE VIEW staging.marketing_touch_latest AS
SELECT * FROM (
    SELECT e.*, ROW_NUMBER() OVER (PARTITION BY touch_id ORDER BY event_version DESC, ingested_at DESC) AS rn
    FROM raw.marketing_touch_events e
) x WHERE rn = 1;

CREATE OR REPLACE VIEW staging.application_latest AS
SELECT * FROM (
    SELECT e.*, ROW_NUMBER() OVER (PARTITION BY application_id ORDER BY event_version DESC, ingested_at DESC) AS rn
    FROM raw.application_events e
) x WHERE rn = 1;

CREATE OR REPLACE VIEW staging.loan_latest AS
SELECT * FROM (
    SELECT e.*, ROW_NUMBER() OVER (PARTITION BY loan_id ORDER BY event_version DESC, ingested_at DESC) AS rn
    FROM raw.loan_events e
) x WHERE rn = 1;

CREATE OR REPLACE VIEW staging.installment_latest AS
SELECT * FROM (
    SELECT e.*, ROW_NUMBER() OVER (PARTITION BY installment_id ORDER BY event_version DESC, ingested_at DESC) AS rn
    FROM raw.installment_events e
) x WHERE rn = 1;

CREATE OR REPLACE VIEW staging.payment_latest AS
SELECT * FROM (
    SELECT e.*, ROW_NUMBER() OVER (PARTITION BY payment_id ORDER BY event_version DESC, ingested_at DESC) AS rn
    FROM raw.payment_events e
) x WHERE rn = 1;

CREATE TABLE IF NOT EXISTS audit.pipeline_runs (
    run_id BIGSERIAL PRIMARY KEY,
    run_date DATE NOT NULL UNIQUE,
    status TEXT NOT NULL,
    batch_id TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    rows_loaded INTEGER NOT NULL DEFAULT 0,
    rows_rejected INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS audit.quality_results (
    quality_id BIGSERIAL PRIMARY KEY,
    run_date DATE NOT NULL,
    check_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    observed_value NUMERIC,
    details TEXT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit.rejected_events (
    rejected_id BIGSERIAL PRIMARY KEY,
    run_date DATE NOT NULL,
    source_event_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload JSONB,
    rejected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mart.daily_credit_kpi (
    report_date DATE NOT NULL,
    channel TEXT NOT NULL,
    product_code TEXT NOT NULL,
    risk_grade TEXT NOT NULL,
    unique_applicants INTEGER NOT NULL,
    applications INTEGER NOT NULL,
    approvals INTEGER NOT NULL,
    accepted INTEGER NOT NULL,
    funded_count INTEGER NOT NULL,
    requested_amount NUMERIC(16,2) NOT NULL,
    funded_amount NUMERIC(16,2) NOT NULL,
    approval_rate NUMERIC(10,6),
    acceptance_rate NUMERIC(10,6),
    PRIMARY KEY (report_date, channel, product_code, risk_grade)
);

CREATE TABLE IF NOT EXISTS mart.daily_portfolio_kpi (
    report_date DATE NOT NULL,
    risk_grade TEXT NOT NULL,
    active_loans INTEGER NOT NULL,
    outstanding_principal NUMERIC(16,2) NOT NULL,
    amount_due NUMERIC(16,2) NOT NULL,
    amount_paid NUMERIC(16,2) NOT NULL,
    collection_rate NUMERIC(10,6),
    dpd_1_balance NUMERIC(16,2) NOT NULL,
    dpd_7_balance NUMERIC(16,2) NOT NULL,
    dpd_30_balance NUMERIC(16,2) NOT NULL,
    dpd_90_balance NUMERIC(16,2) NOT NULL,
    PRIMARY KEY (report_date, risk_grade)
);

CREATE TABLE IF NOT EXISTS mart.daily_campaign_kpi (
    report_date DATE NOT NULL,
    campaign_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    touches INTEGER NOT NULL,
    opens INTEGER NOT NULL,
    clicks INTEGER NOT NULL,
    applications INTEGER NOT NULL,
    funded_count INTEGER NOT NULL,
    funded_amount NUMERIC(16,2) NOT NULL,
    PRIMARY KEY (report_date, campaign_id, channel)
);

CREATE TABLE IF NOT EXISTS mart.vintage_kpi (
    report_date DATE NOT NULL,
    origination_month DATE NOT NULL,
    months_on_book INTEGER NOT NULL,
    active_loans INTEGER NOT NULL,
    outstanding_principal NUMERIC(16,2) NOT NULL,
    dpd_30_balance NUMERIC(16,2) NOT NULL,
    dpd_30_rate NUMERIC(10,6),
    PRIMARY KEY (report_date, origination_month, months_on_book)
);

CREATE TABLE IF NOT EXISTS mart.dq_summary (
    report_date DATE NOT NULL,
    check_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    observed_value NUMERIC,
    details TEXT,
    PRIMARY KEY (report_date, check_name)
);

CREATE INDEX IF NOT EXISTS idx_credit_date ON mart.daily_credit_kpi(report_date);
CREATE INDEX IF NOT EXISTS idx_portfolio_date ON mart.daily_portfolio_kpi(report_date);
CREATE INDEX IF NOT EXISTS idx_vintage_date ON mart.vintage_kpi(report_date);

