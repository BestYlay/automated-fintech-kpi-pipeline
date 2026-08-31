-- Run this manually on the Neon target as its database owner.
-- Create `mart_publisher` and `dashboard_reader` in Neon Console first so
-- their passwords are generated outside the repository.
-- Replace `fintech_db` below with the actual Neon database name if it differs.

GRANT CONNECT ON DATABASE fintech_db TO mart_publisher;
REVOKE ALL ON SCHEMA raw, staging, audit FROM mart_publisher;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA raw, staging, audit FROM mart_publisher;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA raw, staging, audit FROM mart_publisher;
GRANT USAGE ON SCHEMA mart TO mart_publisher;
REVOKE ALL PRIVILEGES ON
    mart.daily_credit_kpi,
    mart.daily_portfolio_kpi,
    mart.daily_campaign_kpi,
    mart.vintage_kpi,
    mart.dq_summary
FROM mart_publisher;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    mart.daily_credit_kpi,
    mart.daily_portfolio_kpi,
    mart.daily_campaign_kpi,
    mart.vintage_kpi,
    mart.dq_summary
TO mart_publisher;

GRANT CONNECT ON DATABASE fintech_db TO dashboard_reader;
REVOKE ALL ON SCHEMA raw, staging, audit FROM dashboard_reader;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA raw, staging, audit FROM dashboard_reader;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA raw, staging, audit FROM dashboard_reader;
REVOKE CREATE ON SCHEMA public FROM dashboard_reader;
GRANT USAGE ON SCHEMA mart TO dashboard_reader;
REVOKE ALL PRIVILEGES ON
    mart.daily_credit_kpi,
    mart.daily_portfolio_kpi,
    mart.daily_campaign_kpi,
    mart.vintage_kpi,
    mart.dq_summary
FROM dashboard_reader;
GRANT SELECT ON
    mart.daily_credit_kpi,
    mart.daily_portfolio_kpi,
    mart.daily_campaign_kpi,
    mart.vintage_kpi,
    mart.dq_summary
TO dashboard_reader;
ALTER ROLE dashboard_reader SET default_transaction_read_only = on;
