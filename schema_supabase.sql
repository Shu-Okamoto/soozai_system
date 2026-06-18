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
   '{"monthly_fixed_cost":70000,"material_rate":0.5,"sales_split":{"west":"西店","south":"南店"},"dx_instore_only":["餅"],"features":{"dx_orders":true}}'::jsonb),
 (3,'tsukemono','漬物部',3,
   '{"monthly_fixed_cost":70000,"material_rate":0.5,"sales_split":{},"features":{"production":true}}'::jsonb)
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

-- ════════════════════════════════════════════════════════════
-- 原材料発注  [Phase 3]
-- カテゴリ選択 → 商品表示 → 在庫数入力 → 発注数(=基準数-在庫数、発注数単位があれば
-- 一番近い倍数に四捨五入)を算出して日付ごとに保存する。
-- すべて部署(department_id)でスコープ。再実行安全（idempotent）。
-- ════════════════════════════════════════════════════════════
-- 業者マスタ（発注先）
CREATE TABLE IF NOT EXISTS hq_suppliers (
    id            BIGSERIAL PRIMARY KEY,
    department_id BIGINT NOT NULL DEFAULT 1 REFERENCES hq_departments(id),
    name          TEXT NOT NULL,           -- 業者名
    order_days    TEXT DEFAULT '',         -- 発注曜日（例: "月,木"）
    delivery_days TEXT DEFAULT '',         -- 納品曜日（例: "火,金"）
    phone         TEXT DEFAULT '',         -- 電話番号
    site_url      TEXT DEFAULT '',         -- サイトURL
    sort_order    INTEGER DEFAULT 0,
    active        INTEGER DEFAULT 1,
    UNIQUE (department_id, name)
);
CREATE INDEX IF NOT EXISTS hq_suppliers_dept_idx ON hq_suppliers (department_id);

-- 発注商品マスタ
CREATE TABLE IF NOT EXISTS hq_order_products (
    id            BIGSERIAL PRIMARY KEY,
    department_id BIGINT NOT NULL DEFAULT 1 REFERENCES hq_departments(id),
    name          TEXT NOT NULL,           -- 商品名
    category      TEXT DEFAULT '',         -- 発注用の独自カテゴリ（肉・魚・調味料 等）
    price         INTEGER DEFAULT 0,       -- 価格
    supplier_id   BIGINT REFERENCES hq_suppliers(id) ON DELETE SET NULL,  -- 業者
    base_qty      INTEGER DEFAULT 0,       -- 基準数
    order_unit    INTEGER DEFAULT 0,       -- 発注数単位（0/NULL=単位指定なし）
    sort_order    INTEGER DEFAULT 0,
    active        INTEGER DEFAULT 1,
    UNIQUE (department_id, name)
);
CREATE INDEX IF NOT EXISTS hq_order_products_dept_idx ON hq_order_products (department_id);

-- 原材料発注 実績（日付 × 発注商品 の在庫数・算出した発注数）
CREATE TABLE IF NOT EXISTS hq_material_orders (
    id               BIGSERIAL PRIMARY KEY,
    department_id    BIGINT NOT NULL DEFAULT 1 REFERENCES hq_departments(id),
    date             TEXT NOT NULL,
    order_product_id BIGINT NOT NULL REFERENCES hq_order_products(id) ON DELETE CASCADE,
    stock_qty        INTEGER DEFAULT 0,    -- 在庫数
    order_qty        INTEGER DEFAULT 0,    -- 発注数（保存時点の算出値）
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (department_id, date, order_product_id)
);
CREATE INDEX IF NOT EXISTS hq_material_orders_date_idx ON hq_material_orders (department_id, date);

-- 商品の製造区分（漬物部で使用）: 'manufacture'(自社製造) | 'consignment'(製造委託)
ALTER TABLE hq_products ADD COLUMN IF NOT EXISTS prod_type TEXT DEFAULT 'manufacture';

-- ════════════════════════════════════════════════════════════
-- 漬物部：製造 → 在庫 → 出荷 → 請求  [Phase 4]
-- 製造(自社)／製造委託(納品)で入庫した数が在庫となり、出荷登録→出荷確定で在庫が減る。
-- 出荷先(hq_channels)×商品(hq_products)の単価表を持ち、月末締めで出荷先別に請求書を作成。
-- すべて部署(department_id)でスコープ。再実行安全（idempotent）。
-- ════════════════════════════════════════════════════════════
-- 製造・入庫（在庫を増やすイベント）
--   kind: 'manufacture'(自社製造) | 'consignment'(製造委託の納品入庫)
CREATE TABLE IF NOT EXISTS hq_production (
    id            BIGSERIAL PRIMARY KEY,
    department_id BIGINT NOT NULL DEFAULT 1 REFERENCES hq_departments(id),
    date          TEXT NOT NULL,
    product_id    BIGINT NOT NULL REFERENCES hq_products(id) ON DELETE CASCADE,
    qty           INTEGER DEFAULT 0,
    kind          TEXT DEFAULT 'manufacture',
    note          TEXT DEFAULT '',
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (department_id, date, product_id, kind)
);
CREATE INDEX IF NOT EXISTS hq_production_dept_date_idx ON hq_production (department_id, date);

-- 出荷先×商品の単価表（販売単価・税抜）
CREATE TABLE IF NOT EXISTS hq_product_prices (
    id            BIGSERIAL PRIMARY KEY,
    department_id BIGINT NOT NULL DEFAULT 1 REFERENCES hq_departments(id),
    channel_id    BIGINT NOT NULL REFERENCES hq_channels(id) ON DELETE CASCADE,
    product_id    BIGINT NOT NULL REFERENCES hq_products(id) ON DELETE CASCADE,
    price         INTEGER DEFAULT 0,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (department_id, channel_id, product_id)
);
CREATE INDEX IF NOT EXISTS hq_product_prices_ch_idx ON hq_product_prices (department_id, channel_id);

-- 出荷登録（FAX受領で登録→出荷確定の2段階。出荷確定時に在庫から減算）
--   status: 'registered'(登録済・未出荷) | 'shipped'(出荷済)
--   unit_price は登録時点の単価をスナップショット（単価表の後日変更で過去が変わらない）
CREATE TABLE IF NOT EXISTS hq_shipments (
    id            BIGSERIAL PRIMARY KEY,
    department_id BIGINT NOT NULL DEFAULT 1 REFERENCES hq_departments(id),
    order_date    TEXT NOT NULL,          -- 出荷登録日（FAX受領日）
    channel_id    BIGINT NOT NULL REFERENCES hq_channels(id),
    product_id    BIGINT NOT NULL REFERENCES hq_products(id),
    qty           INTEGER DEFAULT 0,
    unit_price    INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'registered',
    shipped_date  TEXT,                   -- 出荷確定日（請求の対象月はこの日で判定）
    note          TEXT DEFAULT '',
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS hq_shipments_order_idx   ON hq_shipments (department_id, order_date);
CREATE INDEX IF NOT EXISTS hq_shipments_shipped_idx ON hq_shipments (department_id, shipped_date);

-- 漬物部の機能フラグを有効化（製造日報／在庫／出荷登録／請求書／単価表マスタを表示）。
-- 新規インストールは上の seed で、既存DBはこの UPDATE で反映（再実行安全）。
UPDATE hq_departments
   SET config = jsonb_set(config, '{features,production}', 'true'::jsonb)
 WHERE code = 'tsukemono';
