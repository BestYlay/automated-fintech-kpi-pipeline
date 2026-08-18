ALTER TABLE mart.daily_campaign_kpi
    ADD COLUMN IF NOT EXISTS touch_date DATE;

UPDATE mart.daily_campaign_kpi
SET touch_date = report_date
WHERE touch_date IS NULL;

ALTER TABLE mart.daily_campaign_kpi
    ALTER COLUMN touch_date SET NOT NULL;

ALTER TABLE mart.daily_campaign_kpi
    DROP CONSTRAINT IF EXISTS daily_campaign_kpi_pkey;

ALTER TABLE mart.daily_campaign_kpi
    ADD CONSTRAINT daily_campaign_kpi_pkey
    PRIMARY KEY (report_date, touch_date, campaign_id, channel);

