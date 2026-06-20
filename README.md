# 惣菜本部 業務システム

みかわ 弁当・惣菜部の社内向け業務システム。
出荷計画／実績入力／日報／週間献立／月次・年次サマリ／シフト管理 を一元化。

---

## 構成

```
soozai_system/
├── app.py                ← APIサーバー（Python Flask + Supabase）
├── finalize_pending.py   ← 日報の自動確定 Cron（Render Cron Job）
├── migrate_to_supabase.py← SQLite からの移行スクリプト（過去分・参考）
├── schema_supabase.sql   ← Supabase スキーマ定義
├── render.yaml           ← Render（Web Service + Cron）デプロイ設定
├── requirements.txt      ← Python 依存
├── index.html            ← フロントエンド（SPA・素のJS／CSS）
└── README.md
```

- **DB**: Supabase（PostgreSQL）。`hq_*` テーブル群に本部データ、`dx` スキーマに店頭/弁当注文。
- **部署(department)**: 弁当惣菜部・餅部・漬物部を1アプリで運用するマルチテナント構成。`hq_departments` に部署と設定(`config`)を持ち、`hq_members` を除く全 `hq_*` を `department_id` でスコープ。API は `?dept=<code>`（未指定時は弁当部）で部署を切替。部署ごとの材料費率・固定費・特売チャネル・機能フラグは `config` で制御。
  - ⚠️ **デプロイ順序**: スキーマ(`schema_supabase.sql`)を先に適用してからアプリを更新すること（`department_id` を含む主キー/ユニーク制約に依存するため）。
- **本番**: Render の Web Service（gunicorn）で配信。`/` で `index.html` を返し、`/api/*` で REST API。
- **環境変数**: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `APP_URL`（Cron用）, `CRON_SECRET`（任意）。

---

## ローカル起動

```bash
# 依存インストール
pip install -r requirements.txt

# .env に Supabase 認証情報を設定
cat > .env <<EOF
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_KEY=xxxx
EOF

# APIサーバー起動（5050ポート）
python app.py
```

ブラウザで `http://localhost:5050/` を開く。
（`index.html` をローカルで開いた場合は `API` が `http://localhost:5050/api` に向く）

---

## 部署・ロール

**部署は URL（入口）で決まる**。アプリ内での部署切替は行わない。

| 入口 URL | 部署 |
|---|---|
| `/`          | 弁当惣菜部（従来どおり・挙動不変） |
| `/mochi`     | 餅部 |
| `/tsukemono` | 漬物部 |

- 各 URL は同じ SPA を返し、フロントが `location.pathname` から部署を判定して、その部署のデータのみを表示・編集する（全 API 呼び出しに `?dept=<code>` を自動付与）。
- 部署の `config.features` に応じてメニューを出し分け（餅部・漬物部では「発注」「週間献立」を非表示＝コア機能のみ）。
- 起動時は**ロール選択**のみ（部署選択画面・部署スイッチャーは無し）。`sessionStorage` に保存。

- **👤 メンバー**: 日次業務（出荷指示書／実績入力／日報）＋ 週次・月次の閲覧。
- **🔧 管理者**: 上記に加えて、週間予定数入力／週間献立表／週間シフト予定／各種マスタの編集。

### 管理者 PIN（サーバー側検証 `POST /api/auth/verify-pin`）
- **本部PIN**: 環境変数 `HQ_ADMIN_PIN`（既定 `1234`）。どの部署の入口でも管理者になれる。
- **部署別PIN**: `hq_departments.config.admin_pin`。その部署の入口でのみ有効。
- PIN はクライアントに渡さない（`GET /api/departments` は `admin_pin` を除外して返す）。

---

## 主要画面

### 日次業務（全ロール）
| 画面 | 用途 |
|------|------|
| 📋 出荷指示書 | 当日の出荷計画を出荷先別に表示・印刷 |
| ✏️ 出荷実績入力 | 出荷数量・単価・シフト時間・天気・メモを入力。**マスタにない商品はその場で「✚ 新規商品」から登録可** |
| 📊 売上日報 | 売上・原価・人件費・経費・利益・人時売を自動集計。経費は月固定費を営業日で按分 |

### 週次・月次（全ロール）
| 画面 | 用途 |
|------|------|
| 🍱 週間献立 | カテゴリ × 月〜土 で献立を入力。`◀ 前の週へ` / `次の週へ ▶` で週送り。管理者のみ `📋 前の週の献立をコピー` |
| 📈 月次サマリ | 売上・利益・人時推移と品目別売上 |
| 📈 年次サマリ | 年間集計 |

#### 週間献立の行構成（カテゴリ）
- メイン（煮物） / 煮物① / 酢もの① / サラダ① / 天ぷら
- **デラックスメイン**（メイン肉・揚げ物・魚から選択／削除可）
- **メイン肉**（メイン肉・揚げ物から選択／削除可）
- **メイン魚**（魚サブカテゴリ）
- **NPOメイン**（メイン肉・揚げ物・魚から選択）
- **おかず行**: `＋ 行を追加` で何行でも追加可能（惣菜全件から選択／× で削除）

