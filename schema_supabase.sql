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
    actuals_snapshot   JSONB,
    shifts_snapshot    JSONB,
    channels_snapshot  JSONB,
    finalized_at       TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);
-- 既存テーブルへの追加（再実行安全）
ALTER TABLE hq_daily_reports ADD COLUMN IF NOT EXISTS actuals_snapshot  JSONB;
ALTER TABLE hq_daily_reports ADD COLUMN IF NOT EXISTS shifts_snapshot   JSONB;
ALTER TABLE hq_daily_reports ADD COLUMN IF NOT EXISTS channels_snapshot JSONB;
ALTER TABLE hq_daily_reports ADD COLUMN IF NOT EXISTS finalized_at      TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS hq_daily_reports_finalized_idx
    ON hq_daily_reports (finalized_at) WHERE finalized_at IS NULL;
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
-- dx.InstoreOrder のミラー（履歴保持のため hq 側にも保存）
CREATE TABLE IF NOT EXISTS hq_instore_orders (
    id            BIGSERIAL PRIMARY KEY,
    date          TEXT NOT NULL,
    store_id      INTEGER NOT NULL,
    product_name  TEXT NOT NULL,
    customer_name TEXT DEFAULT '',
    quantity      INTEGER DEFAULT 0,
    price         INTEGER DEFAULT 0,
    category      TEXT DEFAULT '弁当',
    source_id     TEXT UNIQUE,
    synced_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS hq_instore_orders_date_idx ON hq_instore_orders (date);
-- チェックリスト（衛生管理：HACCP＋α）
-- period_type: 'daily' | 'monthly' / period_key: daily='YYYY-MM-DD', monthly='YYYY-MM'
CREATE TABLE IF NOT EXISTS hq_checklist_records (
    id          BIGSERIAL PRIMARY KEY,
    period_type TEXT NOT NULL,
    period_key  TEXT NOT NULL,
    item_key    TEXT NOT NULL,
    checked     BOOLEAN DEFAULT TRUE,
    checked_by  TEXT DEFAULT '',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (period_type, period_key, item_key)
);
CREATE INDEX IF NOT EXISTS hq_checklist_records_period_idx
    ON hq_checklist_records (period_type, period_key);
-- 初期データ（弁当部のカテゴリ/サブカテゴリ）は Phase 2 のユニーク制約変更後に投入する
-- （ファイル末尾の「初期データ」セクション参照）。

-- ─── 売上実績の DX 参照用ビュー ────────────────
-- 本部で取込んだ過去売上実績（hq_daily_reports）を DX 側システムから参照できるようにする。
-- hq と dx は同一 Supabase DB 内の別スキーマなので、dx スキーマに read 専用ビューを置けば
-- DX 側からは dx.sales_history として参照できる（取込APIは public.hq_daily_reports に書き込む）。
CREATE OR REPLACE VIEW dx.sales_history AS
SELECT
    date,
    weather,
    total_sales,
    west_sales,
    south_sales,
    other_sales,
    labor_cost,
    total_hours,
    material_cost,
    expense,
    profit,
    labor_productivity,
    updated_at
FROM public.hq_daily_reports;
-- PostgREST/各ロールから読めるように SELECT 権限を付与
GRANT USAGE ON SCHEMA dx TO anon, authenticated, service_role;
GRANT SELECT ON dx.sales_history TO anon, authenticated, service_role;

-- ════════════════════════════════════════════════════════════
-- 部署(department) 基盤  [Phase 1]
-- 弁当惣菜部のみで使われている本システムを、餅部・漬物部にも拡張するための土台。
-- ・全 hq_* テーブル（メンバーを除く）を「部署」で分離できるよう department_id を追加
-- ・既存データはすべて『弁当惣菜部(id=1)』に紐付け、新規行も既定で弁当部になる
--   → この Phase 1 を適用しても、アプリの挙動は一切変わらない（後方互換）
-- ・ユニーク/主キーへの department_id 取り込みは Phase 2（アプリのdept対応）で実施する
--   （現行コードは on_conflict='date' 等を使うため、ここでは制約を変更しない）
-- 本ファイルは再実行安全（idempotent）。
-- ════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS hq_departments (
    id         BIGSERIAL PRIMARY KEY,
    code       TEXT UNIQUE NOT NULL,          -- 'bento' | 'mochi' | 'tsukemono'
    name       TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    active     INTEGER DEFAULT 1,
    config     JSONB DEFAULT '{}'::jsonb      -- 部署ごとの設定（固定費・材料費率・機能フラグ等）
);
-- 弁当部は現行のハードコード値を厳密に再現。餅部・漬物部の数値は暫定（立上げ時に調整）。
INSERT INTO hq_departments (id, code, name, sort_order, config) VALUES
 (1,'bento','弁当惣菜部',1,
   '{"monthly_fixed_cost":300000,"material_rate":0.5,"sales_split":{"west":"西店","south":"南店"},"features":{"weekly_menu":true,"order_calc":true,"dx_orders":true,"npo_adjust":true,"separate_orders":true}}'::jsonb),
 (2,'mochi','餅部',2,
   '{"monthly_fixed_cost":70000,"material_rate":0.5,"sales_split":{},"features":{}}'::jsonb),
 (3,'tsukemono','漬物部',3,
   '{"monthly_fixed_cost":70000,"material_rate":0.5,"sales_split":{},"features":{}}'::jsonb)
ON CONFLICT (id) DO NOTHING;
-- 部署別の管理者PINは config.admin_pin に設定する（任意・サーバー側で検証）。例:
--   UPDATE hq_departments SET config = config || '{"admin_pin":"0000"}'::jsonb WHERE code='mochi';
-- 本部（全部署切替可）の管理者PINは環境変数 HQ_ADMIN_PIN（既定 1234）。
-- 明示id挿入後はシーケンスを進めておく（以降の自動採番が衝突しないように）
SELECT setval(pg_get_serial_sequence('hq_departments','id'),
              GREATEST((SELECT COALESCE(MAX(id),1) FROM hq_departments), 1));

-- 各テーブルに department_id を追加 → 既存行を弁当部(1)に backfill → 既定値を1に。
-- （hq_members は全部署で共有するため department_id を持たせない）
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'hq_products','hq_channels','hq_categories','hq_subcategories','hq_weekly_menus',
    'hq_shipping_plans','hq_shipping_actuals','hq_shifts','hq_shift_plans',
    'hq_daily_reports','hq_instore_orders','hq_checklist_records'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS department_id BIGINT REFERENCES hq_departments(id);', t);
    EXECUTE format('UPDATE %I SET department_id = 1 WHERE department_id IS NULL;', t);
    EXECUTE format('ALTER TABLE %I ALTER COLUMN department_id SET DEFAULT 1;', t);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I (department_id);', t||'_dept_idx', t);
  END LOOP;
END $$;

-- ════════════════════════════════════════════════════════════
-- 部署(department) 対応  [Phase 2]
-- アプリが department_id でスコープするようになったため、ユニーク/主キーに
-- department_id を取り込み、複数部署で同一(日付/名称/メンバー)を共存可能にする。
-- 既存の単一部署(弁当)データには影響しない。再実行安全。
-- 注: hq_shipping_plans / hq_shipping_actuals は UNIQUE(date,product_id,channel_id) のまま
--     （product_id/channel_id は部署内で一意なIDのため department_id 取込は不要）。
-- ════════════════════════════════════════════════════════════
-- department_id を NOT NULL 化（全行 backfill 済み・既定値1）
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'hq_products','hq_channels','hq_categories','hq_subcategories','hq_weekly_menus',
    'hq_shipping_plans','hq_shipping_actuals','hq_shifts','hq_shift_plans',
    'hq_daily_reports','hq_instore_orders','hq_checklist_records'
  ] LOOP
    EXECUTE format('UPDATE %I SET department_id = 1 WHERE department_id IS NULL;', t);
    EXECUTE format('ALTER TABLE %I ALTER COLUMN department_id SET NOT NULL;', t);
  END LOOP;
