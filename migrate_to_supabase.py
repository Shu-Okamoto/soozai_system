"""
SQLite → Supabase(PostgreSQL) データ移行スクリプト
実行: python migrate_to_supabase.py
"""
import sqlite3, psycopg2, os
from dotenv import load_dotenv

load_dotenv()

SQLITE_PATH  = os.path.join(os.path.dirname(__file__), 'soozai.db')
DATABASE_URL = os.environ.get('DATABASE_URL', '')

TABLE_MAP = {
    'products':         'hq_products',
    'channels':         'hq_channels',
    'weekly_menus':     'hq_weekly_menus',
    'shipping_plans':   'hq_shipping_plans',
    'shipping_actuals': 'hq_shipping_actuals',
    'shifts':           'hq_shifts',
    'shift_plans':      'hq_shift_plans',
    'daily_reports':    'hq_daily_reports',
    'members':          'hq_members',
}

def migrate():
    src  = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row
    dst  = psycopg2.connect(DATABASE_URL)
    dcur = dst.cursor()

    for src_table, dst_table in TABLE_MAP.items():
        rows = src.execute(f'SELECT * FROM {src_table}').fetchall()
        if not rows:
            print(f'  {src_table}: 0件 スキップ')
            continue
        cols   = rows[0].keys()
        ph     = ','.join(['%s'] * len(cols))
        col_str = ','.join(cols)
        dcur.execute(f'DELETE FROM {dst_table}')
        for r in rows:
            dcur.execute(f'INSERT INTO {dst_table} ({col_str}) VALUES ({ph})', tuple(r))
        print(f'  {src_table} → {dst_table}: {len(rows)}件')

    dst.commit()
    src.close(); dst.close()
    print('移行完了')

if __name__ == '__main__':
    print('SQLite → Supabase 移行開始...')
    migrate()
