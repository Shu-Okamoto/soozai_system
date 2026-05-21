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
CREATE TABLE IF NOT EXISTS hq_categories (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    sort_order INTEGER DEFAULT 0,
    active     INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS hq_subcategories (
    id          BIGSERIAL PRIMARY KEY,
    category_id BIGINT NOT NULL REFERENCES hq_categories(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    sort_order  INTEGER DEFAULT 0,
    active      INTEGER DEFAULT 1,
    UNIQUE (category_id, name)
);
-- 初期データ（現行のハードコード値を再現）
INSERT INTO hq_categories (name, sort_order) VALUES
    ('弁当', 1), ('寿司', 2), ('惣菜', 3), ('その他', 4)
ON CONFLICT (name) DO NOTHING;
INSERT INTO hq_subcategories (category_id, name, sort_order)
SELECT c.id, s.name, s.sort_order FROM hq_categories c, (VALUES
    ('弁当','白米',1), ('弁当','三色',2), ('弁当','ちらし',3), ('弁当','炊き込み',4),
    ('惣菜','煮物',1), ('惣菜','酢もの',2), ('惣菜','サラダ',3), ('惣菜','魚',4),
    ('惣菜','天ぷら',5), ('惣菜','漬物',6), ('惣菜','和え物',7), ('惣菜','揚げ物',8), ('惣菜','その他',9)
) AS s(cat_name, name, sort_order) WHERE c.name = s.cat_name
ON CONFLICT (category_id, name) DO NOTHING;
