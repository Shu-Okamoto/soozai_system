from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import os, calendar, math
from datetime import date, timedelta, datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.environ.get('DATABASE_URL', '')
MONTHLY_FIXED_COST = 300000

# ─── 祝日・経費計算 ────────────────────────────
_SHUNBUN = {2023:21,2024:20,2025:20,2026:20,2027:21,2028:20,2029:20,2030:20}
_SHUBUN  = {2023:23,2024:22,2025:23,2026:23,2027:23,2028:22,2029:23,2030:23}

def japanese_holidays(year):
    h = set()
    for m,d in [(1,1),(2,11),(2,23),(4,29),(5,3),(5,4),(5,5),(8,11),(11,3),(11,23)]:
        h.add(date(year,m,d))
    h.add(date(year,3,_SHUNBUN.get(year,20)))
    h.add(date(year,9,_SHUBUN.get(year,23)))
    def nth_mon(y,m,n):
        d0=date(y,m,1); return d0+timedelta(days=(7-d0.weekday())%7)+timedelta(weeks=n-1)
    h.add(nth_mon(year,1,2)); h.add(nth_mon(year,7,3))
    h.add(nth_mon(year,9,3)); h.add(nth_mon(year,10,2))
    h.update({hd+timedelta(days=1) for hd in h if hd.weekday()==6})
    return h

def calc_working_days(year, month):
    holidays = japanese_holidays(year)
    _, days = calendar.monthrange(year, month)
    return sum(1 for d in range(1,days+1)
               if date(year,month,d).weekday()!=6 and date(year,month,d) not in holidays)

def daily_expense(target_date_str):
    dt = datetime.strptime(target_date_str, '%Y-%m-%d')
    wd = calc_working_days(dt.year, dt.month)
    return math.ceil(MONTHLY_FIXED_COST / wd) if wd > 0 else MONTHLY_FIXED_COST

# ─── DB接続 ────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL)

def ensure_tables():
    conn = get_db()
    cur  = conn.cursor()
    tables = [
        '''CREATE TABLE IF NOT EXISTS hq_products (
            id          BIGSERIAL PRIMARY KEY,
            name        TEXT UNIQUE NOT NULL,
            price       INTEGER DEFAULT 0,
            category    TEXT DEFAULT '',
            subcategory TEXT DEFAULT '',
            active      INTEGER DEFAULT 1
        )''',
        '''CREATE TABLE IF NOT EXISTS hq_channels (
            id         BIGSERIAL PRIMARY KEY,
            name       TEXT UNIQUE NOT NULL,
            sort_order INTEGER DEFAULT 0,
            active     INTEGER DEFAULT 1
        )''',
        '''CREATE TABLE IF NOT EXISTS hq_weekly_menus (
            id          BIGSERIAL PRIMARY KEY,
            week_start  TEXT NOT NULL,
            day_of_week INTEGER,
            category    TEXT,
            menu_name   TEXT
        )''',
        '''CREATE TABLE IF NOT EXISTS hq_shipping_plans (
            id          BIGSERIAL PRIMARY KEY,
            date        TEXT NOT NULL,
            product_id  INTEGER,
            channel_id  INTEGER,
            planned_qty INTEGER DEFAULT 0,
            note        TEXT DEFAULT '',
            updated_at  TIMESTAMP DEFAULT NOW(),
            UNIQUE(date,product_id,channel_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS hq_shipping_actuals (
            id            BIGSERIAL PRIMARY KEY,
            date          TEXT NOT NULL,
            product_id    INTEGER,
            channel_id    INTEGER,
            actual_qty    INTEGER DEFAULT 0,
            actual_amount INTEGER DEFAULT 0,
            updated_at    TIMESTAMP DEFAULT NOW(),
            UNIQUE(date,product_id,channel_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS hq_shifts (
            id          BIGSERIAL PRIMARY KEY,
            date        TEXT NOT NULL,
            member_name TEXT NOT NULL,
            hours       REAL DEFAULT 0,
            updated_at  TIMESTAMP DEFAULT NOW(),
            UNIQUE(date,member_name)
        )''',
        '''CREATE TABLE IF NOT EXISTS hq_shift_plans (
            id            BIGSERIAL PRIMARY KEY,
            date          TEXT NOT NULL,
            member_name   TEXT NOT NULL,
            planned_hours REAL DEFAULT 0,
            UNIQUE(date,member_name)
        )''',
        '''CREATE TABLE IF NOT EXISTS hq_daily_reports (
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
            updated_at         TIMESTAMP DEFAULT NOW()
        )''',
        '''CREATE TABLE IF NOT EXISTS hq_members (
            id          BIGSERIAL PRIMARY KEY,
            name        TEXT UNIQUE NOT NULL,
            hourly_wage INTEGER DEFAULT 0,
            active      INTEGER DEFAULT 1
        )''',
    ]
    for sql in tables:
        cur.execute(sql)
    conn.commit()
    conn.close()

