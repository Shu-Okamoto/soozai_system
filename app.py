from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from supabase import create_client
import os, calendar, math
from datetime import date, timedelta, datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

sb = create_client(
    os.environ.get('SUPABASE_URL', ''),
    os.environ.get('SUPABASE_SERVICE_KEY', '')
)

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

# ─── フロントエンド配信 ────────────────────────
@app.route('/')
def index():
    return send_file(os.path.join(os.path.dirname(__file__), 'index.html'))

# ─── 商品マスタ ───────────────────────────────
@app.route('/api/products', methods=['GET'])
def get_products():
    q = sb.table('hq_products').select('*')
    if request.args.get('include_inactive') != '1':
        q = q.eq('active',1)
    r = q.order('category').order('id').execute()
    return jsonify(r.data)

@app.route('/api/products', methods=['POST'])
def add_product():
    d = request.json
    sb.table('hq_products').insert({'name':d['name'],'price':d['price'],'category':d['category'],'subcategory':d.get('subcategory','')}).execute()
    return jsonify({'ok': True})

@app.route('/api/products/<int:pid>', methods=['PUT'])
def update_product(pid):
    d = request.json
    sb.table('hq_products').update({'name':d['name'],'price':d['price'],'category':d['category'],'subcategory':d.get('subcategory',''),'active':d.get('active',1)}).eq('id',pid).execute()
    return jsonify({'ok': True})

# ─── 出荷先マスタ ─────────────────────────────
@app.route('/api/channels', methods=['GET'])
def get_channels():
    r = sb.table('hq_channels').select('*').eq('active',1).order('sort_order').execute()
    return jsonify(r.data)

@app.route('/api/channels', methods=['POST'])
def add_channel():
    d = request.json
    r = sb.table('hq_channels').select('sort_order').order('sort_order', desc=True).limit(1).execute()
    max_order = r.data[0]['sort_order'] if r.data else 0
    sb.table('hq_channels').insert({'name':d['name'],'sort_order':max_order+1}).execute()
    return jsonify({'ok': True})

@app.route('/api/channels/<int:cid>', methods=['PUT'])
def update_channel(cid):
    d = request.json
    sb.table('hq_channels').update({'name':d['name'],'sort_order':d.get('sort_order',0),'active':d.get('active',1)}).eq('id',cid).execute()
    return jsonify({'ok': True})

# ─── カテゴリマスタ ───────────────────────────
@app.route('/api/categories', methods=['GET'])
def get_categories():
    q = sb.table('hq_categories').select('*')
    if request.args.get('include_inactive') != '1':
        q = q.eq('active',1)
    r = q.order('sort_order').order('id').execute()
    return jsonify(r.data)

@app.route('/api/categories', methods=['POST'])
def add_category():
    d = request.json
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'カテゴリ名が空です'}), 400
    r = sb.table('hq_categories').select('sort_order').order('sort_order', desc=True).limit(1).execute()
    max_order = r.data[0]['sort_order'] if r.data else 0
    try:
        sb.table('hq_categories').insert({'name':name,'sort_order':max_order+1}).execute()
    except Exception as e:
        return jsonify({'ok': False, 'error': '同名のカテゴリが既にあります'}), 400
    return jsonify({'ok': True})

@app.route('/api/categories/<int:cid>', methods=['PUT'])
def update_category(cid):
    d = request.json
    new_name = (d.get('name') or '').strip()
    if not new_name:
        return jsonify({'ok': False, 'error': 'カテゴリ名が空です'}), 400
    old = sb.table('hq_categories').select('name').eq('id',cid).execute().data
    if not old:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    old_name = old[0]['name']
    sb.table('hq_categories').update({
        'name':new_name,'sort_order':d.get('sort_order',0),'active':d.get('active',1)
    }).eq('id',cid).execute()
    # リネーム時は使用中商品もカスケード更新
    if old_name != new_name:
        sb.table('hq_products').update({'category':new_name}).eq('category',old_name).execute()
    return jsonify({'ok': True})

