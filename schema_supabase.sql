-- Supabase SQL Editorで実行してください
CREATE TABLE IF NOT EXISTS hq_products (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    price       INTEGER DEFAULT 0,
    category    TEXT DEFAULT '',
    subcategory TEXT DEFAULT '',
    active      INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS hq_channels (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    sort_order INTEGER DEFAULT 0,
    active     INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS hq_weekly_menus (
    id          BIGSERIAL PRIMARY KEY,
    week_start  TEXT NOT NULL,
    day_of_week INTEGER,
    category    TEXT,
    menu_name   TEXT
);
CREATE TABLE IF NOT EXISTS hq_shipping_plans (
    id          BIGSERIAL PRIMARY KEY,
    date        TEXT NOT NULL,
    product_id  INTEGER,
    channel_id  INTEGER,
    planned_qty INTEGER DEFAULT 0,
    note        TEXT DEFAULT '',
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(date,product_id,channel_id)
);
CREATE TABLE IF NOT EXISTS hq_shipping_actuals (
    id            BIGSERIAL PRIMARY KEY,
    date          TEXT NOT NULL,
    product_id    INTEGER,
    channel_id    INTEGER,
    actual_qty    INTEGER DEFAULT 0,
    actual_amount INTEGER DEFAULT 0,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(date,product_id,channel_id)
);
CREATE TABLE IF NOT EXISTS hq_shifts (
    id          BIGSERIAL PRIMARY KEY,
    date        TEXT NOT NULL,
    member_name TEXT NOT NULL,
    hours       REAL DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(date,member_name)
);
CREATE TABLE IF NOT EXISTS hq_shift_plans (
    id            BIGSERIAL PRIMARY KEY,
    date          TEXT NOT NULL,
    member_name   TEXT NOT NULL,
    planned_hours REAL DEFAULT 0,
    UNIQUE(date,member_name)
);
CREATE TABLE IF NOT EXISTS hq_daily_reports (
    date               TEXT PRIMARY KEY,
    weather            TEXT DEFAULT '',
    total_sales        INTEGER DEFAULT 0,
    separate_orders    INTEGER DEFAULT 0,
    material_cost      INTEGER DEFAULT 0,
    labor_cost         INTEGER DEFAULT 0,
    expense            INTEGER DEFAULT 0,
    profit             INTEGER DEFAULT 0,
    labor_productivity REAL DEFAULT 0,
    total_hours        REAL DEFAULT 0,
    west_sales         INTEGER DEFAULT 0,
    south_sales        INTEGER DEFAULT 0,
    other_sales        INTEGER DEFAULT 0,
    note               TEXT DEFAULT '',
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS hq_members (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    hourly_wage INTEGER DEFAULT 0,
    active      INTEGER DEFAULT 1
);