ensure_tables()

# ─── フロントエンド配信 ────────────────────────
@app.route('/')
def index():
    return send_file(os.path.join(os.path.dirname(__file__), 'index.html'))

# ─── 商品マスタ ───────────────────────────────
@app.route('/api/products', methods=['GET'])
def get_products():
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM hq_products WHERE active=1 ORDER BY category,id')
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/products', methods=['POST'])
def add_product():
    d    = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute('INSERT INTO hq_products(name,price,category,subcategory) VALUES(%s,%s,%s,%s)',
                (d['name'], d['price'], d['category'], d.get('subcategory','')))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/products/<int:pid>', methods=['PUT'])
def update_product(pid):
    d    = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute('UPDATE hq_products SET name=%s,price=%s,category=%s,subcategory=%s,active=%s WHERE id=%s',
                (d['name'], d['price'], d['category'], d.get('subcategory',''), d.get('active',1), pid))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

# ─── 出荷先マスタ ─────────────────────────────
@app.route('/api/channels', methods=['GET'])
def get_channels():
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM hq_channels WHERE active=1 ORDER BY sort_order')
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/channels', methods=['POST'])
def add_channel():
    d    = request.json
    conn = get_db()
    cur  = conn.cursor()
    cur.execute('SELECT COALESCE(MAX(sort_order),0) FROM hq_channels')
    max_order = cur.fetchone()[0]
    cur.execute('INSERT INTO hq_channels(name,sort_order) VALUES(%s,%s)', (d['name'], max_order+1))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/channels/<int:cid>', methods=['PUT'])
def update_channel(cid):
    d    = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute('UPDATE hq_channels SET name=%s,sort_order=%s,active=%s WHERE id=%s',
                (d['name'], d.get('sort_order',0), d.get('active',1), cid))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

# ─── 週間献立 ──────────────────────────────────
@app.route('/api/weekly-menus', methods=['GET'])
def get_weekly_menus():
    week_start = request.args.get('week_start')
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM hq_weekly_menus WHERE week_start=%s ORDER BY day_of_week,id', (week_start,))
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/weekly-menus', methods=['POST'])
def save_weekly_menus():
    d    = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute('DELETE FROM hq_weekly_menus WHERE week_start=%s', (d['week_start'],))
    for m in d['menus']:
        if m.get('menu_name','').strip():
            cur.execute('INSERT INTO hq_weekly_menus(week_start,day_of_week,category,menu_name) VALUES(%s,%s,%s,%s)',
                        (d['week_start'], m['day_of_week'], m['category'], m['menu_name']))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/weekly-menus/week-copy', methods=['POST'])