@app.route('/api/categories/<int:cid>', methods=['DELETE'])
def delete_category(cid):
    r = sb.table('hq_categories').select('name').eq('id',cid).execute().data
    if not r:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    name = r[0]['name']
    used = sb.table('hq_products').select('id').eq('category',name).execute().data
    if used:
        return jsonify({'ok': False, 'error': f'使用中の商品が{len(used)}件あるため削除できません'}), 400
    sb.table('hq_subcategories').delete().eq('category_id',cid).execute()
    sb.table('hq_categories').delete().eq('id',cid).execute()
    return jsonify({'ok': True})

@app.route('/api/subcategories', methods=['GET'])
def get_subcategories():
    q = sb.table('hq_subcategories').select('*')
    if request.args.get('include_inactive') != '1':
        q = q.eq('active',1)
    cat_id = request.args.get('category_id')
    if cat_id:
        q = q.eq('category_id', int(cat_id))
    r = q.order('category_id').order('sort_order').order('id').execute()
    return jsonify(r.data)

@app.route('/api/subcategories', methods=['POST'])
def add_subcategory():
    d = request.json
    name = (d.get('name') or '').strip()
    cat_id = d.get('category_id')
    if not name or not cat_id:
        return jsonify({'ok': False, 'error': 'カテゴリ・サブカテゴリ名が必要です'}), 400
    r = sb.table('hq_subcategories').select('sort_order').eq('category_id',cat_id).order('sort_order', desc=True).limit(1).execute()
    max_order = r.data[0]['sort_order'] if r.data else 0
    try:
        sb.table('hq_subcategories').insert({'category_id':cat_id,'name':name,'sort_order':max_order+1}).execute()
    except Exception:
        return jsonify({'ok': False, 'error': '同名のサブカテゴリが既にあります'}), 400
    return jsonify({'ok': True})

@app.route('/api/subcategories/<int:sid>', methods=['PUT'])
def update_subcategory(sid):
    d = request.json
    new_name = (d.get('name') or '').strip()
    if not new_name:
        return jsonify({'ok': False, 'error': 'サブカテゴリ名が空です'}), 400
    old = sb.table('hq_subcategories').select('name,category_id').eq('id',sid).execute().data
    if not old:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    old_name = old[0]['name']
    cat_id   = old[0]['category_id']
    sb.table('hq_subcategories').update({
        'name':new_name,'sort_order':d.get('sort_order',0),'active':d.get('active',1)
    }).eq('id',sid).execute()
    # 同カテゴリ内のリネームのみカスケード
    if old_name != new_name:
        cat = sb.table('hq_categories').select('name').eq('id',cat_id).execute().data
        if cat:
            sb.table('hq_products').update({'subcategory':new_name}).eq('category',cat[0]['name']).eq('subcategory',old_name).execute()
    return jsonify({'ok': True})

@app.route('/api/subcategories/<int:sid>', methods=['DELETE'])
def delete_subcategory(sid):
    r = sb.table('hq_subcategories').select('name,category_id').eq('id',sid).execute().data
    if not r:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    name   = r[0]['name']
    cat_id = r[0]['category_id']
    cat = sb.table('hq_categories').select('name').eq('id',cat_id).execute().data
    if cat:
        used = sb.table('hq_products').select('id').eq('category',cat[0]['name']).eq('subcategory',name).execute().data
        if used:
            return jsonify({'ok': False, 'error': f'使用中の商品が{len(used)}件あるため削除できません'}), 400
    sb.table('hq_subcategories').delete().eq('id',sid).execute()
    return jsonify({'ok': True})

# ─── 週間献立 ──────────────────────────────────
@app.route('/api/weekly-menus', methods=['GET'])
def get_weekly_menus():
    week_start = request.args.get('week_start')
    r = sb.table('hq_weekly_menus').select('*').eq('week_start',week_start).order('day_of_week').order('id').execute()
    return jsonify(r.data)

