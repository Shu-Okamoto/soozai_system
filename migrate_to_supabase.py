"""
SQLite → Supabase データ移行スクリプト（requests版）

事前準備：
1. .env に SUPABASE_URL と SUPABASE_SERVICE_KEY を設定
2. Supabase SQL Editor で schema_supabase.sql を実行済みであること

実行方法：
  python migrate_to_supabase.py
"""
import sqlite3, os, json
import requests
from dotenv import load_dotenv

load_dotenv()

SQLITE_PATH   = os.path.join(os.path.dirname(__file__), 'soozai.db')
SUPABASE_URL  = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY  = os.environ.get('SUPABASE_SERVICE_KEY', '')

HEADERS = {
    'apikey':        SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type':  'application/json',
    'Prefer':        'resolution=merge-duplicates',
}

TABLE_MAP = [
    ('weekly_menus',  'hq_weekly_menus',  ['created_at']),
    ('daily_reports', 'hq_daily_reports', ['id']),
]

def upsert(table, rows, batch_size=100):
    url = f'{SUPABASE_URL}/rest/v1/{table}'
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i+batch_size]
        r = requests.post(url, headers=HEADERS, data=json.dumps(batch, default=str))
        if r.status_code not in (200, 201):
            raise Exception(f'HTTP {r.status_code}: {r.text[:200]}')

def migrate():
    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row

    for src_table, dst_table, exclude_cols in TABLE_MAP:
        try:
            rows = src.execute(f'SELECT * FROM {src_table}').fetchall()
        except Exception as e:
            print(f'  [{src_table}] スキップ: {e}')
            continue

        if not rows:
            print(f'  [{src_table}] 0件')
            continue

        data = [{k:v for k,v in dict(r).items() if k not in exclude_cols} for r in rows]
        try:
            upsert(dst_table, data)
            print(f'  [OK] {src_table} -> {dst_table}: {len(data)}件')
        except Exception as e:
            print(f'  [NG] {dst_table} error: {e}')

    src.close()
    print('\n移行完了')
    print('\n─────────────────────────────────────────')
    print('Supabase SQL Editorで以下を実行してシーケンスをリセット:')
    print('─────────────────────────────────────────')
    for t in ['hq_products','hq_channels','hq_members','hq_weekly_menus',
              'hq_shipping_plans','hq_shipping_actuals','hq_shifts','hq_shift_plans']:
        print(f"SELECT setval('{t}_id_seq', COALESCE((SELECT MAX(id) FROM {t}), 1));")

if __name__ == '__main__':
    if not os.path.exists(SQLITE_PATH):
        print(f'エラー: {SQLITE_PATH} が見つかりません')
        exit(1)
    if not SUPABASE_URL or not SUPABASE_KEY:
        print('エラー: .env に SUPABASE_URL / SUPABASE_SERVICE_KEY を設定してください')
        exit(1)

    print(f'移行元: {SQLITE_PATH}')
    print(f'移行先: {SUPABASE_URL}')
    print('─────────────────────────────────────────')
    migrate()
