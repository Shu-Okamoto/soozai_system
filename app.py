from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import sqlite3, json, io, os, calendar, math
from datetime import date, timedelta, datetime

MONTHLY_FIXED_COST = 300000

# 春分・秋分の日（近年の実際の日付）
_SHUNBUN = {2023:21,2024:20,2025:20,2026:20,2027:21,2028:20,2029:20,2030:20}
_SHUBUN  = {2023:23,2024:22,2025:23,2026:23,2027:23,2028:22,2029:23,2030:23}

def japanese_holidays(year):
    h = set()
    # 固定祝日
    for m,d in [(1,1),(2,11),(2,23),(4,29),(5,3),(5,4),(5,5),(8,11),(11,3),(11,23)]:
        h.add(date(year,m,d))
    # 春分・秋分
    h.add(date(year,3,_SHUNBUN.get(year,20)))
    h.add(date(year,9,_SHUBUN.get(year,23)))
    # ハッピーマンデー
    def nth_mon(y,m,n):
        d0=date(y,m,1); return d0+timedelta(days=(7-d0.weekday())%7)+timedelta(weeks=n-1)
    h.add(nth_mon(year,1,2))   # 成人の日
    h.add(nth_mon(year,7,3))   # 海の日
    h.add(nth_mon(year,9,3))   # 敬老の日
    h.add(nth_mon(year,10,2))  # スポーツの日
    # 振替休日
    sub = {hd+timedelta(days=1) for hd in h if hd.weekday()==6}
    h.update(sub)
    return h

def calc_working_days(year, month):
    """月の営業日数（日曜・祝日除く）"""
    holidays = japanese_holidays(year)
    _, days = calendar.monthrange(year, month)
    return sum(1 for d in range(1,days+1)
               if date(year,month,d).weekday()!=6 and date(year,month,d) not in holidays)

def daily_expense(target_date_str):
    """家賃光熱費 / 月間営業日数（切り上げ）"""
    dt = datetime.strptime(target_date_str, '%Y-%m-%d')
    wd = calc_working_days(dt.year, dt.month)
    return math.ceil(MONTHLY_FIXED_COST / wd) if wd > 0 else MONTHLY_FIXED_COST

app = Flask(__name__)
CORS(app)
# Renderの永続ディスク(/data)があればそちらを使用、なければローカル
_data_dir = '/data' if os.path.isdir('/data') else os.path.dirname(__file__)
DB = os.path.join(_data_dir, 'soozai.db')

@app.route('/')
def index():
    return send_file(os.path.join(os.path.dirname(__file__), 'index.html'))

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_tables():
    db = get_db()
    db.execute('''CREATE TABLE IF NOT EXISTS members (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE NOT NULL,
        hourly_wage INTEGER DEFAULT 0,
        active      INTEGER DEFAULT 1
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS shift_plans (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        date         TEXT NOT NULL,
        member_name  TEXT NOT NULL,
        planned_hours REAL DEFAULT 0,
        UNIQUE(date, member_name)
    )''')
    try:
        db.execute('ALTER TABLE products ADD COLUMN subcategory TEXT DEFAULT ""')
    except Exception:
        pass
    db.commit(); db.close()

ensure_tables()


# ─── 商品マスタ ───────────────────────────────
@app.route('/api/products', methods=['GET'])
def get_products():
    db = get_db()
    rows = db.execute('SELECT * FROM products WHERE active=1 ORDER BY category,id').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/products', methods=['POST'])
def add_product():
    d = request.json
    db = get_db()
    db.execute('INSERT INTO products(name,price,category,subcategory) VALUES(?,?,?,?)',
               (d['name'], d['price'], d['category'], d.get('subcategory','')))
    db.commit(); db.close()
    return jsonify({'ok': True})

@app.route('/api/products/<int:pid>', methods=['PUT'])
def update_product(pid):
    d = request.json
    db = get_db()
    db.execute('UPDATE products SET name=?,price=?,category=?,subcategory=?,active=? WHERE id=?',
               (d['name'], d['price'], d['category'], d.get('subcategory',''), d.get('active',1), pid))
    db.commit(); db.close()
    return jsonify({'ok': True})