@app.route('/api/weekly-menus', methods=['POST'])
def save_weekly_menus():
    d = request.json
    sb.table('hq_weekly_menus').delete().eq('week_start',d['week_start']).execute()
    rows = [{'week_start':d['week_start'],'day_of_week':m['day_of_week'],'category':m['category'],'menu_name':m['menu_name']}
            for m in d['menus'] if m.get('menu_name','').strip()]
    if rows:
        sb.table('hq_weekly_menus').insert(rows).execute()
    return jsonify({'ok': True})

@app.route('/api/weekly-menus/week-copy', methods=['POST'])
def copy_weekly_menus():
    d = request.json
    src, dst = d['src_start'], d['dst_start']
    r = sb.table('hq_weekly_menus').select('*').eq('week_start',src).execute()
    sb.table('hq_weekly_menus').delete().eq('week_start',dst).execute()
    rows = [{'week_start':dst,'day_of_week':m['day_of_week'],'category':m['category'],'menu_name':m['menu_name']} for m in r.data]
    if rows:
        sb.table('hq_weekly_menus').insert(rows).execute()
    return jsonify({'ok': True, 'copied': len(rows)})

# ─── 出荷指示書（計画）────────────────────────
@app.route('/api/shipping-plans', methods=['GET'])
def get_shipping_plans():
    date_from = request.args.get('date_from')
    date_to   = request.args.get('date_to', date_from)
    plans = sb.table('hq_shipping_plans').select('*').gte('date',date_from).lte('date',date_to).execute().data
    if not plans:
        return jsonify([])
    pids = list({p['product_id'] for p in plans})
    cids = list({p['channel_id']  for p in plans})
    prods = {p['id']:p for p in sb.table('hq_products').select('id,name,price,category').in_('id',pids).execute().data}
    chans = {c['id']:c for c in sb.table('hq_channels').select('id,name,sort_order').in_('id',cids).execute().data}
    result = []
    for p in plans:
        row = dict(p)
        pr = prods.get(p['product_id'],{}); ch = chans.get(p['channel_id'],{})
        row.update({'product_name':pr.get('name',''),'price':pr.get('price',0),'category':pr.get('category',''),'channel_name':ch.get('name',''),'sort_order':ch.get('sort_order',0)})
        result.append(row)
    result.sort(key=lambda x:(x['date'],x.get('category',''),x.get('product_id',0),x.get('sort_order',0)))
    return jsonify(result)

@app.route('/api/shipping-plans/bulk', methods=['POST'])
def bulk_save_plans():
    items = request.json
    rows  = [{'date':d['date'],'product_id':d['product_id'],'channel_id':d['channel_id'],'planned_qty':d['planned_qty'],'note':d.get('note','')} for d in items]
    if rows:
        sb.table('hq_shipping_plans').upsert(rows, on_conflict='date,product_id,channel_id').execute()
    return jsonify({'ok': True, 'count': len(items)})

@app.route('/api/shipping-plans/week-copy', methods=['POST'])
def copy_week_plan():
    d         = request.json
    src_from  = d['src_from']; src_to = d['src_to']; dst_from = d['dst_from']
    diff      = (datetime.strptime(dst_from,'%Y-%m-%d') - datetime.strptime(src_from,'%Y-%m-%d')).days
    # planned_qty=0 の行（=空欄として保存された行）はコピー対象外にする
    # （コピー時に今週入力済みの値をゼロで上書きしてしまうため）
    rows      = sb.table('hq_shipping_plans').select('*').gte('date',src_from).lte('date',src_to).gt('planned_qty',0).execute().data
    new_rows  = [{'date':(datetime.strptime(r['date'],'%Y-%m-%d')+timedelta(days=diff)).strftime('%Y-%m-%d'),
                  'product_id':r['product_id'],'channel_id':r['channel_id'],'planned_qty':r['planned_qty']} for r in rows]
    if new_rows:
        sb.table('hq_shipping_plans').upsert(new_rows, on_conflict='date,product_id,channel_id').execute()
    return jsonify({'ok': True, 'copied': len(rows)})