END $$;

-- hq_daily_reports: 主キーを (department_id, date) に変更
ALTER TABLE hq_daily_reports DROP CONSTRAINT IF EXISTS hq_daily_reports_pkey;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='hq_daily_reports_pkey') THEN
    ALTER TABLE hq_daily_reports ADD CONSTRAINT hq_daily_reports_pkey PRIMARY KEY (department_id, date);
  END IF;
END $$;

-- 名称/メンバー/チェック項目のユニークに department_id を取り込む
DO $$ BEGIN
  -- 商品名: (department_id, name)
  ALTER TABLE hq_products DROP CONSTRAINT IF EXISTS hq_products_name_key;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='hq_products_dept_name_key') THEN
    ALTER TABLE hq_products ADD CONSTRAINT hq_products_dept_name_key UNIQUE (department_id, name);
  END IF;
  -- 出荷先名: (department_id, name)
  ALTER TABLE hq_channels DROP CONSTRAINT IF EXISTS hq_channels_name_key;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='hq_channels_dept_name_key') THEN
    ALTER TABLE hq_channels ADD CONSTRAINT hq_channels_dept_name_key UNIQUE (department_id, name);
  END IF;
  -- カテゴリ名: (department_id, name)
  ALTER TABLE hq_categories DROP CONSTRAINT IF EXISTS hq_categories_name_key;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='hq_categories_dept_name_key') THEN
    ALTER TABLE hq_categories ADD CONSTRAINT hq_categories_dept_name_key UNIQUE (department_id, name);
  END IF;
  -- シフト実績: (department_id, date, member_name)  ※メンバーは全部署共有のため必須
  ALTER TABLE hq_shifts DROP CONSTRAINT IF EXISTS hq_shifts_date_member_name_key;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='hq_shifts_dept_date_member_key') THEN
    ALTER TABLE hq_shifts ADD CONSTRAINT hq_shifts_dept_date_member_key UNIQUE (department_id, date, member_name);
  END IF;
  -- シフト予定: (department_id, date, member_name)
  ALTER TABLE hq_shift_plans DROP CONSTRAINT IF EXISTS hq_shift_plans_date_member_name_key;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='hq_shift_plans_dept_date_member_key') THEN
    ALTER TABLE hq_shift_plans ADD CONSTRAINT hq_shift_plans_dept_date_member_key UNIQUE (department_id, date, member_name);
  END IF;
  -- チェックリスト: (department_id, period_type, period_key, item_key)
  ALTER TABLE hq_checklist_records DROP CONSTRAINT IF EXISTS hq_checklist_records_period_type_period_key_item_key_key;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='hq_checklist_dept_period_item_key') THEN
    ALTER TABLE hq_checklist_records ADD CONSTRAINT hq_checklist_dept_period_item_key UNIQUE (department_id, period_type, period_key, item_key);
  END IF;
END $$;

-- ─── 初期データ（弁当部のカテゴリ/サブカテゴリ。Phase 2 制約変更後に投入）──
INSERT INTO hq_categories (department_id, name, sort_order) VALUES
    (1,'弁当', 1), (1,'寿司', 2), (1,'惣菜', 3), (1,'その他', 4)
ON CONFLICT (department_id, name) DO NOTHING;
INSERT INTO hq_subcategories (department_id, category_id, name, sort_order)
SELECT 1, c.id, s.name, s.sort_order FROM hq_categories c, (VALUES
    ('弁当','白米',1), ('弁当','三色',2), ('弁当','ちらし',3), ('弁当','炊き込み',4),
    ('惣菜','煮物',1), ('惣菜','酢もの',2), ('惣菜','サラダ',3), ('惣菜','魚',4),
    ('惣菜','天ぷら',5), ('惣菜','漬物',6), ('惣菜','和え物',7), ('惣菜','揚げ物',8), ('惣菜','その他',9)
) AS s(cat_name, name, sort_order) WHERE c.name = s.cat_name AND c.department_id = 1
ON CONFLICT (category_id, name) DO NOTHING;