# ─── 出荷先マスタ ─────────────────────────────
@app.route('/api/channels', methods=['GET'])
def get_channels():
    db = get_db()
    rows = db.execute('SELECT * FROM channels WHERE active=1 ORDER BY sort_order').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/channels', methods=['POST'])
def add_channel():
    d = request.json
    db = get_db()
    max_order = db.execute('SELECT COALESCE(MAX(sort_order),0) FROM channels').fetchone()[0]
    db.execute('INSERT INTO channels(name,sort_order) VALUES(?,?)', (d['name'], max_order + 1))
    db.commit(); db.close()
    return jsonify({'ok': True})

@app.route('/api/channels/<int:cid>', methods=['PUT'])
def update_channel(cid):
    d = request.json
    db = get_db()
    db.execute('UPDATE channels SET name=?,sort_order=?,active=? WHERE id=?',
               (d['name'], d.get('sort_order', 0), d.get('active', 1), cid))
    db.commit(); db.close()
    return jsonify({'ok': True})

# ─── 週間献立 ──────────────────────────────────
@app.route('/api/weekly-menus', methods=['GET'])
def get_weekly_menus():
    week_start = request.args.get('week_start')
    db = get_db()
    rows = db.execute('SELECT * FROM weekly_menus WHERE week_start=? ORDER BY day_of_week,id', (week_start,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/weekly-menus', methods=['POST'])
def save_weekly_menus():
    d = request.json  # {week_start, menus:[{day_of_week, category, menu_name}]}
    db = get_db()
    db.execute('DELETE FROM weekly_menus WHERE week_start=?', (d['week_start'],))
    for m in d['menus']:
        if m.get('menu_name','').strip():
            db.execute('INSERT INTO weekly_menus(week_start,day_of_week,category,menu_name) VALUES(?,?,?,?)',
                       (d['week_start'], m['day_of_week'], m['category'], m['menu_name']))
    db.commit(); db.close()
    return jsonify({'ok': True})

# ─── 出荷指示書（計画）────────────────────────
@app.route('/api/shipping-plans', methods=['GET'])
def get_shipping_plans():
    date_from = request.args.get('date_from')
    date_to   = request.args.get('date_to', date_from)
    db = get_db()
    rows = db.execute('''
        SELECT sp.*, p.name as product_name, p.price, p.category,
               c.name as channel_name, c.sort_order
        FROM shipping_plans sp
        JOIN products p ON sp.product_id=p.id
        JOIN channels c ON sp.channel_id=c.id
        WHERE sp.date BETWEEN ? AND ?
        ORDER BY sp.date, p.category, p.id, c.sort_order
    ''', (date_from, date_to)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/shipping-plans', methods=['POST'])
def save_shipping_plan():
    d = request.json
    db = get_db()
    db.execute('''
        INSERT INTO shipping_plans(date,product_id,channel_id,planned_qty,note)
        VALUES(?,?,?,?,?)
        ON CONFLICT(date,product_id,channel_id) DO UPDATE SET planned_qty=?,note=?,updated_at=datetime('now','localtime')
    ''', (d['date'], d['product_id'], d['channel_id'], d['planned_qty'], d.get('note',''),
          d['planned_qty'], d.get('note','')))
    db.commit(); db.close()
    return jsonify({'ok': True})

@app.route('/api/shipping-plans/bulk', methods=['POST'])
def bulk_save_plans():
    """一週間分の指示書を一括保存"""
    items = request.json  # [{date, product_id, channel_id, planned_qty}]
    db = get_db()
    for d in items:
        db.execute('''
            INSERT INTO shipping_plans(date,product_id,channel_id,planned_qty,note)
            VALUES(?,?,?,?,?)
            ON CONFLICT(date,product_id,channel_id) DO UPDATE SET planned_qty=?,updated_at=datetime('now','localtime')
        ''', (d['date'],d['product_id'],d['channel_id'],d['planned_qty'],d.get('note',''),d['planned_qty']))
    db.commit(); db.close()
    return jsonify({'ok': True, 'count': len(items)})

@app.route('/api/shipping-plans/week-copy', methods=['POST'])
def copy_week_plan():
    """先週の計画を今週にコピー"""
    d = request.json
    src_from = d['src_from']
    src_to   = d['src_to']
    dst_from = d['dst_from']
    # 日数差を計算
    src_start = datetime.strptime(src_from, '%Y-%m-%d')
    dst_start = datetime.strptime(dst_from, '%Y-%m-%d')
    diff = (dst_start - src_start).days
    db = get_db()
    rows = db.execute('SELECT * FROM shipping_plans WHERE date BETWEEN ? AND ?', (src_from, src_to)).fetchall()
    for r in rows:
        new_date = (datetime.strptime(r['date'],'%Y-%m-%d') + timedelta(days=diff)).strftime('%Y-%m-%d')
        db.execute('''
            INSERT OR IGNORE INTO shipping_plans(date,product_id,channel_id,planned_qty)
            VALUES(?,?,?,?)
        ''', (new_date, r['product_id'], r['channel_id'], r['planned_qty']))
    db.commit(); db.close()
    return jsonify({'ok': True, 'copied': len(rows)})

# ─── 出荷実績（メンバー入力）─────────────────
@app.route('/api/shipping-actuals', methods=['GET'])
def get_shipping_actuals():
    target_date = request.args.get('date')
    db = get_db()
    rows = db.execute('''
        SELECT sa.*, p.name as product_name, p.price, p.category,
               c.name as channel_name, c.sort_order
        FROM shipping_actuals sa
        JOIN products p ON sa.product_id=p.id
        JOIN channels c ON sa.channel_id=c.id
        WHERE sa.date=?
        ORDER BY p.category, p.id, c.sort_order
    ''', (target_date,)).fetchall()
    # 計画数も付与
    plans = db.execute('SELECT product_id, channel_id, planned_qty FROM shipping_plans WHERE date=?', (target_date,)).fetchall()
    plan_map = {(r['product_id'],r['channel_id']): r['planned_qty'] for r in plans}
    db.close()
    result = []
    for r in rows:
        d = dict(r)
        d['planned_qty'] = plan_map.get((r['product_id'],r['channel_id']), 0)
        result.append(d)
    return jsonify(result)

@app.route('/api/shipping-actuals/init', methods=['POST'])
def init_actuals_from_plan():
    """指示書から実績書の初期データを生成（当日分）"""
    target_date = request.json.get('date')
    db = get_db()
    plans = db.execute('SELECT * FROM shipping_plans WHERE date=? AND planned_qty>0', (target_date,)).fetchall()
    for p in plans:
        db.execute('''
            INSERT OR IGNORE INTO shipping_actuals(date,product_id,channel_id,actual_qty,actual_amount)
            VALUES(?,?,?,?,?)
        ''', (target_date, p['product_id'], p['channel_id'], p['planned_qty'],
              p['planned_qty'] * db.execute('SELECT price FROM products WHERE id=?',(p['product_id'],)).fetchone()['price']))
    db.commit(); db.close()
    return jsonify({'ok': True})

@app.route('/api/shipping-actuals', methods=['POST'])
def save_actual():
    d = request.json
    db = get_db()
    price = db.execute('SELECT price FROM products WHERE id=?', (d['product_id'],)).fetchone()['price']
    amount = d['actual_qty'] * price
    db.execute('''
        INSERT INTO shipping_actuals(date,product_id,channel_id,actual_qty,actual_amount)
        VALUES(?,?,?,?,?)
        ON CONFLICT(date,product_id,channel_id) DO UPDATE SET actual_qty=?,actual_amount=?,updated_at=datetime('now','localtime')
    ''', (d['date'],d['product_id'],d['channel_id'],d['actual_qty'],amount,d['actual_qty'],amount))
    db.commit(); db.close()
    return jsonify({'ok': True})

@app.route('/api/shipping-actuals/bulk', methods=['POST'])
def bulk_save_actuals():
    items = request.json
    db = get_db()
    for d in items:
        price = db.execute('SELECT price FROM products WHERE id=?', (d['product_id'],)).fetchone()
        price = price['price'] if price else 0
        amount = d['actual_qty'] * price
        db.execute('''
            INSERT INTO shipping_actuals(date,product_id,channel_id,actual_qty,actual_amount)
            VALUES(?,?,?,?,?)
            ON CONFLICT(date,product_id,channel_id) DO UPDATE SET actual_qty=?,actual_amount=?,updated_at=datetime('now','localtime')
        ''', (d['date'],d['product_id'],d['channel_id'],d['actual_qty'],amount,d['actual_qty'],amount))
    db.commit(); db.close()
    return jsonify({'ok': True})

# ─── メンバーマスタ ───────────────────────────
@app.route('/api/members', methods=['GET'])
def get_members():
    db = get_db()
    rows = db.execute('SELECT * FROM members WHERE active=1 ORDER BY id').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/members', methods=['POST'])
def add_member():
    d = request.json
    db = get_db()
    db.execute('INSERT INTO members(name,hourly_wage) VALUES(?,?)', (d['name'], d.get('hourly_wage', 0)))
    db.commit(); db.close()
    return jsonify({'ok': True})

@app.route('/api/members/<int:mid>', methods=['PUT'])
def update_member(mid):
    d = request.json
    db = get_db()
    db.execute('UPDATE members SET name=?,hourly_wage=?,active=? WHERE id=?',
               (d['name'], d.get('hourly_wage', 0), d.get('active', 1), mid))
    db.commit(); db.close()
    return jsonify({'ok': True})

# ─── シフト ────────────────────────────────────
@app.route('/api/shifts', methods=['GET'])
def get_shifts():
    target_date = request.args.get('date')
    db = get_db()
    rows = db.execute('SELECT * FROM shifts WHERE date=? ORDER BY member_name', (target_date,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/shifts', methods=['POST'])
def save_shifts():
    d = request.json  # {date, shifts:[{member_name, hours}]}
    db = get_db()
    db.execute('DELETE FROM shifts WHERE date=?', (d['date'],))
    for s in d['shifts']:
        if s.get('member_name','').strip() and s.get('hours', 0) > 0:
            db.execute('INSERT INTO shifts(date,member_name,hours) VALUES(?,?,?)',
                       (d['date'], s['member_name'], s['hours']))
    db.commit(); db.close()
    return jsonify({'ok': True})

# ─── シフト週次管理（予定） ────────────────────
@app.route('/api/shift-plans', methods=['GET'])
def get_shift_plans():
    target_date = request.args.get('date')
    db = get_db()
    rows = db.execute('SELECT * FROM shift_plans WHERE date=? ORDER BY member_name', (target_date,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/shifts/week', methods=['GET'])
def get_shifts_week():
    week_start = request.args.get('week_start')
    start = datetime.strptime(week_start, '%Y-%m-%d')
    dates = [(start + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    db = get_db()
    rows = db.execute('SELECT * FROM shift_plans WHERE date IN ({})'.format(','.join(['?']*6)), dates).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/shifts/week-bulk', methods=['POST'])
def save_shifts_week_bulk():
    d = request.json
    start = datetime.strptime(d['week_start'], '%Y-%m-%d')
    dates = [(start + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    db = get_db()
    db.execute('DELETE FROM shift_plans WHERE date IN ({})'.format(','.join(['?']*6)), dates)
    for s in d.get('shifts', []):
        if s.get('hours', 0) > 0:
            db.execute('INSERT INTO shift_plans(date,member_name,planned_hours) VALUES(?,?,?)',
                       (s['date'], s['member_name'], s['hours']))
    db.commit(); db.close()
    return jsonify({'ok': True})

@app.route('/api/shifts/week-copy', methods=['POST'])
def copy_shifts_week():
    d = request.json
    src = datetime.strptime(d['src_start'], '%Y-%m-%d')
    dst = datetime.strptime(d['dst_start'], '%Y-%m-%d')
    src_dates = [(src + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    dst_dates = [(dst + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    db = get_db()
    db.execute('DELETE FROM shift_plans WHERE date IN ({})'.format(','.join(['?']*6)), dst_dates)
    rows = db.execute('SELECT * FROM shift_plans WHERE date IN ({})'.format(','.join(['?']*6)), src_dates).fetchall()
    for r in rows:
        idx = src_dates.index(r['date'])
        db.execute('INSERT INTO shift_plans(date,member_name,planned_hours) VALUES(?,?,?)',
                   (dst_dates[idx], r['member_name'], r['planned_hours']))
    db.commit(); db.close()
    return jsonify({'ok': True, 'copied': len(rows)})

@app.route('/api/weekly-menus/week-copy', methods=['POST'])
def copy_weekly_menus():
    d = request.json
    src, dst = d['src_start'], d['dst_start']
    db = get_db()
    db.execute('DELETE FROM weekly_menus WHERE week_start=?', (dst,))
    rows = db.execute('SELECT * FROM weekly_menus WHERE week_start=?', (src,)).fetchall()
    for r in rows:
        db.execute('INSERT INTO weekly_menus(week_start,day_of_week,category,menu_name) VALUES(?,?,?,?)',
                   (dst, r['day_of_week'], r['category'], r['menu_name']))
    db.commit(); db.close()
    return jsonify({'ok': True, 'copied': len(rows)})

# ─── 日報生成（自動集計）────────────────────
def calc_daily_report(target_date, db):
    """実績書＋シフトから日報を自動計算"""
    actuals = db.execute('''
        SELECT sa.actual_qty, sa.actual_amount, c.name as channel_name
        FROM shipping_actuals sa
        JOIN channels c ON sa.channel_id=c.id
        WHERE sa.date=?
    ''', (target_date,)).fetchall()

    total = sum(r['actual_amount'] for r in actuals)
    west  = sum(r['actual_amount'] for r in actuals if r['channel_name']=='西店')
    south = sum(r['actual_amount'] for r in actuals if r['channel_name']=='南店')
    other = total - west - south

    shifts = db.execute('SELECT SUM(hours) as h FROM shifts WHERE date=?', (target_date,)).fetchone()
    total_hours = shifts['h'] or 0

    # NPO売上（税込計算：×1.08）
    npo = db.execute('''
        SELECT SUM(sa.actual_amount) as a FROM shipping_actuals sa
        JOIN products p ON sa.product_id=p.id
        WHERE sa.date=? AND p.name LIKE 'NPO%'
    ''', (target_date,)).fetchone()['a'] or 0
    total_with_npo = total + int(npo * 0.08)

    material = int(total_with_npo * 0.5)
    expense  = daily_expense(target_date)
    labor_rows = db.execute('''
        SELECT s.hours, COALESCE(m.hourly_wage, 0) as wage
        FROM shifts s LEFT JOIN members m ON s.member_name = m.name
        WHERE s.date = ?
    ''', (target_date,)).fetchall()
    labor_cost = sum(int(r['hours'] * r['wage']) for r in labor_rows)

    cost_total = material + labor_cost + expense
    profit = total_with_npo - cost_total
    labor_prod = (total_with_npo / total_hours) if total_hours > 0 else 0

    return {
        'date': target_date,
        'total_sales': total,
        'material_cost': material,
        'labor_cost': labor_cost,
        'expense': expense,
        'profit': profit,
        'labor_productivity': round(labor_prod, 1),
        'total_hours': total_hours,
        'west_sales': west,
        'south_sales': south,
        'other_sales': other,
    }

@app.route('/api/daily-reports/<date_str>', methods=['GET'])
def get_daily_report(date_str):
    db = get_db()
    row = db.execute('SELECT * FROM daily_reports WHERE date=?', (date_str,)).fetchone()
    if row:
        result = dict(row)
    else:
        result = calc_daily_report(date_str, db)
    # 商品×チャンネル 実績明細
    actuals_detail = db.execute('''
        SELECT sa.product_id, sa.channel_id, sa.actual_qty, sa.actual_amount,
               p.name as product_name, p.category, p.price,
               c.name as channel_name, c.sort_order
        FROM shipping_actuals sa
        JOIN products p ON sa.product_id = p.id
        JOIN channels c ON sa.channel_id = c.id
        WHERE sa.date = ? AND sa.actual_qty > 0
        ORDER BY p.category, p.id, c.sort_order
    ''', (date_str,)).fetchall()
    result['actuals_detail'] = [dict(r) for r in actuals_detail]
    # チャンネル一覧
    channels = db.execute('SELECT * FROM channels WHERE active=1 ORDER BY sort_order').fetchall()
    result['channels'] = [dict(c) for c in channels]
    # シフト付与
    shifts = db.execute('SELECT * FROM shifts WHERE date=? ORDER BY member_name', (date_str,)).fetchall()
    result['shifts'] = [dict(r) for r in shifts]
    db.close()
    return jsonify(result)

@app.route('/api/daily-info/<date_str>', methods=['POST'])
def save_daily_info(date_str):
    """天気・別注文・メモのみ部分保存（他の集計値は上書きしない）"""
    d = request.json
    db = get_db()
    db.execute('''
        INSERT INTO daily_reports(date, weather, separate_orders, note,
          total_sales, material_cost, labor_cost, expense, profit, labor_productivity, total_hours,
          west_sales, south_sales, other_sales)
        VALUES(?,?,?,?, 0,0,0,0,0,0,0, 0,0,0)
        ON CONFLICT(date) DO UPDATE SET
          weather=excluded.weather,
          separate_orders=excluded.separate_orders,
          note=excluded.note,
          updated_at=datetime('now','localtime')
    ''', (date_str, d.get('weather',''), d.get('separate_orders',0), d.get('note','')))
    db.commit(); db.close()
    return jsonify({'ok': True})

@app.route('/api/daily-reports/<date_str>', methods=['POST'])
def save_daily_report(date_str):
    d = request.json
    db = get_db()
    db.execute('''
        INSERT INTO daily_reports(date,weather,total_sales,separate_orders,material_cost,labor_cost,expense,profit,labor_productivity,total_hours,west_sales,south_sales,other_sales,note)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET weather=?,total_sales=?,separate_orders=?,material_cost=?,labor_cost=?,expense=?,profit=?,labor_productivity=?,total_hours=?,west_sales=?,south_sales=?,other_sales=?,note=?,updated_at=datetime('now','localtime')
    ''', (date_str, d.get('weather'), d.get('total_sales',0), d.get('separate_orders',0),
          d.get('material_cost',0), d.get('labor_cost',0), d.get('expense',7000),
          d.get('profit',0), d.get('labor_productivity',0), d.get('total_hours',0),
          d.get('west_sales',0), d.get('south_sales',0), d.get('other_sales',0), d.get('note',''),
          d.get('weather'), d.get('total_sales',0), d.get('separate_orders',0),
          d.get('material_cost',0), d.get('labor_cost',0), d.get('expense',7000),
          d.get('profit',0), d.get('labor_productivity',0), d.get('total_hours',0),
          d.get('west_sales',0), d.get('south_sales',0), d.get('other_sales',0), d.get('note','')))
    db.commit(); db.close()
    return jsonify({'ok': True})

@app.route('/api/daily-reports/<date_str>/generate', methods=['POST'])
def generate_daily_report(date_str):
    """実績+シフトから日報を自動生成して保存"""
    db = get_db()
    calc = calc_daily_report(date_str, db)
    weather = request.json.get('weather','') if request.json else ''
    note    = request.json.get('note','')    if request.json else ''
    # separate_ordersは別注文APIから取得
    sep = db.execute('''
        SELECT SUM(sa.actual_amount) as a FROM shipping_actuals sa
        JOIN products p ON sa.product_id=p.id
        WHERE sa.date=? AND p.name LIKE '別注%'
    ''', (date_str,)).fetchone()['a'] or 0

    db.execute('''
        INSERT INTO daily_reports(date,weather,total_sales,separate_orders,material_cost,labor_cost,expense,profit,labor_productivity,total_hours,west_sales,south_sales,other_sales,note)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET weather=?,total_sales=?,separate_orders=?,material_cost=?,labor_cost=?,expense=?,profit=?,labor_productivity=?,total_hours=?,west_sales=?,south_sales=?,other_sales=?,updated_at=datetime('now','localtime')
    ''', (date_str, weather, calc['total_sales'], int(sep),
          calc['material_cost'], calc['labor_cost'], calc['expense'],
          calc['profit'], calc['labor_productivity'], calc['total_hours'],
          calc['west_sales'], calc['south_sales'], calc['other_sales'], note,
          weather, calc['total_sales'], int(sep),
          calc['material_cost'], calc['labor_cost'], calc['expense'],
          calc['profit'], calc['labor_productivity'], calc['total_hours'],
          calc['west_sales'], calc['south_sales'], calc['other_sales']))
    db.commit(); db.close()
    calc['separate_orders'] = int(sep)
    return jsonify({'ok': True, **calc})

# ─── 月次サマリ ────────────────────────────────
@app.route('/api/monthly-summary', methods=['GET'])
def monthly_summary():
    ym = request.args.get('month')  # YYYY-MM
    db = get_db()
    rows = db.execute('''
        SELECT * FROM daily_reports
        WHERE date LIKE ? ORDER BY date
    ''', (ym + '%',)).fetchall()
    if not rows:
        db.close()
        return jsonify({'month': ym, 'days': [], 'summary': {}})

    days = [dict(r) for r in rows]
    total_sales    = sum(d['total_sales'] for d in days)
    total_labor    = sum(d['labor_cost'] for d in days)
    total_profit   = sum(d['profit'] for d in days)
    total_hours    = sum(d['total_hours'] for d in days)
    avg_lp         = (total_sales / total_hours) if total_hours > 0 else 0
    west_total     = sum(d['west_sales'] for d in days)
    south_total    = sum(d['south_sales'] for d in days)
    other_total    = sum(d['other_sales'] for d in days)

    summary = {
        'total_sales':    total_sales,
        'total_profit':   total_profit,
        'profit_rate':    round(total_profit / total_sales * 100, 1) if total_sales else 0,
        'total_labor':    total_labor,
        'labor_rate':     round(total_labor / total_sales * 100, 1) if total_sales else 0,
        'op_days':        len(days),
        'avg_daily_sales': int(total_sales / len(days)) if days else 0,
        'avg_labor_prod': round(avg_lp, 0),
        'west_sales':     west_total,
        'south_sales':    south_total,
        'other_sales':    other_total,
    }
    db.close()
    return jsonify({'month': ym, 'days': days, 'summary': summary})

# ─── 印刷用データ ─────────────────────────────
@app.route('/api/print/shipping-plan', methods=['GET'])
def print_shipping_plan():
    """出荷指示書の印刷用データ"""
    target_date = request.args.get('date')
    db = get_db()
    channels = db.execute('SELECT * FROM channels WHERE active=1 ORDER BY sort_order').fetchall()
    products = db.execute('SELECT * FROM products WHERE active=1 ORDER BY category,id').fetchall()
    plans = db.execute('''
        SELECT product_id, channel_id, planned_qty, note
        FROM shipping_plans WHERE date=?
    ''', (target_date,)).fetchall()
    plan_map = {(r['product_id'],r['channel_id']): r['planned_qty'] for r in plans}
    note_map = {r['product_id']: r['note'] for r in plans if r['note']}

    # 週間献立
    from datetime import datetime
    dt = datetime.strptime(target_date, '%Y-%m-%d')
    monday = (dt - timedelta(days=dt.weekday())).strftime('%Y-%m-%d')
    dow = dt.weekday() + 1  # 1=月
    menus = db.execute(
        'SELECT * FROM weekly_menus WHERE week_start=? AND day_of_week=? ORDER BY category',
        (monday, dow)
    ).fetchall()
    db.close()

    result = {
        'date': target_date,
        'channels': [dict(c) for c in channels],
        'products': [],
        'menus': [dict(m) for m in menus],
    }
    for p in products:
        row = {'id': p['id'], 'name': p['name'], 'price': p['price'], 'category': p['category'], 'note': note_map.get(p['id'],''), 'quantities': {}}
        for c in channels:
            row['quantities'][c['id']] = plan_map.get((p['id'], c['id']), 0)
        row['total'] = sum(row['quantities'].values())
        if row['total'] > 0:
            result['products'].append(row)
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True, port=5050)