# ─── 出荷実績 ─────────────────────────────────
@app.route('/api/shipping-actuals', methods=['GET'])
def get_shipping_actuals():
    target_date = request.args.get('date')
    actuals = sb.table('hq_shipping_actuals').select('*').eq('date',target_date).execute().data
    plans   = sb.table('hq_shipping_plans').select('product_id,channel_id,planned_qty').eq('date',target_date).execute().data
    plan_map = {(p['product_id'],p['channel_id']):p['planned_qty'] for p in plans}
    if not actuals:
        return jsonify([])
    pids = list({a['product_id'] for a in actuals})
    cids = list({a['channel_id']  for a in actuals})
    prods = {p['id']:p for p in sb.table('hq_products').select('id,name,price,category').in_('id',pids).execute().data}
    chans = {c['id']:c for c in sb.table('hq_channels').select('id,name,sort_order').in_('id',cids).execute().data}
    result = []
    for a in actuals:
        row = dict(a); pr = prods.get(a['product_id'],{}); ch = chans.get(a['channel_id'],{})
        row.update({'product_name':pr.get('name',''),'price':pr.get('price',0),'category':pr.get('category',''),
                    'channel_name':ch.get('name',''),'sort_order':ch.get('sort_order',0),
                    'planned_qty':plan_map.get((a['product_id'],a['channel_id']),0)})
        result.append(row)
    return jsonify(result)

@app.route('/api/shipping-actuals/init', methods=['POST'])
def init_actuals_from_plan():
    target_date = request.json.get('date')
    plans = sb.table('hq_shipping_plans').select('*').eq('date',target_date).gt('planned_qty',0).execute().data
    pids  = list({p['product_id'] for p in plans})
    prods = {p['id']:p for p in sb.table('hq_products').select('id,price').in_('id',pids).execute().data} if pids else {}
    rows  = [{'date':target_date,'product_id':p['product_id'],'channel_id':p['channel_id'],
              'actual_qty':p['planned_qty'],'actual_amount':p['planned_qty']*prods.get(p['product_id'],{}).get('price',0)} for p in plans]
    if rows:
        sb.table('hq_shipping_actuals').upsert(rows, on_conflict='date,product_id,channel_id').execute()
    return jsonify({'ok': True})

@app.route('/api/shipping-actuals/bulk', methods=['POST'])
def bulk_save_actuals():
    items = request.json
    pids  = list({d['product_id'] for d in items})
    prods = {p['id']:p['price'] for p in sb.table('hq_products').select('id,price').in_('id',pids).execute().data} if pids else {}
    def price_of(d):
        up = d.get('unit_price')
        return up if up is not None and up != '' else prods.get(d['product_id'], 0)
    rows  = [{'date':d['date'],'product_id':d['product_id'],'channel_id':d['channel_id'],
              'actual_qty':d['actual_qty'],'actual_amount':d['actual_qty']*price_of(d)} for d in items]
    if rows:
        sb.table('hq_shipping_actuals').upsert(rows, on_conflict='date,product_id,channel_id').execute()

    # 既存日報があれば自動で再生成（カード値・月次集計を実績と一致させる）
    dates = {d['date'] for d in items}
    sync_keys = ['total_sales','material_cost','labor_cost','expense','profit','labor_productivity',
                 'total_hours','west_sales','south_sales','other_sales','separate_orders']
    for date in dates:
        stored = sb.table('hq_daily_reports').select('expense').eq('date',date).execute().data
        if not stored:
            continue
        calc = calc_daily_report(date, expense_override=stored[0].get('expense'))
        sb.table('hq_daily_reports').update({k:calc[k] for k in sync_keys}).eq('date',date).execute()
    return jsonify({'ok': True})

# ─── メンバーマスタ ───────────────────────────
@app.route('/api/members', methods=['GET'])
def get_members():
    r = sb.table('hq_members').select('*').eq('active',1).order('id').execute()
    return jsonify(r.data)