def copy_weekly_menus():
    d    = request.json
    src, dst = d['src_start'], d['dst_start']
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('DELETE FROM hq_weekly_menus WHERE week_start=%s', (dst,))
    cur.execute('SELECT * FROM hq_weekly_menus WHERE week_start=%s', (src,))
    rows = cur.fetchall()
    for r in rows:
        cur.execute('INSERT INTO hq_weekly_menus(week_start,day_of_week,category,menu_name) VALUES(%s,%s,%s,%s)',
                    (dst, r['day_of_week'], r['category'], r['menu_name']))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'copied': len(rows)})

# ─── 出荷指示書（計画）────────────────────────
@app.route('/api/shipping-plans', methods=['GET'])
def get_shipping_plans():
    date_from = request.args.get('date_from')
    date_to   = request.args.get('date_to', date_from)
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT sp.*, p.name as product_name, p.price, p.category,
               c.name as channel_name, c.sort_order
        FROM hq_shipping_plans sp
        JOIN hq_products p ON sp.product_id=p.id
        JOIN hq_channels c ON sp.channel_id=c.id
        WHERE sp.date BETWEEN %s AND %s
        ORDER BY sp.date, p.category, p.id, c.sort_order
    ''', (date_from, date_to))
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/shipping-plans', methods=['POST'])
def save_shipping_plan():
    d    = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute('''
        INSERT INTO hq_shipping_plans(date,product_id,channel_id,planned_qty,note)
        VALUES(%s,%s,%s,%s,%s)
        ON CONFLICT(date,product_id,channel_id) DO UPDATE SET
            planned_qty=EXCLUDED.planned_qty, note=EXCLUDED.note, updated_at=NOW()
    ''', (d['date'], d['product_id'], d['channel_id'], d['planned_qty'], d.get('note','')))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/shipping-plans/bulk', methods=['POST'])
def bulk_save_plans():
    items = request.json
    conn  = get_db(); cur = conn.cursor()
    for d in items:
        cur.execute('''
            INSERT INTO hq_shipping_plans(date,product_id,channel_id,planned_qty,note)
            VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT(date,product_id,channel_id) DO UPDATE SET
                planned_qty=EXCLUDED.planned_qty, updated_at=NOW()
        ''', (d['date'],d['product_id'],d['channel_id'],d['planned_qty'],d.get('note','')))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'count': len(items)})

@app.route('/api/shipping-plans/week-copy', methods=['POST'])
def copy_week_plan():
    d         = request.json
    src_from  = d['src_from']; src_to = d['src_to']; dst_from = d['dst_from']
    src_start = datetime.strptime(src_from, '%Y-%m-%d')
    dst_start = datetime.strptime(dst_from, '%Y-%m-%d')
    diff      = (dst_start - src_start).days
    conn      = get_db()
    cur       = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM hq_shipping_plans WHERE date BETWEEN %s AND %s', (src_from, src_to))
    rows = cur.fetchall()
    for r in rows:
        new_date = (datetime.strptime(r['date'],'%Y-%m-%d') + timedelta(days=diff)).strftime('%Y-%m-%d')
        cur.execute('''
            INSERT INTO hq_shipping_plans(date,product_id,channel_id,planned_qty)
            VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING
        ''', (new_date, r['product_id'], r['channel_id'], r['planned_qty']))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'copied': len(rows)})

# ─── 出荷実績 ─────────────────────────────────
@app.route('/api/shipping-actuals', methods=['GET'])
def get_shipping_actuals():
    target_date = request.args.get('date')
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT sa.*, p.name as product_name, p.price, p.category,
               c.name as channel_name, c.sort_order
        FROM hq_shipping_actuals sa
        JOIN hq_products p ON sa.product_id=p.id
        JOIN hq_channels c ON sa.channel_id=c.id
        WHERE sa.date=%s ORDER BY p.category, p.id, c.sort_order
    ''', (target_date,))
    rows = cur.fetchall()
    cur.execute('SELECT product_id, channel_id, planned_qty FROM hq_shipping_plans WHERE date=%s', (target_date,))
    plans    = cur.fetchall()
    plan_map = {(r['product_id'],r['channel_id']): r['planned_qty'] for r in plans}
    conn.close()
    result = []
    for r in rows:
        row = dict(r)
        row['planned_qty'] = plan_map.get((r['product_id'],r['channel_id']), 0)
        result.append(row)
    return jsonify(result)

