-- Tempiro Energy Monitor - Supabase Schema
-- Kör detta i Supabase SQL Editor

-- Tabell: energy_readings
CREATE TABLE IF NOT EXISTS energy_readings (
    id BIGSERIAL PRIMARY KEY,
    device_id TEXT NOT NULL,
    device_name TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    delta_power REAL NOT NULL,
    accumulated_value REAL NOT NULL,
    current_value REAL,
    UNIQUE(device_id, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_energy_device_time ON energy_readings(device_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_energy_timestamp ON energy_readings(timestamp DESC);

-- Tabell: spot_prices
CREATE TABLE IF NOT EXISTS spot_prices (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    price_area TEXT NOT NULL,
    price_sek REAL NOT NULL,
    price_eur REAL,
    UNIQUE(timestamp, price_area)
);

CREATE INDEX IF NOT EXISTS idx_spot_timestamp ON spot_prices(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_spot_area_time ON spot_prices(price_area, timestamp DESC);

-- Tabell: sync_status
CREATE TABLE IF NOT EXISTS sync_status (
    id BIGSERIAL PRIMARY KEY,
    sync_type TEXT NOT NULL,
    device_id TEXT,
    last_sync TIMESTAMPTZ NOT NULL,
    oldest_data TIMESTAMPTZ,
    UNIQUE(sync_type, device_id)
);

-- Row Level Security (RLS) - läs-åtkomst med publishable key
ALTER TABLE energy_readings ENABLE ROW LEVEL SECURITY;
ALTER TABLE spot_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_status ENABLE ROW LEVEL SECURITY;

-- Policy: alla kan läsa (publishable key)
CREATE POLICY "Allow read" ON energy_readings FOR SELECT USING (true);
CREATE POLICY "Allow read" ON spot_prices FOR SELECT USING (true);
CREATE POLICY "Allow read" ON sync_status FOR SELECT USING (true);

-- Policy: bara server (secret key) kan skriva
CREATE POLICY "Allow insert" ON energy_readings FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow insert" ON spot_prices FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow upsert" ON sync_status FOR ALL USING (true);

-- Tabell: monthly_summaries (cache för avslutade månader)
CREATE TABLE IF NOT EXISTS monthly_summaries (
    id BIGSERIAL PRIMARY KEY,
    month TEXT NOT NULL,
    total_kwh REAL,
    total_cost REAL,
    avg_price_ore REAL,
    readings INTEGER,
    partial BOOLEAN DEFAULT false,
    devices JSONB,
    UNIQUE(month)
);

CREATE INDEX IF NOT EXISTS idx_monthly_month ON monthly_summaries(month DESC);

ALTER TABLE monthly_summaries ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow read" ON monthly_summaries FOR SELECT USING (true);
CREATE POLICY "Allow upsert" ON monthly_summaries FOR ALL USING (true);