@app.route('/api/members', methods=['POST'])
def add_member():
    d = request.json
    sb.table('hq_members').insert({'name':d['name'],'hourly_wage':d.get('hourly_wage',0)}).execute()
    return jsonify({'ok': True})

@app.route('/api/members/<int:mid>', methods=['PUT'])
def update_member(mid):
    d = request.json
    sb.table('hq_members').update({'name':d['name'],'hourly_wage':d.get('hourly_wage',0),'active':d.get('active',1)}).eq('id',mid).execute()
    return jsonify({'ok': True})

# ─── シフト ────────────────────────────────────
@app.route('/api/shifts', methods=['GET'])
def get_shifts():
    r = sb.table('hq_shifts').select('*').eq('date',request.args.get('date')).order('member_name').execute()
    return jsonify(r.data)

@app.route('/api/shifts', methods=['POST'])
def save_shifts():
    d = request.json
    date = d['date']
    sb.table('hq_shifts').delete().eq('date',date).execute()
    rows = [{'date':date,'member_name':s['member_name'],'hours':s['hours']}
            for s in d['shifts'] if s.get('member_name','').strip() and s.get('hours',0)>0]
    if rows:
        sb.table('hq_shifts').insert(rows).execute()

    # 既存日報があれば自動で再生成（人件費・利益・人時売を実績と一致させる）
    stored = sb.table('hq_daily_reports').select('expense').eq('date',date).execute().data
    if stored:
        calc = calc_daily_report(date, expense_override=stored[0].get('expense'))
        sync_keys = ['total_sales','material_cost','labor_cost','expense','profit','labor_productivity',
                     'total_hours','west_sales','south_sales','other_sales','separate_orders']
        sb.table('hq_daily_reports').update({k:calc[k] for k in sync_keys}).eq('date',date).execute()
    return jsonify({'ok': True})

# ─── シフト週次管理（予定） ────────────────────
@app.route('/api/shift-plans', methods=['GET'])
def get_shift_plans():
    r = sb.table('hq_shift_plans').select('*').eq('date',request.args.get('date')).order('member_name').execute()
    return jsonify(r.data)