「天ぷら」〜「NPOメイン」のメイン系カテゴリは品名セルの背景色を変えて視認性アップ。

### 管理者専用
| 画面 | 用途 |
|------|------|
| 📋 週間予定数入力 | 商品 × 出荷先の出荷計画を一日 / 週間で入力。`◀ 前の週へ` `次の週へ ▶` `📋 前の週の予定をコピー` |
| 🍱 週間献立表 | 内部的に「週間献立」と同じ画面 |
| 📅 週間シフト予定 | メンバー × 日付の予定時間を入力。`◀ 前の週へ` `次の週へ ▶` `📋 前の週のシフトをコピー` |

### マスタ（管理者専用）
- ⚙️ 商品マスタ（カテゴリ / サブカテゴリ / 単価 / 有効・無効）
- 🏷 カテゴリマスタ
- 🏪 出荷先マスタ（並び順可変）
- 💴 単価表マスタ（出荷先×商品の販売単価・税抜／漬物部）
- 👤 メンバーマスタ（時給）

### 漬物部：製造 → 在庫 → 出荷 → 請求（`config.features.production`）
製造と出荷にタイムラグがある漬物部向けの専用フロー。`features.production` を持つ部署でのみ
表示され、弁当の売上フロー（出荷指示書／実績／日報／分析）は非表示になる。
マスタ以外（製造日報・在庫・出荷登録・請求書・月次/年次・出荷分析）はメンバーにも表示し、
各種マスタの編集は管理者のみ。

| 画面 | 用途 |
|------|------|
| 🏭 製造日報 | 自社製造数・委託入庫数・勤務時間(メンバー別)・天気・メモを入力し、「確定」で**在庫反映＋売上日報生成**。確定後も修正可（自動確定の対象外）。商品は**商品マスタの製造区分(自社製造/製造委託)**で各表に振り分け |
| 📦 在庫 | **基準日**時点の在庫一覧（製造日≦基準日・出荷日≦基準日で計算）。入庫／出荷済／在庫／引当／引当可に加え、製造日FIFOで**最古製造日・経過日数**と製造日別の在庫残明細（賞味期限の目安）を表示 |
| 🚚 出荷登録 | 登録日・納品予定日・請求先・納品先(マスタ選択 or 直接入力)を指定し、**複数商品をまとめて**数量入力 →「出荷」ボタンで確定（確定時に在庫から減算）。単価は単価表(請求先×商品)から自動入力。**請求先を選ぶと、その請求先の単価表に単価がある商品だけが選択候補**になる（加えて**送料(税10%)**をどの請求先でも選択可・初期0円）。在庫不足は警告のみ（登録は可能）。一覧は**状態フィルタ（すべて/登録済/出荷済）と並べ替え（納品予定が近い順／登録日が新しい順）**に対応 |
| 🧾 請求書 | 請求先×月で出荷済を集計し、税込の請求書を画面表示・印刷（PDF化）。商品は軽減税率8%、**送料は10%**で税区分ごとに集計。請求先住所と発行元（自社）情報（住所・電話・口座・ロゴ）を表記。明細列は 出荷日／商品名／納品先／数量／単価／金額。1請求先=1ページに収まるよう印刷を最適化 |
| 📈 月次/年次サマリ | 製造数ベースで集計。月次は惣菜部と同じ**日別明細（製造高・材料費・人件費・経費・差引利益・人時売）**＋商品別製造数。年次は商品×月の製造数マトリクス。`features.production` の部署では売上ベースに代えてこちらを表示 |
| 🚚 出荷分析 | 製造ベースとは別に**出荷ベースの売上分析**。月内の出荷（登録日ベース）を**日別明細（既定）／取引先別／商品別／納品先別**で集計（金額＝数量×単価・税抜） |

- 漬物部では **出荷先＝請求先**（`hq_channels`）として扱う。請求先に複数の **納品先**（`hq_delivery_destinations`）を紐づけられる（商社など）。固定はマスタ登録（名称・郵便番号・住所・電話番号）、単発は出荷登録で直接入力（`hq_shipments.dest_name` にスナップ保存、マスタ選択時は `dest_id` も保持）。
- 商品ごとに **製造区分** `hq_products.prod_type`（`manufacture`=自社製造 / `consignment`=製造委託）を持ち、製造日報の入力表を区分で分けて管理する（商品マスタで設定）。
- 請求先マスタ（出荷先マスタ）は漬物部のみ拡張表示：郵便番号・住所・電話番号・FAX番号・分類(`ctype`=商社/小売/生協/委託)・担当者・メールを保持（`hq_channels` の追加列）。
- 発行元（自社）情報は **自社情報マスタ** で編集し `hq_departments.config.issuer`（name/zip/address/phone/bank/logo_url）に保存。請求書の差出人として印字。
- 製造日報の **売上(製造高)＝自社製造数 × 商品マスタ単価**。委託入庫(`kind=consignment`)は在庫のみで売上対象外。
  人件費＝勤務時間×時給、材料費＝売上×材料費率、経費＝月固定費の営業日按分。`hq_daily_reports` に保存（売上日報）。