@app.route('/api/shipping-actuals/init', methods=['POST'])
def init_actuals_from_plan():
    target_date = request.json.get('date')
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT sp.*, p.price FROM hq_shipping_plans sp JOIN hq_products p ON sp.product_id=p.id WHERE sp.date=%s AND sp.planned_qty>0', (target_date,))
    plans = cur.fetchall()
    for p in plans:
        cur.execute('''
            INSERT INTO hq_shipping_actuals(date,product_id,channel_id,actual_qty,actual_amount)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
        ''', (target_date, p['product_id'], p['channel_id'], p['planned_qty'], p['planned_qty']*p['price']))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/shipping-actuals/bulk', methods=['POST'])
def bulk_save_actuals():
    items = request.json
    conn  = get_db()
    cur   = conn.cursor(cursor_factory=RealDictCursor)
    for d in items:
        cur.execute('SELECT price FROM hq_products WHERE id=%s', (d['product_id'],))
        row   = cur.fetchone()
        price = row['price'] if row else 0
        amt   = d['actual_qty'] * price
        cur.execute('''
            INSERT INTO hq_shipping_actuals(date,product_id,channel_id,actual_qty,actual_amount)
            VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT(date,product_id,channel_id) DO UPDATE SET
                actual_qty=EXCLUDED.actual_qty, actual_amount=EXCLUDED.actual_amount, updated_at=NOW()
        ''', (d['date'],d['product_id'],d['channel_id'],d['actual_qty'],amt))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

# ─── メンバーマスタ ───────────────────────────
@app.route('/api/members', methods=['GET'])
def get_members():
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM hq_members WHERE active=1 ORDER BY id')
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/members', methods=['POST'])
def add_member():
    d    = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute('INSERT INTO hq_members(name,hourly_wage) VALUES(%s,%s)', (d['name'], d.get('hourly_wage',0)))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/members/<int:mid>', methods=['PUT'])
def update_member(mid):
    d    = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute('UPDATE hq_members SET name=%s,hourly_wage=%s,active=%s WHERE id=%s',
                (d['name'], d.get('hourly_wage',0), d.get('active',1), mid))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

# ─── シフト ────────────────────────────────────
@app.route('/api/shifts', methods=['GET'])
def get_shifts():
    target_date = request.args.get('date')
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM hq_shifts WHERE date=%s ORDER BY member_name', (target_date,))
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/shifts', methods=['POST'])
def save_shifts():
    d    = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute('DELETE FROM hq_shifts WHERE date=%s', (d['date'],))
    for s in d['shifts']:
        if s.get('member_name','').strip() and s.get('hours',0) > 0:
            cur.execute('INSERT INTO hq_shifts(date,member_name,hours) VALUES(%s,%s,%s)',
                        (d['date'], s['member_name'], s['hours']))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