@app.route('/api/shifts/week', methods=['GET'])
def get_shifts_week():
    week_start = request.args.get('week_start')
    start  = datetime.strptime(week_start,'%Y-%m-%d')
    dates  = [(start+timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    r = sb.table('hq_shift_plans').select('*').in_('date',dates).execute()
    return jsonify(r.data)

@app.route('/api/shifts/week-bulk', methods=['POST'])
def save_shifts_week_bulk():
    d     = request.json
    start = datetime.strptime(d['week_start'],'%Y-%m-%d')
    dates = [(start+timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    sb.table('hq_shift_plans').delete().in_('date',dates).execute()
    rows = [{'date':s['date'],'member_name':s['member_name'],'planned_hours':s['hours']}
            for s in d.get('shifts',[]) if s.get('hours',0)>0]
    if rows:
        sb.table('hq_shift_plans').insert(rows).execute()
    return jsonify({'ok': True})

@app.route('/api/shifts/week-copy', methods=['POST'])
def copy_shifts_week():
    d         = request.json
    src       = datetime.strptime(d['src_start'],'%Y-%m-%d')
    dst       = datetime.strptime(d['dst_start'],'%Y-%m-%d')
    src_dates = [(src+timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    dst_dates = [(dst+timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    rows      = sb.table('hq_shift_plans').select('*').in_('date',src_dates).execute().data
    sb.table('hq_shift_plans').delete().in_('date',dst_dates).execute()
    new_rows = [{'date':dst_dates[src_dates.index(r['date'])],'member_name':r['member_name'],'planned_hours':r['planned_hours']} for r in rows]
    if new_rows:
        sb.table('hq_shift_plans').insert(new_rows).execute()
    return jsonify({'ok': True, 'copied': len(rows)})

# ─── 日報 ─────────────────────────────────────
def calc_daily_report(target_date, expense_override=None):
    # 非活性の出荷先は売上集計から除外（売上明細マトリクスと一致させるため）
    active_chs   = sb.table('hq_channels').select('id,name').eq('active',1).execute().data
    active_cids  = {c['id'] for c in active_chs}
    channels     = {c['id']:c['name'] for c in active_chs}
    actuals_all  = sb.table('hq_shipping_actuals').select('actual_amount,product_id,channel_id').eq('date',target_date).execute().data
    actuals      = [r for r in actuals_all if r['channel_id'] in active_cids]
    total        = sum(r['actual_amount'] for r in actuals)
    west         = sum(r['actual_amount'] for r in actuals if channels.get(r['channel_id'])=='西店')
    south        = sum(r['actual_amount'] for r in actuals if channels.get(r['channel_id'])=='南店')
    other        = total - west - south

    betch_pids = {p['id'] for p in sb.table('hq_products').select('id').like('name','別注%').execute().data}
    separate_orders = int(sum(r['actual_amount'] for r in actuals if r['product_id'] in betch_pids))

    shifts_data = sb.table('hq_shifts').select('hours,member_name').eq('date',target_date).execute().data
    total_hours = sum(s['hours'] for s in shifts_data)

    npo_pids = {p['id'] for p in sb.table('hq_products').select('id').like('name','NPO%').execute().data}
    npo      = sum(r['actual_amount'] for r in actuals if r['product_id'] in npo_pids)
    total_with_npo = total + int(npo * 0.08)
    material = int(total_with_npo * 0.5)
    expense  = expense_override if expense_override is not None else daily_expense(target_date)

    wage_map   = {m['name']:m['hourly_wage'] for m in sb.table('hq_members').select('name,hourly_wage').execute().data}
    labor_cost = sum(int(s['hours']*wage_map.get(s['member_name'],0)) for s in shifts_data)
    profit     = total_with_npo - material - labor_cost - expense
    labor_prod = (total_with_npo/total_hours) if total_hours>0 else 0

    return {'date':target_date,'total_sales':total,'material_cost':material,'labor_cost':labor_cost,
            'expense':expense,'profit':profit,'labor_productivity':round(labor_prod,1),
            'total_hours':total_hours,'west_sales':west,'south_sales':south,'other_sales':other,
            'separate_orders':separate_orders}

@app.route('/api/daily-reports/<date_str>', methods=['GET'])
def get_daily_report(date_str):
    stored = sb.table('hq_daily_reports').select('*').eq('date',date_str).execute().data
    # 常に最新の集計を再計算（出荷先のactive化など状態変化に追従させるため）
    calc   = calc_daily_report(date_str, expense_override=stored[0].get('expense') if stored else None)
    if stored:
        result = {**stored[0], **calc}
        # 保存済みとズレていれば月次サマリのために遅延更新
        sync_keys = ['total_sales','material_cost','labor_cost','profit','labor_productivity',
                     'total_hours','west_sales','south_sales','other_sales','separate_orders']
        if any(stored[0].get(k) != calc.get(k) for k in sync_keys):
            sb.table('hq_daily_reports').update({k:calc[k] for k in sync_keys}).eq('date',date_str).execute()
    else:
        result = calc

    active_chs    = sb.table('hq_channels').select('*').eq('active',1).order('sort_order').execute().data
    active_cids   = {c['id'] for c in active_chs}
    actuals = sb.table('hq_shipping_actuals').select('*').eq('date',date_str).gt('actual_qty',0).execute().data
    actuals = [a for a in actuals if a['channel_id'] in active_cids]
    if actuals:
        pids  = list({a['product_id'] for a in actuals})
        prods = {p['id']:p for p in sb.table('hq_products').select('id,name,category,price').in_('id',pids).execute().data}
        chans = {c['id']:c for c in active_chs}
        detail = []
        for a in actuals:
            row = dict(a); pr=prods.get(a['product_id'],{}); ch=chans.get(a['channel_id'],{})
            row.update({'product_name':pr.get('name',''),'category':pr.get('category',''),'price':pr.get('price',0),
                        'channel_name':ch.get('name',''),'sort_order':ch.get('sort_order',0)})
            detail.append(row)
        detail.sort(key=lambda x:(x.get('category',''),x.get('product_id',0),x.get('sort_order',0)))
        result['actuals_detail'] = detail
    else:
        result['actuals_detail'] = []

    result['channels'] = active_chs
    result['shifts']   = sb.table('hq_shifts').select('*').eq('date',date_str).order('member_name').execute().data
    return jsonify(result)

@app.route('/api/daily-info/<date_str>', methods=['POST'])
def save_daily_info(date_str):
    d = request.json
    existing = sb.table('hq_daily_reports').select('date').eq('date',date_str).execute().data
    if existing:
        sb.table('hq_daily_reports').update({'weather':d.get('weather',''),'note':d.get('note',''),'separate_orders':d.get('separate_orders',0)}).eq('date',date_str).execute()
    else:
        sb.table('hq_daily_reports').insert({'date':date_str,'weather':d.get('weather',''),'note':d.get('note',''),'separate_orders':d.get('separate_orders',0),'total_sales':0,'material_cost':0,'labor_cost':0,'expense':0,'profit':0,'labor_productivity':0,'total_hours':0,'west_sales':0,'south_sales':0,'other_sales':0}).execute()
    return jsonify({'ok': True})

@app.route('/api/daily-reports/<date_str>', methods=['POST'])
def save_daily_report(date_str):
    d = request.json
    data = {'date':date_str,'weather':d.get('weather'),'total_sales':d.get('total_sales',0),'separate_orders':d.get('separate_orders',0),
            'material_cost':d.get('material_cost',0),'labor_cost':d.get('labor_cost',0),'expense':d.get('expense',0),
            'profit':d.get('profit',0),'labor_productivity':d.get('labor_productivity',0),'total_hours':d.get('total_hours',0),
            'west_sales':d.get('west_sales',0),'south_sales':d.get('south_sales',0),'other_sales':d.get('other_sales',0),'note':d.get('note','')}
    sb.table('hq_daily_reports').upsert(data, on_conflict='date').execute()
    return jsonify({'ok': True})

@app.route('/api/daily-reports/<date_str>/generate', methods=['POST'])
def generate_daily_report(date_str):
    calc    = calc_daily_report(date_str)
    weather = request.json.get('weather','') if request.json else ''
    note    = request.json.get('note','')    if request.json else ''
    existing = sb.table('hq_daily_reports').select('note').eq('date',date_str).execute().data
    saved_note = existing[0]['note'] if existing else note
    data = {'date':date_str,'weather':weather,'total_sales':calc['total_sales'],'separate_orders':calc['separate_orders'],
            'material_cost':calc['material_cost'],'labor_cost':calc['labor_cost'],'expense':calc['expense'],
            'profit':calc['profit'],'labor_productivity':calc['labor_productivity'],'total_hours':calc['total_hours'],
            'west_sales':calc['west_sales'],'south_sales':calc['south_sales'],'other_sales':calc['other_sales'],'note':saved_note}
    sb.table('hq_daily_reports').upsert(data, on_conflict='date').execute()
    return jsonify({'ok': True, **calc})

# ─── 月次サマリ ────────────────────────────────
@app.route('/api/monthly-summary', methods=['GET'])
def monthly_summary():
    ym   = request.args.get('month')
    rows = sb.table('hq_daily_reports').select('*').like('date',ym+'%').order('date').execute().data
    if not rows:
        return jsonify({'month':ym,'days':[],'summary':{}})
    days        = rows
    total_sales = sum(d['total_sales'] for d in days)
    total_labor = sum(d['labor_cost']  for d in days)
    total_profit= sum(d['profit']      for d in days)
    total_hours = sum(d['total_hours'] for d in days)
    avg_lp      = (total_sales/total_hours) if total_hours>0 else 0
    summary = {'total_sales':total_sales,'total_profit':total_profit,
               'profit_rate':round(total_profit/total_sales*100,1) if total_sales else 0,
               'total_labor':total_labor,'labor_rate':round(total_labor/total_sales*100,1) if total_sales else 0,
               'op_days':len(days),'avg_daily_sales':int(total_sales/len(days)) if days else 0,
               'avg_labor_prod':round(avg_lp,0),
               'west_sales':sum(d['west_sales'] for d in days),'south_sales':sum(d['south_sales'] for d in days),'other_sales':sum(d['other_sales'] for d in days)}
    return jsonify({'month':ym,'days':days,'summary':summary})

# ─── 印刷用データ ─────────────────────────────
@app.route('/api/print/shipping-plan', methods=['GET'])
def print_shipping_plan():
    target_date = request.args.get('date')
    channels = sb.table('hq_channels').select('*').eq('active',1).order('sort_order').execute().data
    products = sb.table('hq_products').select('*').eq('active',1).order('category').order('id').execute().data
    plans    = sb.table('hq_shipping_plans').select('product_id,channel_id,planned_qty,note').eq('date',target_date).execute().data
    plan_map = {(p['product_id'],p['channel_id']):p['planned_qty'] for p in plans}
    note_map = {p['product_id']:p['note'] for p in plans if p.get('note')}
    dt     = datetime.strptime(target_date,'%Y-%m-%d')
    monday = (dt-timedelta(days=dt.weekday())).strftime('%Y-%m-%d')
    menus  = sb.table('hq_weekly_menus').select('*').eq('week_start',monday).eq('day_of_week',dt.weekday()+1).order('category').execute().data
    result = {'date':target_date,'channels':channels,'products':[],'menus':menus}
    for p in products:
        row = {'id':p['id'],'name':p['name'],'price':p['price'],'category':p['category'],'note':note_map.get(p['id'],''),'quantities':{}}
        for c in channels:
            row['quantities'][c['id']] = plan_map.get((p['id'],c['id']),0)
        row['total'] = sum(row['quantities'].values())
        if row['total']>0:
            result['products'].append(row)
    return jsonify(result)

# ─── 注文弁当（bento app 連携）────────────────
@app.route('/api/bento/orders', methods=['GET'])
def get_bento_orders():
    """指定日(?date=YYYY-MM-DD)、または無指定なら明日以降の注文を取得"""
    target_date = request.args.get('date')
    try:
        q = sb.table('orders').select('*')
        if target_date:
            q = q.eq('delivery_date', target_date)
        else:
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            q = q.gte('delivery_date', tomorrow)
        orders = q.order('delivery_date').order('created_at').execute().data
    except Exception as e:
        return jsonify({'error': f'orders取得失敗: {e}'}), 500
    if not orders:
        return jsonify([])

    office_ids  = list({o['office_id']  for o in orders if o.get('office_id')})
    member_ids  = list({o['member_id']  for o in orders if o.get('member_id')})
    product_ids = list({o['product_id'] for o in orders if o.get('product_id')})

    def safe_in(table, ids, fields='id,name'):
        if not ids: return {}
        try:
            rows = sb.table(table).select(fields).in_('id', ids).execute().data
            return {r['id']: r for r in rows}
        except Exception:
            return {}

    offices  = safe_in('offices',  office_ids)
    members  = safe_in('members',  member_ids)
    products = safe_in('products', product_ids)

    result = []
    for o in orders:
        result.append({
            'id':             o['id'],
            'delivery_date':  o['delivery_date'],
            'office_name':    offices.get(o.get('office_id'),  {}).get('name', ''),
            'member_name':    members.get(o.get('member_id'),  {}).get('name', ''),
            'product_name':   products.get(o.get('product_id'),{}).get('name', ''),
            'quantity':       o.get('quantity', 0),
            'payment_method': o.get('payment_method', ''),
            'note':           o.get('note', ''),
        })
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True, port=5050)
