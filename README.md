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

## ロール

起動時にロール選択画面が表示される（`sessionStorage` に保存）。

- **👤 メンバー**: 日次業務（出荷指示書／実績入力／日報）＋ 週次・月次の閲覧。
- **🔧 管理者**: 上記に加えて、週間予定数入力／週間献立表／週間シフト予定／各種マスタの編集。

管理者 PIN は `index.html` の `ADMIN_PIN` 定数（既定 `1234`）。

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
- 👤 メンバーマスタ（時給）

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