# ─── シフト週次管理（予定） ────────────────────
@app.route('/api/shift-plans', methods=['GET'])
def get_shift_plans():
    target_date = request.args.get('date')
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM hq_shift_plans WHERE date=%s ORDER BY member_name', (target_date,))
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/shifts/week', methods=['GET'])
def get_shifts_week():
    week_start = request.args.get('week_start')
    start  = datetime.strptime(week_start, '%Y-%m-%d')
    dates  = [(start + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    conn   = get_db()
    cur    = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM hq_shift_plans WHERE date = ANY(%s)', (dates,))
    rows   = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/shifts/week-bulk', methods=['POST'])
def save_shifts_week_bulk():
    d      = request.json
    start  = datetime.strptime(d['week_start'], '%Y-%m-%d')
    dates  = [(start + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    conn   = get_db(); cur = conn.cursor()
    cur.execute('DELETE FROM hq_shift_plans WHERE date = ANY(%s)', (dates,))
    for s in d.get('shifts', []):
        if s.get('hours', 0) > 0:
            cur.execute('INSERT INTO hq_shift_plans(date,member_name,planned_hours) VALUES(%s,%s,%s)',
                        (s['date'], s['member_name'], s['hours']))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/shifts/week-copy', methods=['POST'])
def copy_shifts_week():
    d         = request.json
    src       = datetime.strptime(d['src_start'], '%Y-%m-%d')
    dst       = datetime.strptime(d['dst_start'], '%Y-%m-%d')
    src_dates = [(src + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    dst_dates = [(dst + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    conn      = get_db()
    cur       = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('DELETE FROM hq_shift_plans WHERE date = ANY(%s)', (dst_dates,))
    cur.execute('SELECT * FROM hq_shift_plans WHERE date = ANY(%s)', (src_dates,))
    rows = cur.fetchall()
    for r in rows:
        idx = src_dates.index(r['date'])
        cur.execute('INSERT INTO hq_shift_plans(date,member_name,planned_hours) VALUES(%s,%s,%s)',
                    (dst_dates[idx], r['member_name'], r['planned_hours']))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'copied': len(rows)})

# ─── 日報 ─────────────────────────────────────
def calc_daily_report(target_date, conn):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT sa.actual_amount, c.name as channel_name
        FROM hq_shipping_actuals sa JOIN hq_channels c ON sa.channel_id=c.id
        WHERE sa.date=%s
    ''', (target_date,))
    actuals   = cur.fetchall()
    total     = sum(r['actual_amount'] for r in actuals)
    west      = sum(r['actual_amount'] for r in actuals if r['channel_name']=='西店')
    south     = sum(r['actual_amount'] for r in actuals if r['channel_name']=='南店')
    other     = total - west - south

    cur.execute('SELECT SUM(hours) AS h FROM hq_shifts WHERE date=%s', (target_date,))
    total_hours = cur.fetchone()['h'] or 0

    cur.execute('''
        SELECT SUM(sa.actual_amount) AS a FROM hq_shipping_actuals sa
        JOIN hq_products p ON sa.product_id=p.id
        WHERE sa.date=%s AND p.name LIKE %s
    ''', (target_date, 'NPO%'))
    npo            = cur.fetchone()['a'] or 0
    total_with_npo = total + int(npo * 0.08)
    material       = int(total_with_npo * 0.5)
    expense        = daily_expense(target_date)

    cur.execute('''
        SELECT s.hours, COALESCE(m.hourly_wage,0) AS wage
        FROM hq_shifts s LEFT JOIN hq_members m ON s.member_name=m.name
        WHERE s.date=%s
    ''', (target_date,))
    labor_cost  = sum(int(r['hours']*r['wage']) for r in cur.fetchall())
    profit      = total_with_npo - material - labor_cost - expense
    labor_prod  = (total_with_npo / total_hours) if total_hours > 0 else 0

    return {
        'date': target_date, 'total_sales': total, 'material_cost': material,
        'labor_cost': labor_cost, 'expense': expense, 'profit': profit,
        'labor_productivity': round(labor_prod,1), 'total_hours': total_hours,
        'west_sales': west, 'south_sales': south, 'other_sales': other,
    }

@app.route('/api/daily-reports/<date_str>', methods=['GET'])
def get_daily_report(date_str):
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM hq_daily_reports WHERE date=%s', (date_str,))
    row    = cur.fetchone()
    result = dict(row) if row else calc_daily_report(date_str, conn)
    cur.execute('''
        SELECT sa.product_id, sa.channel_id, sa.actual_qty, sa.actual_amount,
               p.name as product_name, p.category, p.price,
               c.name as channel_name, c.sort_order
        FROM hq_shipping_actuals sa
        JOIN hq_products p ON sa.product_id=p.id
        JOIN hq_channels c ON sa.channel_id=c.id
        WHERE sa.date=%s AND sa.actual_qty>0
        ORDER BY p.category, p.id, c.sort_order
    ''', (date_str,))
    result['actuals_detail'] = [dict(r) for r in cur.fetchall()]
    cur.execute('SELECT * FROM hq_channels WHERE active=1 ORDER BY sort_order')
    result['channels'] = [dict(r) for r in cur.fetchall()]
    cur.execute('SELECT * FROM hq_shifts WHERE date=%s ORDER BY member_name', (date_str,))
    result['shifts'] = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(result)

@app.route('/api/daily-info/<date_str>', methods=['POST'])
def save_daily_info(date_str):
    d    = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute('''
        INSERT INTO hq_daily_reports(date,weather,separate_orders,note,
            total_sales,material_cost,labor_cost,expense,profit,labor_productivity,total_hours,
            west_sales,south_sales,other_sales)
        VALUES(%s,%s,%s,%s, 0,0,0,0,0,0,0, 0,0,0)
        ON CONFLICT(date) DO UPDATE SET
            weather=EXCLUDED.weather,
            separate_orders=EXCLUDED.separate_orders,
            note=EXCLUDED.note,
            updated_at=NOW()
    ''', (date_str, d.get('weather',''), d.get('separate_orders',0), d.get('note','')))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/daily-reports/<date_str>', methods=['POST'])
def save_daily_report(date_str):
    d    = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute('''
        INSERT INTO hq_daily_reports
            (date,weather,total_sales,separate_orders,material_cost,labor_cost,
             expense,profit,labor_productivity,total_hours,west_sales,south_sales,other_sales,note)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(date) DO UPDATE SET
            weather=%s,total_sales=%s,separate_orders=%s,material_cost=%s,labor_cost=%s,
            expense=%s,profit=%s,labor_productivity=%s,total_hours=%s,
            west_sales=%s,south_sales=%s,other_sales=%s,note=%s,updated_at=NOW()
    ''', (date_str, d.get('weather'), d.get('total_sales',0), d.get('separate_orders',0),
          d.get('material_cost',0), d.get('labor_cost',0), d.get('expense',0),
          d.get('profit',0), d.get('labor_productivity',0), d.get('total_hours',0),
          d.get('west_sales',0), d.get('south_sales',0), d.get('other_sales',0), d.get('note',''),
          d.get('weather'), d.get('total_sales',0), d.get('separate_orders',0),
          d.get('material_cost',0), d.get('labor_cost',0), d.get('expense',0),
          d.get('profit',0), d.get('labor_productivity',0), d.get('total_hours',0),
          d.get('west_sales',0), d.get('south_sales',0), d.get('other_sales',0), d.get('note','')))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/daily-reports/<date_str>/generate', methods=['POST'])
def generate_daily_report(date_str):
    conn    = get_db()
    calc    = calc_daily_report(date_str, conn)
    weather = request.json.get('weather','') if request.json else ''
    note    = request.json.get('note','')    if request.json else ''
    cur     = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''
        SELECT SUM(sa.actual_amount) AS a FROM hq_shipping_actuals sa
        JOIN hq_products p ON sa.product_id=p.id
        WHERE sa.date=%s AND p.name LIKE %s
    ''', (date_str, '別注%'))
    sep = cur.fetchone()['a'] or 0
    cur.execute('''
        INSERT INTO hq_daily_reports
            (date,weather,total_sales,separate_orders,material_cost,labor_cost,
             expense,profit,labor_productivity,total_hours,west_sales,south_sales,other_sales,note)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(date) DO UPDATE SET
            weather=%s,total_sales=%s,separate_orders=%s,material_cost=%s,labor_cost=%s,
            expense=%s,profit=%s,labor_productivity=%s,total_hours=%s,
            west_sales=%s,south_sales=%s,other_sales=%s,updated_at=NOW()
    ''', (date_str, weather, calc['total_sales'], int(sep),
          calc['material_cost'], calc['labor_cost'], calc['expense'],
          calc['profit'], calc['labor_productivity'], calc['total_hours'],
          calc['west_sales'], calc['south_sales'], calc['other_sales'], note,
          weather, calc['total_sales'], int(sep),
          calc['material_cost'], calc['labor_cost'], calc['expense'],
          calc['profit'], calc['labor_productivity'], calc['total_hours'],
          calc['west_sales'], calc['south_sales'], calc['other_sales']))
    conn.commit(); conn.close()
    calc['separate_orders'] = int(sep)
    return jsonify({'ok': True, **calc})

# ─── 月次サマリ ────────────────────────────────
@app.route('/api/monthly-summary', methods=['GET'])
def monthly_summary():
    ym   = request.args.get('month')
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM hq_daily_reports WHERE date LIKE %s ORDER BY date", (ym+'%',))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return jsonify({'month': ym, 'days': [], 'summary': {}})
    days        = [dict(r) for r in rows]
    total_sales = sum(d['total_sales'] for d in days)
    total_labor = sum(d['labor_cost']  for d in days)
    total_profit= sum(d['profit']      for d in days)
    total_hours = sum(d['total_hours'] for d in days)
    avg_lp      = (total_sales / total_hours) if total_hours > 0 else 0
    summary = {
        'total_sales':     total_sales,
        'total_profit':    total_profit,
        'profit_rate':     round(total_profit/total_sales*100,1) if total_sales else 0,
        'total_labor':     total_labor,
        'labor_rate':      round(total_labor/total_sales*100,1)  if total_sales else 0,
        'op_days':         len(days),
        'avg_daily_sales': int(total_sales/len(days)) if days else 0,
        'avg_labor_prod':  round(avg_lp,0),
        'west_sales':      sum(d['west_sales']  for d in days),
        'south_sales':     sum(d['south_sales'] for d in days),
        'other_sales':     sum(d['other_sales'] for d in days),
    }
    return jsonify({'month': ym, 'days': days, 'summary': summary})

# ─── 印刷用データ ─────────────────────────────
@app.route('/api/print/shipping-plan', methods=['GET'])
def print_shipping_plan():
    target_date = request.args.get('date')
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM hq_channels WHERE active=1 ORDER BY sort_order')
    channels = cur.fetchall()
    cur.execute('SELECT * FROM hq_products WHERE active=1 ORDER BY category,id')
    products = cur.fetchall()
    cur.execute('SELECT product_id,channel_id,planned_qty,note FROM hq_shipping_plans WHERE date=%s', (target_date,))
    plans    = cur.fetchall()
    plan_map = {(r['product_id'],r['channel_id']): r['planned_qty'] for r in plans}
    note_map = {r['product_id']: r['note'] for r in plans if r['note']}
    dt     = datetime.strptime(target_date, '%Y-%m-%d')
    monday = (dt - timedelta(days=dt.weekday())).strftime('%Y-%m-%d')
    dow    = dt.weekday() + 1
    cur.execute('SELECT * FROM hq_weekly_menus WHERE week_start=%s AND day_of_week=%s ORDER BY category', (monday, dow))
    menus = cur.fetchall()
    conn.close()
    result = {'date': target_date, 'channels': [dict(c) for c in channels], 'products': [], 'menus': [dict(m) for m in menus]}
    for p in products:
        row = {'id': p['id'], 'name': p['name'], 'price': p['price'], 'category': p['category'],
               'note': note_map.get(p['id'],''), 'quantities': {}}
        for c in channels:
            row['quantities'][c['id']] = plan_map.get((p['id'],c['id']),0)
        row['total'] = sum(row['quantities'].values())
        if row['total'] > 0:
            result['products'].append(row)
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True, port=5050)