- `features.production` の部署は「確定後も修正可」のため、売上日報の**自動確定(cron/遅延)を行わない**。
- 在庫＝`hq_production`(入庫) の累計 − `hq_shipments`(status=shipped) の累計。出荷確定でのみ在庫が減る。
- 請求は `shipped_date` が対象月内の出荷済を集計（出荷登録時の `unit_price` をスナップショット保存）。
- 関連テーブル: `hq_production` / `hq_product_prices` / `hq_shipments` / `hq_delivery_destinations`。
- 関連API: `GET/POST /api/production` `/bulk`、`GET /api/inventory`、`GET/POST /api/product-prices` `/bulk`、`GET/POST/PUT/DELETE /api/shipments` `/bulk` `/<id>/ship`、`GET/POST/PUT/DELETE /api/delivery-destinations`、`GET/PUT /api/issuer`（発行元情報）、`GET /api/invoices`、`GET /api/production-summary`（製造数ベース月次/年次）、`GET /api/shipment-analysis?month=YYYY-MM`（出荷ベース分析：日別/取引先別/商品別/納品先別）。製造日報の確定は `POST /api/production/bulk` ×2 → `POST /api/shifts` → `POST /api/daily-info/<date>` → `POST /api/daily-reports/<date>/generate`。

---

## 売上・経費ロジック

- **材料費**: 売上 × 50%（暫定値）
- **経費（固定費）**: 月 300,000 円 ÷ その月の営業日数（祝日・日曜を除く、月固定）を切り上げ
  - 営業日数の祝日判定は `app.py` の `japanese_holidays()` で行う
- **人件費**: シフト実績（時間）× メンバーマスタの時給
- **差引利益** = 売上 − 材料費 − 人件費 − 経費
- **人時売上** = 売上 ÷ 総人時

---

## 日報の確定（finalize）

- 売上日報の `🔒 確定` を押すと当該日が確定状態になり、数値・明細は変更不可になる（天気・メモのみ編集可）。
- **自動確定 Cron**: `finalize_pending.py` を Render Cron が毎日 03:00 JST（18:00 UTC）に起動し、`POST /api/admin/finalize-pending` を叩いて過去日の未確定日報を一括 finalize。
- Cron は `APP_URL` を環境変数で参照。`CRON_SECRET` を設定すると `X-Cron-Secret` ヘッダで簡易認証。

---

## デプロイ（Render）

`render.yaml` で 2 サービスを定義。

```yaml
services:
  - type: web                    # Flask + gunicorn
    name: sozai-system
    startCommand: gunicorn app:app
  - type: cron                   # 日報自動確定
    name: finalize-daily-reports
    schedule: "0 18 * * *"       # 03:00 JST
    startCommand: python finalize_pending.py
```

Supabase の URL / Service Key、`APP_URL`、`ADMIN_PIN`（フロント側 `index.html`）を環境ごとに切り替えること。

---

## API（抜粋）

| メソッド・パス | 用途 |
|---|---|
| `GET /api/products` | 商品マスタ取得（`?include_inactive=1` で無効も含む） |
| `POST /api/products` | 商品追加（実績入力画面の「✚ 新規商品」からも呼ばれる） |
| `GET/POST /api/weekly-menus` | 週間献立の取得・保存（`?week_start=YYYY-MM-DD`） |
| `POST /api/weekly-menus/week-copy` | 週間献立を前週からコピー |
| `GET/POST /api/shipping-plans` `/bulk` | 出荷計画 |
| `POST /api/shipping-plans/week-copy` | 出荷計画を前週からコピー |
| `GET/POST /api/shipping-actuals` `/bulk` `/init` | 出荷実績 |
| `GET/POST /api/shifts` `/week` `/week-bulk` `/week-copy` | シフト |
| `GET/POST /api/daily-reports/<date>` `/generate` `/finalize` | 売上日報 |
| `POST /api/admin/finalize-pending` | 過去日の未確定日報を一括 finalize（Cron） |
| `GET /api/monthly-summary` `/yearly-summary` | 集計 |
| `GET /api/bento/orders` `/api/instore/orders` | 弁当注文・店頭注文（`dx` スキーマ） |

---

## 開発メモ

- フロントはビルドなしの素のJS／CSS。`index.html` 1枚に全 SPA を実装。
- DB スキーマ変更は `schema_supabase.sql` を Supabase 側で適用。
- 注文系（`dx` スキーマ）は `supabase-py` の挙動上、別 client（`sb_dx`）で分離している（`app.py` 冒頭コメント参照）。
- 過去 SQLite 環境からの移行は `migrate_to_supabase.py`（一回限り・参考用）。
