from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from supabase import create_client
import os, calendar, math, re
from datetime import date, timedelta, datetime, timezone
from dotenv import load_dotenv

JST = timezone(timedelta(hours=9))
def today_jst():
    return datetime.now(JST).date()
def now_jst_iso():
    return datetime.now(JST).isoformat()
def finalize_ts(date_str):
    """日報の確定時刻。過去日はその営業日の終了時刻(23:59:59 JST)を返し、
    当日以降は現在時刻を返す。これにより、過去日の遅延確定や過去データの
    取込でも『確定時刻＝現在時刻』にならず、その日報の営業日を反映する。"""
    try:
        if date_str < today_jst().isoformat():
            return f'{date_str}T23:59:59+09:00'
    except TypeError:
        pass
    return now_jst_iso()

load_dotenv()

app = Flask(__name__)
CORS(app)

sb = create_client(
    os.environ.get('SUPABASE_URL', ''),
    os.environ.get('SUPABASE_SERVICE_KEY', '')
)
# dx 用クライアントは sb と独立に生成する。
# supabase-py の Client.schema() は内部 postgrest のヘッダを破壊的に書き換えるため、
# 同じ sb で schema('dx') を呼ぶと以降の sb.table(...) も dx を見にいって全壊する。
sb_dx = create_client(
    os.environ.get('SUPABASE_URL', ''),
    os.environ.get('SUPABASE_SERVICE_KEY', '')
)
sb_dx.postgrest.schema('dx')

MONTHLY_FIXED_COST = 300000   # 部署 config に monthly_fixed_cost が無い場合のフォールバック

# ─── 部署(department) 解決 [Phase 2] ───────────────
# フロントは全リクエストに ?dept=<code> を付与する。未指定時は弁当部にフォールバック
# （フロント未対応のうちは常に弁当部＝従来どおりの挙動）。
DEFAULT_DEPT_CODE = 'bento'
# hq_departments が未適用/未読込のときのフォールバック。弁当部の従来挙動を保つため
# 全機能ONの config を持たせる（DBに部署が在ればそちらが優先される）。
_FALLBACK_DEPT = {
    'id': 1, 'code': 'bento', 'name': '弁当惣菜部',
    'config': {
        'monthly_fixed_cost': MONTHLY_FIXED_COST, 'material_rate': 0.5,
        'sales_split': {'west': '西店', 'south': '南店'},
        'features': {'weekly_menu': True, 'order_calc': True, 'dx_orders': True,
                     'npo_adjust': True, 'separate_orders': True},
    },
}
_DEPT_BY_CODE = {}

def _refresh_departments():
    global _DEPT_BY_CODE
    try:
        rows = sb.table('hq_departments').select('*').execute().data or []
        _DEPT_BY_CODE = {r['code']: r for r in rows}
    except Exception:
        _DEPT_BY_CODE = {}
    return _DEPT_BY_CODE

def get_dept(code=None):
    """リクエスト(?dept= または X-Department ヘッダ)から部署を解決して dict を返す。"""
    if code is None:
        try:
            code = request.args.get('dept') or request.headers.get('X-Department')
        except RuntimeError:        # request コンテキスト外（cron 等）
            code = None
    code = code or DEFAULT_DEPT_CODE
    if code not in _DEPT_BY_CODE:
        _refresh_departments()
    return (_DEPT_BY_CODE.get(code)
            or _DEPT_BY_CODE.get(DEFAULT_DEPT_CODE)
            or _FALLBACK_DEPT)

def dept_id(code=None):
    return get_dept(code)['id']

def dept_config(d=None):
    return (d or get_dept()).get('config') or {}

def is_hq_request():
    """本部（弁当部 or config.features.hq=true の部署）からのリクエストか。
    メンバー編集・売上実績取込など全社共通の操作は本部のみに限定する。"""
    dep = get_dept()
    return dep.get('code') == DEFAULT_DEPT_CODE or bool((dep.get('config') or {}).get('features', {}).get('hq'))

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

def daily_expense(target_date_str, monthly_fixed_cost=None):
    dt = datetime.strptime(target_date_str, '%Y-%m-%d')
    wd = calc_working_days(dt.year, dt.month)
    fixed = MONTHLY_FIXED_COST if monthly_fixed_cost is None else monthly_fixed_cost
    return math.ceil(fixed / wd) if wd > 0 else fixed

# ─── フロントエンド配信 ────────────────────────
@app.route('/')
def index():
    return send_file(os.path.join(os.path.dirname(__file__), 'index.html'))

# 部署別の入口（/mochi, /tsukemono 等）。同じSPAを返し、フロントが URL から部署を判定する。
# /api/... は2セグメント以上なのでこの単一セグメントのルートには一致しない。
@app.route('/<dept_code>')
def index_dept(dept_code):
    return send_file(os.path.join(os.path.dirname(__file__), 'index.html'))

# ─── 部署マスタ（フロントの部署セレクタ・機能フラグ用）──
@app.route('/api/departments', methods=['GET'])
def get_departments():
    rows = sb.table('hq_departments').select('id,code,name,sort_order,active,config')\
        .eq('active',1).order('sort_order').execute().data
    # admin_pin はクライアントへ返さない（PIN検証はサーバー側 /api/auth/verify-pin で行う）
    for r in rows:
        cfg = r.get('config')
        if isinstance(cfg, dict) and 'admin_pin' in cfg:
            r['config'] = {k: v for k, v in cfg.items() if k != 'admin_pin'}
    return jsonify(rows)

# ─── 管理者PIN検証 ─────────────────────────────
# 本部PIN（環境変数 HQ_ADMIN_PIN、既定 1234）＝全部署切替可。
# 部署別PIN（hq_departments.config.admin_pin）＝その部署のみ。
HQ_ADMIN_PIN = os.environ.get('HQ_ADMIN_PIN', '1234')

@app.route('/api/auth/verify-pin', methods=['POST'])
def verify_pin():
    d = request.json or {}
    pin  = str(d.get('pin', '')).strip()
    code = d.get('dept')
    if not pin:
        return jsonify({'ok': False})
    # 本部管理者（全部署切替可）
    if pin == HQ_ADMIN_PIN:
        return jsonify({'ok': True, 'role': 'admin', 'scope': 'hq'})
    # 部署別管理者（自部署のみ）
    dep = get_dept(code)
    dep_pin = str((dep.get('config') or {}).get('admin_pin') or '').strip()
    if dep_pin and pin == dep_pin:
        return jsonify({'ok': True, 'role': 'admin', 'scope': 'dept', 'dept': dep.get('code')})
    return jsonify({'ok': False})

# ─── 商品マスタ ───────────────────────────────
@app.route('/api/products', methods=['GET'])
def get_products():
    q = sb.table('hq_products').select('*').eq('department_id', dept_id())
    if request.args.get('include_inactive') != '1':
        q = q.eq('active',1)
    r = q.order('category').order('id').execute()
    return jsonify(r.data)

@app.route('/api/products', methods=['POST'])
def add_product():
    d = request.json
    sb.table('hq_products').insert({'name':d['name'],'price':d['price'],'category':d['category'],'subcategory':d.get('subcategory',''),'prod_type':d.get('prod_type','manufacture'),'department_id':dept_id()}).execute()
    return jsonify({'ok': True})

@app.route('/api/products/<int:pid>', methods=['PUT'])
def update_product(pid):
    d = request.json
    upd = {'name':d['name'],'price':d['price'],'category':d['category'],'subcategory':d.get('subcategory',''),'active':d.get('active',1)}
    if 'prod_type' in d:
        upd['prod_type'] = d['prod_type']
    sb.table('hq_products').update(upd).eq('id',pid).eq('department_id',dept_id()).execute()
    return jsonify({'ok': True})

# ─── 出荷先マスタ ─────────────────────────────
@app.route('/api/channels', methods=['GET'])
def get_channels():
    r = sb.table('hq_channels').select('*').eq('department_id',dept_id()).eq('active',1).order('sort_order').execute()
    return jsonify(r.data)

@app.route('/api/channels', methods=['POST'])
def add_channel():
    d = request.json
    did = dept_id()
    r = sb.table('hq_channels').select('sort_order').eq('department_id',did).order('sort_order', desc=True).limit(1).execute()
    max_order = r.data[0]['sort_order'] if r.data else 0
    sb.table('hq_channels').insert({'name':d['name'],'sort_order':max_order+1,'department_id':did}).execute()
    return jsonify({'ok': True})

@app.route('/api/channels/<int:cid>', methods=['PUT'])
def update_channel(cid):
    d = request.json
    sb.table('hq_channels').update({'name':d['name'],'sort_order':d.get('sort_order',0),'active':d.get('active',1)}).eq('id',cid).eq('department_id',dept_id()).execute()
    return jsonify({'ok': True})

# ─── 原材料発注：業者マスタ ───────────────────
@app.route('/api/suppliers', methods=['GET'])
def get_suppliers():
    q = sb.table('hq_suppliers').select('*').eq('department_id',dept_id())
    if request.args.get('include_inactive') != '1':
        q = q.eq('active',1)
    r = q.order('sort_order').order('id').execute()
    return jsonify(r.data)

@app.route('/api/suppliers', methods=['POST'])
def add_supplier():
    d = request.json or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '業者名が空です'}), 400
    did = dept_id()
    r = sb.table('hq_suppliers').select('sort_order').eq('department_id',did).order('sort_order', desc=True).limit(1).execute()
    max_order = r.data[0]['sort_order'] if r.data else 0
    try:
        sb.table('hq_suppliers').insert({
            'name':name,'order_days':d.get('order_days',''),'delivery_days':d.get('delivery_days',''),
            'phone':d.get('phone',''),'site_url':d.get('site_url',''),
            'sort_order':max_order+1,'department_id':did
        }).execute()
    except Exception:
        return jsonify({'ok': False, 'error': '同名の業者が既にあります'}), 400
    return jsonify({'ok': True})

@app.route('/api/suppliers/<int:sid>', methods=['PUT'])
def update_supplier(sid):
    d = request.json or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '業者名が空です'}), 400
    sb.table('hq_suppliers').update({
        'name':name,'order_days':d.get('order_days',''),'delivery_days':d.get('delivery_days',''),
        'phone':d.get('phone',''),'site_url':d.get('site_url',''),
        'sort_order':d.get('sort_order',0),'active':d.get('active',1)
    }).eq('id',sid).eq('department_id',dept_id()).execute()
    return jsonify({'ok': True})

@app.route('/api/suppliers/<int:sid>', methods=['DELETE'])
def delete_supplier(sid):
    did = dept_id()
    # 使用中の発注商品があれば業者参照を外す（ON DELETE SET NULL 相当を明示）
    sb.table('hq_order_products').update({'supplier_id':None}).eq('supplier_id',sid).eq('department_id',did).execute()
    sb.table('hq_suppliers').delete().eq('id',sid).eq('department_id',did).execute()
    return jsonify({'ok': True})

# ─── 原材料発注：発注商品マスタ ───────────────
def calc_order_qty(base_qty, stock_qty, order_unit):
    """発注数 = 基準数 - 在庫数（0未満は0）。発注数単位があれば一番近い倍数に四捨五入。"""
    need = (base_qty or 0) - (stock_qty or 0)
    if need <= 0:
        return 0
    unit = order_unit or 0
    if unit > 0:
        return int(math.floor(need / unit + 0.5)) * unit
    return int(need)

@app.route('/api/order-products', methods=['GET'])
def get_order_products():
    q = sb.table('hq_order_products').select('*').eq('department_id',dept_id())
    if request.args.get('include_inactive') != '1':
        q = q.eq('active',1)
    cat = request.args.get('category')
    if cat:
        q = q.eq('category', cat)
    r = q.order('category').order('sort_order').order('id').execute()
    return jsonify(r.data)

@app.route('/api/order-products', methods=['POST'])
def add_order_product():
    d = request.json or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '商品名が空です'}), 400
    did = dept_id()
    r = sb.table('hq_order_products').select('sort_order').eq('department_id',did).order('sort_order', desc=True).limit(1).execute()
    max_order = r.data[0]['sort_order'] if r.data else 0
    try:
        sb.table('hq_order_products').insert({
            'name':name,'category':(d.get('category') or '').strip(),'price':d.get('price',0) or 0,
            'supplier_id':d.get('supplier_id') or None,'base_qty':d.get('base_qty',0) or 0,
            'order_unit':d.get('order_unit',0) or 0,'sort_order':max_order+1,'department_id':did
        }).execute()
    except Exception:
        return jsonify({'ok': False, 'error': '同名の発注商品が既にあります'}), 400
    return jsonify({'ok': True})

@app.route('/api/order-products/<int:pid>', methods=['PUT'])
def update_order_product(pid):
    d = request.json or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '商品名が空です'}), 400
    sb.table('hq_order_products').update({
        'name':name,'category':(d.get('category') or '').strip(),'price':d.get('price',0) or 0,
        'supplier_id':d.get('supplier_id') or None,'base_qty':d.get('base_qty',0) or 0,
        'order_unit':d.get('order_unit',0) or 0,'sort_order':d.get('sort_order',0),'active':d.get('active',1)
    }).eq('id',pid).eq('department_id',dept_id()).execute()
    return jsonify({'ok': True})

@app.route('/api/order-products/<int:pid>', methods=['DELETE'])
def delete_order_product(pid):
    did = dept_id()
    sb.table('hq_order_products').delete().eq('id',pid).eq('department_id',did).execute()
    return jsonify({'ok': True})

# ─── 原材料発注：在庫入力・発注数（日付ごと）──
@app.route('/api/material-orders', methods=['GET'])
def get_material_orders():
    target_date = request.args.get('date')
    r = sb.table('hq_material_orders').select('*').eq('department_id',dept_id()).eq('date',target_date).execute()
    return jsonify(r.data)

@app.route('/api/material-orders/bulk', methods=['POST'])
def save_material_orders():
    d = request.json or {}
    target_date = d.get('date')
    items = d.get('items', [])
    did = dept_id()
    if not target_date:
        return jsonify({'ok': False, 'error': 'date が必要です'}), 400
    # 発注数はサーバー側で再計算（基準数・単位はマスタを正とする）
    pids = [it.get('order_product_id') for it in items if it.get('order_product_id')]
    prods = {p['id']:p for p in sb.table('hq_order_products').select('id,base_qty,order_unit')
             .eq('department_id',did).in_('id',pids).execute().data} if pids else {}
    rows = []
    for it in items:
        pid = it.get('order_product_id')
        if not pid or pid not in prods:
            continue
        stock = it.get('stock_qty', 0) or 0
        p = prods[pid]
        oq = calc_order_qty(p.get('base_qty'), stock, p.get('order_unit'))
        rows.append({'date':target_date,'order_product_id':pid,'stock_qty':stock,
                     'order_qty':oq,'department_id':did})
    # その日の入力を入れ替え（送られてこなかった商品は削除＝未入力扱い）
    sb.table('hq_material_orders').delete().eq('department_id',did).eq('date',target_date).execute()
    if rows:
        sb.table('hq_material_orders').insert(rows).execute()
    return jsonify({'ok': True, 'count': len(rows)})

# ═══════════════════════════════════════════════
# 漬物部：製造 → 在庫 → 出荷 → 請求  [Phase 4]
# ═══════════════════════════════════════════════
# ─── 製造・入庫（在庫を増やす）──────────────
@app.route('/api/production', methods=['GET'])
def get_production():
    did = dept_id()
    q = sb.table('hq_production').select('*').eq('department_id',did)
    if request.args.get('date'):
        q = q.eq('date', request.args['date'])
    if request.args.get('kind'):
        q = q.eq('kind', request.args['kind'])
    return jsonify(q.order('id').execute().data)

@app.route('/api/production/bulk', methods=['POST'])
def save_production():
    d = request.json or {}
    did = dept_id()
    target_date = d.get('date'); kind = d.get('kind','manufacture')
    if not target_date:
        return jsonify({'ok': False, 'error': 'date が必要です'}), 400
    # 当該日・区分の入力を入れ替え（数量0は未入力扱いで削除）
    sb.table('hq_production').delete().eq('department_id',did).eq('date',target_date).eq('kind',kind).execute()
    rows = [{'department_id':did,'date':target_date,'kind':kind,
             'product_id':it['product_id'],'qty':it.get('qty',0) or 0,'note':it.get('note','')}
            for it in d.get('items',[]) if it.get('product_id') and (it.get('qty') or 0) != 0]
    if rows:
        sb.table('hq_production').insert(rows).execute()
    return jsonify({'ok': True, 'count': len(rows)})

# ─── 在庫（製造・入庫の累計 − 出荷済の累計）──
@app.route('/api/inventory', methods=['GET'])
def get_inventory():
    did = dept_id()
    prods = sb.table('hq_products').select('id,name,category').eq('department_id',did).eq('active',1).order('id').execute().data
    prod_rows = sb.table('hq_production').select('product_id,qty').eq('department_id',did).execute().data
    ship_rows = sb.table('hq_shipments').select('product_id,qty,status').eq('department_id',did).execute().data
    in_map = {}
    for r in prod_rows:
        in_map[r['product_id']] = in_map.get(r['product_id'],0) + (r['qty'] or 0)
    shipped, reserved = {}, {}
    for s in ship_rows:
        tgt = shipped if s['status'] == 'shipped' else reserved
        tgt[s['product_id']] = tgt.get(s['product_id'],0) + (s['qty'] or 0)
    out = []
    for p in prods:
        i = in_map.get(p['id'],0); sh = shipped.get(p['id'],0); rv = reserved.get(p['id'],0)
        out.append({'product_id':p['id'],'name':p['name'],'category':p.get('category',''),
                    'in_qty':i,'shipped_qty':sh,'on_hand':i-sh,'reserved':rv,'available':i-sh-rv})
    return jsonify(out)

# ─── 出荷先×商品の単価表 ─────────────────────
@app.route('/api/product-prices', methods=['GET'])
def get_product_prices():
    did = dept_id()
    q = sb.table('hq_product_prices').select('*').eq('department_id',did)
    if request.args.get('channel_id'):
        q = q.eq('channel_id', request.args['channel_id'])
    return jsonify(q.execute().data)

@app.route('/api/product-prices/bulk', methods=['POST'])
def save_product_prices():
    d = request.json or {}
    did = dept_id(); ch = d.get('channel_id')
    if not ch:
        return jsonify({'ok': False, 'error': 'channel_id が必要です'}), 400
    sb.table('hq_product_prices').delete().eq('department_id',did).eq('channel_id',ch).execute()
    rows = [{'department_id':did,'channel_id':ch,'product_id':it['product_id'],'price':it.get('price',0) or 0}
            for it in d.get('items',[]) if it.get('product_id') and (it.get('price') or 0) > 0]
    if rows:
        sb.table('hq_product_prices').insert(rows).execute()
    return jsonify({'ok': True, 'count': len(rows)})

def _price_for(did, channel_id, product_id):
    r = sb.table('hq_product_prices').select('price').eq('department_id',did)\
        .eq('channel_id',channel_id).eq('product_id',product_id).limit(1).execute().data
    return r[0]['price'] if r else 0

# ─── 出荷登録（登録 → 出荷確定の2段階）───────
@app.route('/api/shipments', methods=['GET'])
def get_shipments():
    did = dept_id()
    q = sb.table('hq_shipments').select('*').eq('department_id',did)
    if request.args.get('from'):       q = q.gte('order_date', request.args['from'])
    if request.args.get('to'):         q = q.lte('order_date', request.args['to'])
    if request.args.get('status'):     q = q.eq('status', request.args['status'])
    if request.args.get('channel_id'): q = q.eq('channel_id', request.args['channel_id'])
    return jsonify(q.order('order_date', desc=True).order('id', desc=True).execute().data)

@app.route('/api/shipments', methods=['POST'])
def add_shipment():
    d = request.json or {}; did = dept_id()
    for f in ('order_date','channel_id','product_id'):
        if not d.get(f):
            return jsonify({'ok': False, 'error': f'{f} が必要です'}), 400
    up = d.get('unit_price')
    if up in (None, ''):
        up = _price_for(did, d['channel_id'], d['product_id'])
    sb.table('hq_shipments').insert({
        'department_id':did,'order_date':d['order_date'],'channel_id':d['channel_id'],
        'product_id':d['product_id'],'qty':d.get('qty',0) or 0,'unit_price':up or 0,
        'status':'registered','note':d.get('note','')
    }).execute()
    return jsonify({'ok': True})

@app.route('/api/shipments/<int:sid>', methods=['PUT'])
def update_shipment(sid):
    d = request.json or {}; did = dept_id()
    upd = {f: d[f] for f in ('order_date','channel_id','product_id','qty','unit_price','note','status','shipped_date') if f in d}
    if upd:
        sb.table('hq_shipments').update(upd).eq('id',sid).eq('department_id',did).execute()
    return jsonify({'ok': True})

@app.route('/api/shipments/<int:sid>/ship', methods=['POST'])
def ship_shipment(sid):
    d = request.json or {}; did = dept_id()
    shipped = d.get('shipped_date') or date.today().isoformat()
    sb.table('hq_shipments').update({'status':'shipped','shipped_date':shipped})\
        .eq('id',sid).eq('department_id',did).execute()
    return jsonify({'ok': True})

@app.route('/api/shipments/<int:sid>', methods=['DELETE'])
def delete_shipment(sid):
    sb.table('hq_shipments').delete().eq('id',sid).eq('department_id',dept_id()).execute()
    return jsonify({'ok': True})

# ─── 請求書（出荷先×月、軽減税率8%・税込）──
@app.route('/api/invoices', methods=['GET'])
def get_invoices():
    did = dept_id()
    month = request.args.get('month'); ch = request.args.get('channel_id')
    if not month:
        return jsonify({'ok': False, 'error': 'month が必要です'}), 400
    y, m = map(int, month.split('-'))
    last = calendar.monthrange(y, m)[1]
    start, end = f'{month}-01', f'{month}-{last:02d}'
    q = sb.table('hq_shipments').select('*').eq('department_id',did).eq('status','shipped')\
        .gte('shipped_date',start).lte('shipped_date',end)
    if ch:
        q = q.eq('channel_id', ch)
    ships = q.execute().data
    prods = {p['id']:p['name'] for p in sb.table('hq_products').select('id,name').eq('department_id',did).execute().data}
    chans = {c['id']:c['name'] for c in sb.table('hq_channels').select('id,name').eq('department_id',did).execute().data}
    by = {}
    for s in ships:
        amt = (s['qty'] or 0) * (s['unit_price'] or 0)
        by.setdefault(s['channel_id'], []).append({
            'shipped_date':s['shipped_date'],'product_id':s['product_id'],
            'product_name':prods.get(s['product_id'],''),'qty':s['qty'],
            'unit_price':s['unit_price'],'amount':amt})
    out = []
    for cid, lines in by.items():
        lines.sort(key=lambda x:(x['shipped_date'] or '', x['product_name']))
        subtotal = sum(l['amount'] for l in lines)
        tax = int(math.floor(subtotal * 0.08))
        out.append({'channel_id':cid,'channel_name':chans.get(cid,''),'month':month,
                    'lines':lines,'subtotal':subtotal,'tax':tax,'total':subtotal+tax})
    out.sort(key=lambda x:x['channel_name'])
    return jsonify(out)

# ─── 製造数ベースの月次・年次サマリ（漬物部）──
@app.route('/api/production-summary', methods=['GET'])
def production_summary():
    did = dept_id()
    month = request.args.get('month'); year = request.args.get('year')
    prods = {p['id']:p for p in sb.table('hq_products').select('id,name,category,price').eq('department_id',did).execute().data}
    def price_of(pid): return (prods.get(pid,{}).get('price') or 0)
    if month:
        y, m = map(int, month.split('-')); last = calendar.monthrange(y, m)[1]
        rows = sb.table('hq_production').select('date,product_id,qty').eq('department_id',did).eq('kind','manufacture')\
            .gte('date',f'{month}-01').lte('date',f'{month}-{last:02d}').execute().data
        per = {}
        for r in rows:
            per[r['product_id']] = per.get(r['product_id'],0) + (r['qty'] or 0)
        products, total_qty, total_val = [], 0, 0
        for pid, qty in per.items():
            p = prods.get(pid,{}); price = price_of(pid); val = qty*price
            products.append({'product_id':pid,'name':p.get('name',''),'category':p.get('category',''),
                             'qty':qty,'price':price,'amount':val})
            total_qty += qty; total_val += val
        products.sort(key=lambda x:(x['category'],x['name']))
        daily = {}
        for r in rows:
            daily[r['date']] = daily.get(r['date'],0) + (r['qty'] or 0)*price_of(r['product_id'])
        days = [{'date':k,'amount':daily[k]} for k in sorted(daily)]
        return jsonify({'month':month,'products':products,'days':days,'total_qty':total_qty,'total_amount':total_val})
    if year:
        rows = sb.table('hq_production').select('date,product_id,qty').eq('department_id',did).eq('kind','manufacture')\
            .gte('date',f'{year}-01-01').lte('date',f'{year}-12-31').execute().data
        per, months_total, total_qty, total_val = {}, {}, 0, 0
        for r in rows:
            pid = r['product_id']; mo = r['date'][:7]; qty = r['qty'] or 0; price = price_of(pid)
            per.setdefault(pid,{})[mo] = per.setdefault(pid,{}).get(mo,0) + qty
            months_total[mo] = months_total.get(mo,0) + qty*price
            total_qty += qty; total_val += qty*price
        products = []
        for pid, mm in per.items():
            p = prods.get(pid,{}); price = price_of(pid); tot = sum(mm.values())
            products.append({'product_id':pid,'name':p.get('name',''),'category':p.get('category',''),
                             'price':price,'months':mm,'qty':tot,'amount':tot*price})
        products.sort(key=lambda x:(x['category'],x['name']))
        return jsonify({'year':year,'products':products,'months':[f'{year}-{i:02d}' for i in range(1,13)],
                        'months_total':months_total,'total_qty':total_qty,'total_amount':total_val})
    return jsonify({'error':'month or year required'}), 400

# ─── カテゴリマスタ ───────────────────────────
@app.route('/api/categories', methods=['GET'])
def get_categories():
    q = sb.table('hq_categories').select('*').eq('department_id',dept_id())
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
    did = dept_id()
    r = sb.table('hq_categories').select('sort_order').eq('department_id',did).order('sort_order', desc=True).limit(1).execute()
    max_order = r.data[0]['sort_order'] if r.data else 0
    try:
        sb.table('hq_categories').insert({'name':name,'sort_order':max_order+1,'department_id':did}).execute()
    except Exception as e:
        return jsonify({'ok': False, 'error': '同名のカテゴリが既にあります'}), 400
    return jsonify({'ok': True})

@app.route('/api/categories/<int:cid>', methods=['PUT'])
def update_category(cid):
    d = request.json
    did = dept_id()
    new_name = (d.get('name') or '').strip()
    if not new_name:
        return jsonify({'ok': False, 'error': 'カテゴリ名が空です'}), 400
    old = sb.table('hq_categories').select('name').eq('id',cid).eq('department_id',did).execute().data
    if not old:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    old_name = old[0]['name']
    sb.table('hq_categories').update({
        'name':new_name,'sort_order':d.get('sort_order',0),'active':d.get('active',1)
    }).eq('id',cid).eq('department_id',did).execute()
    # リネーム時は使用中商品もカスケード更新（同一部署内のみ）
    if old_name != new_name:
        sb.table('hq_products').update({'category':new_name}).eq('category',old_name).eq('department_id',did).execute()
    return jsonify({'ok': True})

@app.route('/api/categories/<int:cid>', methods=['DELETE'])
def delete_category(cid):
    did = dept_id()
    r = sb.table('hq_categories').select('name').eq('id',cid).eq('department_id',did).execute().data
    if not r:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    name = r[0]['name']
    used = sb.table('hq_products').select('id').eq('category',name).eq('department_id',did).execute().data
    if used:
        return jsonify({'ok': False, 'error': f'使用中の商品が{len(used)}件あるため削除できません'}), 400
    sb.table('hq_subcategories').delete().eq('category_id',cid).execute()
    sb.table('hq_categories').delete().eq('id',cid).eq('department_id',did).execute()
    return jsonify({'ok': True})

@app.route('/api/subcategories', methods=['GET'])
def get_subcategories():
    q = sb.table('hq_subcategories').select('*').eq('department_id',dept_id())
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
    did = dept_id()
    r = sb.table('hq_subcategories').select('sort_order').eq('category_id',cat_id).order('sort_order', desc=True).limit(1).execute()
    max_order = r.data[0]['sort_order'] if r.data else 0
    try:
        sb.table('hq_subcategories').insert({'category_id':cat_id,'name':name,'sort_order':max_order+1,'department_id':did}).execute()
    except Exception:
        return jsonify({'ok': False, 'error': '同名のサブカテゴリが既にあります'}), 400
    return jsonify({'ok': True})

@app.route('/api/subcategories/<int:sid>', methods=['PUT'])
def update_subcategory(sid):
    d = request.json
    new_name = (d.get('name') or '').strip()
    if not new_name:
        return jsonify({'ok': False, 'error': 'サブカテゴリ名が空です'}), 400
    did = dept_id()
    old = sb.table('hq_subcategories').select('name,category_id').eq('id',sid).eq('department_id',did).execute().data
    if not old:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    old_name = old[0]['name']
    cat_id   = old[0]['category_id']
    sb.table('hq_subcategories').update({
        'name':new_name,'sort_order':d.get('sort_order',0),'active':d.get('active',1)
    }).eq('id',sid).eq('department_id',did).execute()
    # 同カテゴリ内のリネームのみカスケード
    if old_name != new_name:
        cat = sb.table('hq_categories').select('name').eq('id',cat_id).execute().data
        if cat:
            sb.table('hq_products').update({'subcategory':new_name}).eq('category',cat[0]['name']).eq('subcategory',old_name).eq('department_id',did).execute()
    return jsonify({'ok': True})

@app.route('/api/subcategories/<int:sid>', methods=['DELETE'])
def delete_subcategory(sid):
    did = dept_id()
    r = sb.table('hq_subcategories').select('name,category_id').eq('id',sid).eq('department_id',did).execute().data
    if not r:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    name   = r[0]['name']
    cat_id = r[0]['category_id']
    cat = sb.table('hq_categories').select('name').eq('id',cat_id).execute().data
    if cat:
        used = sb.table('hq_products').select('id').eq('category',cat[0]['name']).eq('subcategory',name).eq('department_id',did).execute().data
        if used:
            return jsonify({'ok': False, 'error': f'使用中の商品が{len(used)}件あるため削除できません'}), 400
    sb.table('hq_subcategories').delete().eq('id',sid).eq('department_id',did).execute()
    return jsonify({'ok': True})

# ─── 週間献立 ──────────────────────────────────
@app.route('/api/weekly-menus', methods=['GET'])
def get_weekly_menus():
    week_start = request.args.get('week_start')
    r = sb.table('hq_weekly_menus').select('*').eq('department_id',dept_id()).eq('week_start',week_start).order('day_of_week').order('id').execute()
    return jsonify(r.data)

@app.route('/api/weekly-menus', methods=['POST'])
def save_weekly_menus():
    d = request.json
    did = dept_id()
    sb.table('hq_weekly_menus').delete().eq('department_id',did).eq('week_start',d['week_start']).execute()
    rows = [{'week_start':d['week_start'],'day_of_week':m['day_of_week'],'category':m['category'],'menu_name':m['menu_name'],'department_id':did}
            for m in d['menus'] if m.get('menu_name','').strip()]
    if rows:
        sb.table('hq_weekly_menus').insert(rows).execute()
    return jsonify({'ok': True})

@app.route('/api/weekly-menus/week-copy', methods=['POST'])
def copy_weekly_menus():
    d = request.json
    did = dept_id()
    src, dst = d['src_start'], d['dst_start']
    r = sb.table('hq_weekly_menus').select('*').eq('department_id',did).eq('week_start',src).execute()
    sb.table('hq_weekly_menus').delete().eq('department_id',did).eq('week_start',dst).execute()
    rows = [{'week_start':dst,'day_of_week':m['day_of_week'],'category':m['category'],'menu_name':m['menu_name'],'department_id':did} for m in r.data]
    if rows:
        sb.table('hq_weekly_menus').insert(rows).execute()
    return jsonify({'ok': True, 'copied': len(rows)})

# ─── 出荷指示書（計画）────────────────────────
@app.route('/api/shipping-plans', methods=['GET'])
def get_shipping_plans():
    date_from = request.args.get('date_from')
    date_to   = request.args.get('date_to', date_from)
    plans = sb.table('hq_shipping_plans').select('*').eq('department_id',dept_id()).gte('date',date_from).lte('date',date_to).execute().data
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
    payload = request.json
    # 新フォーマット {dates, items} と旧フォーマット [items] の両対応
    if isinstance(payload, list):
        items = payload
        dates = list({d['date'] for d in items})
    else:
        items = payload.get('items', [])
        dates = payload.get('dates') or list({d['date'] for d in items})
    did = dept_id()
    # スパース化：送信日付の既存行を一度全削除し、planned_qty>0 のみ再挿入する。
    for date in dates:
        sb.table('hq_shipping_plans').delete().eq('department_id',did).eq('date',date).execute()
    rows = [{'date':d['date'],'product_id':d['product_id'],'channel_id':d['channel_id'],
             'planned_qty':d['planned_qty'],'note':d.get('note',''),'department_id':did}
            for d in items if d['planned_qty'] > 0]
    if rows:
        sb.table('hq_shipping_plans').insert(rows).execute()
    return jsonify({'ok': True, 'count': len(rows)})

@app.route('/api/shipping-plans/week-copy', methods=['POST'])
def copy_week_plan():
    d         = request.json
    src_from  = d['src_from']; src_to = d['src_to']; dst_from = d['dst_from']
    did       = dept_id()
    diff      = (datetime.strptime(dst_from,'%Y-%m-%d') - datetime.strptime(src_from,'%Y-%m-%d')).days
    # Supabase のデフォルト1000行制限に当たって週後半（金・土）が欠ける問題を避けるため
    # 日付ごとにクエリを分割。planned_qty=0 の行はコピー対象外。
    rows = []
    cur = datetime.strptime(src_from,'%Y-%m-%d')
    end = datetime.strptime(src_to,  '%Y-%m-%d')
    while cur <= end:
        day_str = cur.strftime('%Y-%m-%d')
        rows.extend(sb.table('hq_shipping_plans').select('*').eq('department_id',did).eq('date',day_str).gt('planned_qty',0).execute().data)
        cur += timedelta(days=1)
    new_rows  = [{'date':(datetime.strptime(r['date'],'%Y-%m-%d')+timedelta(days=diff)).strftime('%Y-%m-%d'),
                  'product_id':r['product_id'],'channel_id':r['channel_id'],'planned_qty':r['planned_qty'],'department_id':did} for r in rows]
    if new_rows:
        sb.table('hq_shipping_plans').upsert(new_rows, on_conflict='date,product_id,channel_id').execute()
    return jsonify({'ok': True, 'copied': len(rows)})

# ─── 出荷実績 ─────────────────────────────────
@app.route('/api/shipping-actuals', methods=['GET'])
def get_shipping_actuals():
    target_date = request.args.get('date')
    did = dept_id()
    actuals = sb.table('hq_shipping_actuals').select('*').eq('department_id',did).eq('date',target_date).execute().data
    plans   = sb.table('hq_shipping_plans').select('product_id,channel_id,planned_qty').eq('department_id',did).eq('date',target_date).execute().data
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
    body = request.json or {}
    target_date = body.get('date')
    # only_empty=True: 既に実績が入っている (product_id, channel_id) は触らず、
    # 空欄（未入力）のセルだけ計画値で補完する（調整保存→実績反映で手入力を消さないため）。
    only_empty = bool(body.get('only_empty'))
    did = dept_id()
    plans = sb.table('hq_shipping_plans').select('*').eq('department_id',did).eq('date',target_date).gt('planned_qty',0).execute().data
    pids  = list({p['product_id'] for p in plans})
    prods = {p['id']:p for p in sb.table('hq_products').select('id,price').in_('id',pids).execute().data} if pids else {}
    filled = set()
    if only_empty:
        ex = sb.table('hq_shipping_actuals').select('product_id,channel_id,actual_qty')\
            .eq('department_id',did).eq('date',target_date).execute().data
        filled = {(r['product_id'], r['channel_id']) for r in ex if (r.get('actual_qty') or 0) > 0}
    rows  = [{'date':target_date,'product_id':p['product_id'],'channel_id':p['channel_id'],
              'actual_qty':p['planned_qty'],'actual_amount':p['planned_qty']*prods.get(p['product_id'],{}).get('price',0),'department_id':did}
             for p in plans if (p['product_id'], p['channel_id']) not in filled]
    if rows:
        sb.table('hq_shipping_actuals').upsert(rows, on_conflict='date,product_id,channel_id').execute()
    return jsonify({'ok': True})

@app.route('/api/shipping-actuals/bulk', methods=['POST'])
def bulk_save_actuals():
    payload = request.json
    # 新フォーマット {dates, items} と旧フォーマット [items] の両対応
    if isinstance(payload, list):
        items = payload
        dates = list({d['date'] for d in items})
    else:
        items = payload.get('items', [])
        dates = payload.get('dates') or list({d['date'] for d in items})
    dep = get_dept(); did = dep['id']; cfg = dep.get('config') or {}
    pids  = list({d['product_id'] for d in items})
    prods = {p['id']:p['price'] for p in sb.table('hq_products').select('id,price').in_('id',pids).execute().data} if pids else {}
    def price_of(d):
        up = d.get('unit_price')
        return up if up is not None and up != '' else prods.get(d['product_id'], 0)
    # スパース化：送信日付の既存行を一度削除し、actual_qty>0 の行だけ再挿入する。
    for date in dates:
        sb.table('hq_shipping_actuals').delete().eq('department_id',did).eq('date',date).execute()
    rows = [{'date':d['date'],'product_id':d['product_id'],'channel_id':d['channel_id'],
             'actual_qty':d['actual_qty'],'actual_amount':d['actual_qty']*price_of(d),'department_id':did}
            for d in items if d['actual_qty'] > 0]
    if rows:
        sb.table('hq_shipping_actuals').insert(rows).execute()

    # 既存日報があれば自動で再生成（カード値・月次集計を実績と一致させる）
    # dates は payload からの明示的リストを使う（全クリア時にも sync を効かせるため）
    # ただし確定済み（finalized_at IS NOT NULL）は上書きしない
    sync_keys = ['total_sales','material_cost','labor_cost','expense','profit','labor_productivity',
                 'total_hours','west_sales','south_sales','other_sales','separate_orders']
    for date in dates:
        stored = sb.table('hq_daily_reports').select('expense,finalized_at').eq('department_id',did).eq('date',date).execute().data
        if not stored or stored[0].get('finalized_at'):
            continue
        calc = calc_daily_report(date, did, cfg, expense_override=stored[0].get('expense'))
        sb.table('hq_daily_reports').update({k:calc[k] for k in sync_keys}).eq('department_id',did).eq('date',date).execute()
    return jsonify({'ok': True})

# ─── メンバーマスタ ───────────────────────────
@app.route('/api/members', methods=['GET'])
def get_members():
    r = sb.table('hq_members').select('*').eq('active',1).order('id').execute()
    return jsonify(r.data)

@app.route('/api/members', methods=['POST'])
def add_member():
    if not is_hq_request():
        return jsonify({'ok': False, 'error': 'メンバー編集は本部のみ可能です'}), 403
    d = request.json
    sb.table('hq_members').insert({'name':d['name'],'hourly_wage':d.get('hourly_wage',0)}).execute()
    return jsonify({'ok': True})

@app.route('/api/members/<int:mid>', methods=['PUT'])
def update_member(mid):
    if not is_hq_request():
        return jsonify({'ok': False, 'error': 'メンバー編集は本部のみ可能です'}), 403
    d = request.json
    sb.table('hq_members').update({'name':d['name'],'hourly_wage':d.get('hourly_wage',0),'active':d.get('active',1)}).eq('id',mid).execute()
    return jsonify({'ok': True})

# ─── チェックリスト（衛生管理：HACCP＋α） ──────
# period_type: 'daily' | 'monthly'
# period_key : daily='YYYY-MM-DD' / monthly='YYYY-MM'
@app.route('/api/checklist', methods=['GET'])
def get_checklist():
    pt = request.args.get('period_type')
    pk = request.args.get('period_key')
    r = sb.table('hq_checklist_records').select('*').eq('department_id',dept_id()).eq('period_type',pt).eq('period_key',pk).execute()
    return jsonify(r.data)

@app.route('/api/checklist', methods=['POST'])
def save_checklist():
    d  = request.json
    did = dept_id()
    pt = d['period_type']
    pk = d['period_key']
    by = d.get('checked_by','')
    sb.table('hq_checklist_records').delete().eq('department_id',did).eq('period_type',pt).eq('period_key',pk).execute()
    rows = [{'period_type':pt,'period_key':pk,'item_key':it['item_key'],'checked':True,'checked_by':by,'department_id':did}
            for it in d.get('items',[]) if it.get('checked')]
    if rows:
        sb.table('hq_checklist_records').insert(rows).execute()
    return jsonify({'ok': True})

# ─── シフト ────────────────────────────────────
@app.route('/api/shifts', methods=['GET'])
def get_shifts():
    r = sb.table('hq_shifts').select('*').eq('department_id',dept_id()).eq('date',request.args.get('date')).order('member_name').execute()
    return jsonify(r.data)

@app.route('/api/shifts', methods=['POST'])
def save_shifts():
    d = request.json
    dep = get_dept(); did = dep['id']; cfg = dep.get('config') or {}
    date = d['date']
    sb.table('hq_shifts').delete().eq('department_id',did).eq('date',date).execute()
    rows = [{'date':date,'member_name':s['member_name'],'hours':s['hours'],'department_id':did}
            for s in d['shifts'] if s.get('member_name','').strip() and s.get('hours',0)>0]
    if rows:
        sb.table('hq_shifts').insert(rows).execute()

    # 既存日報があれば自動で再生成（人件費・利益・人時売を実績と一致させる）
    # 確定済みは上書きしない
    stored = sb.table('hq_daily_reports').select('expense,finalized_at').eq('department_id',did).eq('date',date).execute().data
    if stored and not stored[0].get('finalized_at'):
        calc = calc_daily_report(date, did, cfg, expense_override=stored[0].get('expense'))
        sync_keys = ['total_sales','material_cost','labor_cost','expense','profit','labor_productivity',
                     'total_hours','west_sales','south_sales','other_sales','separate_orders']
        sb.table('hq_daily_reports').update({k:calc[k] for k in sync_keys}).eq('department_id',did).eq('date',date).execute()
    return jsonify({'ok': True})

# ─── シフト週次管理（予定） ────────────────────
@app.route('/api/shift-plans', methods=['GET'])
def get_shift_plans():
    r = sb.table('hq_shift_plans').select('*').eq('department_id',dept_id()).eq('date',request.args.get('date')).order('member_name').execute()
    return jsonify(r.data)

@app.route('/api/shifts/week', methods=['GET'])
def get_shifts_week():
    week_start = request.args.get('week_start')
    start  = datetime.strptime(week_start,'%Y-%m-%d')
    dates  = [(start+timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    r = sb.table('hq_shift_plans').select('*').eq('department_id',dept_id()).in_('date',dates).execute()
    return jsonify(r.data)

@app.route('/api/shifts/week-bulk', methods=['POST'])
def save_shifts_week_bulk():
    d     = request.json
    did   = dept_id()
    start = datetime.strptime(d['week_start'],'%Y-%m-%d')
    dates = [(start+timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    sb.table('hq_shift_plans').delete().eq('department_id',did).in_('date',dates).execute()
    rows = [{'date':s['date'],'member_name':s['member_name'],'planned_hours':s['hours'],'department_id':did}
            for s in d.get('shifts',[]) if s.get('hours',0)>0]
    if rows:
        sb.table('hq_shift_plans').insert(rows).execute()
    return jsonify({'ok': True})

@app.route('/api/shifts/week-copy', methods=['POST'])
def copy_shifts_week():
    d         = request.json
    did       = dept_id()
    src       = datetime.strptime(d['src_start'],'%Y-%m-%d')
    dst       = datetime.strptime(d['dst_start'],'%Y-%m-%d')
    src_dates = [(src+timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    dst_dates = [(dst+timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6)]
    rows      = sb.table('hq_shift_plans').select('*').eq('department_id',did).in_('date',src_dates).execute().data
    sb.table('hq_shift_plans').delete().eq('department_id',did).in_('date',dst_dates).execute()
    new_rows = [{'date':dst_dates[src_dates.index(r['date'])],'member_name':r['member_name'],'planned_hours':r['planned_hours'],'department_id':did} for r in rows]
    if new_rows:
        sb.table('hq_shift_plans').insert(new_rows).execute()
    return jsonify({'ok': True, 'copied': len(rows)})

# ─── 日報 ─────────────────────────────────────
def _calc_production_report(target_date, did, cfg, expense_override=None):
    """製造フロー部署（漬物部）の日報計算。
    売上(製造高)＝自社製造(kind=manufacture)の数量×商品マスタ単価。
    委託入庫(consignment)は在庫のみで売上には含めない。人件費はシフト×時給。"""
    material_rate = cfg.get('material_rate', 0.5)
    fixed_cost    = cfg.get('monthly_fixed_cost')
    prod = sb.table('hq_production').select('product_id,qty')\
        .eq('department_id',did).eq('date',target_date).eq('kind','manufacture').execute().data
    pids = list({r['product_id'] for r in prod})
    price_map = {}
    if pids:
        price_map = {p['id']: (p.get('price') or 0)
                     for p in sb.table('hq_products').select('id,price').in_('id',pids).execute().data}
    total = sum((r['qty'] or 0) * price_map.get(r['product_id'], 0) for r in prod)
    shifts_data = sb.table('hq_shifts').select('hours,member_name').eq('department_id',did).eq('date',target_date).execute().data
    total_hours = sum(s['hours'] for s in shifts_data)
    wage_map   = {m['name']:m['hourly_wage'] for m in sb.table('hq_members').select('name,hourly_wage').execute().data}
    labor_cost = sum(int(s['hours']*wage_map.get(s['member_name'],0)) for s in shifts_data)
    material   = int(total * material_rate)
    expense    = expense_override if expense_override is not None else daily_expense(target_date, fixed_cost)
    profit     = total - material - labor_cost - expense
    labor_prod = (total/total_hours) if total_hours>0 else 0
    return {'date':target_date,'total_sales':total,'material_cost':material,'labor_cost':labor_cost,
            'expense':expense,'profit':profit,'labor_productivity':round(labor_prod,1),
            'total_hours':total_hours,'west_sales':0,'south_sales':0,'other_sales':total,
            'separate_orders':0}

def calc_daily_report(target_date, did, cfg, expense_override=None):
    # cfg（部署設定）で材料費率・固定費・特売チャネル名・各機能の有無を切り替える。
    # 餅部・漬物部は features 空・sales_split 空のため、店別内訳/別注/NPO/DX合算は行われない。
    cfg = cfg or {}
    feats = cfg.get('features') or {}
    # 製造フロー部署（漬物部）は製造数×単価ベースで算出（出荷/NPO/別注/DXは使わない）
    if feats.get('production'):
        return _calc_production_report(target_date, did, cfg, expense_override)
    split = cfg.get('sales_split') or {}
    west_name     = split.get('west')
    south_name    = split.get('south')
    material_rate = cfg.get('material_rate', 0.5)
    fixed_cost    = cfg.get('monthly_fixed_cost')

    # 非活性の出荷先は売上集計から除外（売上明細マトリクスと一致させるため）
    active_chs   = sb.table('hq_channels').select('id,name').eq('department_id',did).eq('active',1).execute().data
    active_cids  = {c['id'] for c in active_chs}
    channels     = {c['id']:c['name'] for c in active_chs}
    actuals_all  = sb.table('hq_shipping_actuals').select('actual_amount,product_id,channel_id').eq('department_id',did).eq('date',target_date).execute().data
    actuals      = [r for r in actuals_all if r['channel_id'] in active_cids]
    total        = sum(r['actual_amount'] for r in actuals)
    west  = sum(r['actual_amount'] for r in actuals if west_name  and channels.get(r['channel_id'])==west_name)
    south = sum(r['actual_amount'] for r in actuals if south_name and channels.get(r['channel_id'])==south_name)

    # dx 店頭注文の合算（dx_orders 機能のある部署：弁当=餅以外 / 餅部=餅のみ。取込時にカテゴリ振り分け済み）
    if feats.get('dx_orders'):
        has_split = bool(west_name or south_name)
        instore_rows = sb.table('hq_instore_orders').select('store_id,quantity,price').eq('department_id',did).eq('date',target_date).execute().data
        # store_id は全社共通の物理店舗ID。店名を全部署横断で解決し、店名で西/南へ振り分ける
        # （餅部のチャネルIDは弁当部と異なるため、ID一致ではなく店名で判定する）
        name_by_sid = {}
        if has_split:
            sids = list({r.get('store_id') for r in instore_rows if r.get('store_id') is not None})
            if sids:
                for c in sb.table('hq_channels').select('id,name').in_('id', sids).execute().data:
                    name_by_sid[c['id']] = c['name']
        for r in instore_rows:
            amt = int(round(float(r.get('quantity') or 0)) * round(float(r.get('price') or 0)))
            total += amt
            if has_split:
                cname = name_by_sid.get(r.get('store_id'))
                if   west_name  and cname == west_name:  west  += amt
                elif south_name and cname == south_name: south += amt

        # bento システム注文（配達弁当）は弁当アプリ由来。店頭カテゴリ限定の部署（餅部）では取り込まない
        bento_orders = []
        if not cfg.get('dx_instore_only'):
            try:
                bento_orders = sb.table('orders').select('product_id,quantity')\
                    .eq('delivery_date',target_date).execute().data
            except Exception:
                bento_orders = []
        if bento_orders:
            bento_pids = list({o['product_id'] for o in bento_orders if o.get('product_id')})
            try:
                bento_prods = sb.table('products').select('*').in_('id', bento_pids).execute().data
            except Exception:
                bento_prods = []
            def _bp_price(p):
                for k in ('price','unit_price'):
                    v = p.get(k)
                    if v is not None:
                        try:    return int(round(float(v)))
                        except: pass
                return 0
            bp_price = {p['id']: _bp_price(p) for p in bento_prods}
            for o in bento_orders:
                amt = int(round(float(o.get('quantity') or 0))) * bp_price.get(o.get('product_id'), 0)
                total += amt  # 配達は西/南以外なので other に入る

    other = total - west - south

    if feats.get('separate_orders'):
        betch_pids = {p['id'] for p in sb.table('hq_products').select('id').eq('department_id',did).like('name','別注%').execute().data}
        separate_orders = int(sum(r['actual_amount'] for r in actuals if r['product_id'] in betch_pids))
    else:
        separate_orders = 0

    shifts_data = sb.table('hq_shifts').select('hours,member_name').eq('department_id',did).eq('date',target_date).execute().data
    total_hours = sum(s['hours'] for s in shifts_data)

    if feats.get('npo_adjust'):
        npo_pids = {p['id'] for p in sb.table('hq_products').select('id').eq('department_id',did).like('name','NPO%').execute().data}
        npo      = sum(r['actual_amount'] for r in actuals if r['product_id'] in npo_pids)
        total_with_npo = total + int(npo * 0.08)
    else:
        total_with_npo = total

    material = int(total_with_npo * material_rate)
    expense  = expense_override if expense_override is not None else daily_expense(target_date, fixed_cost)

    # メンバー（時給）は全部署共通マスタ
    wage_map   = {m['name']:m['hourly_wage'] for m in sb.table('hq_members').select('name,hourly_wage').execute().data}
    labor_cost = sum(int(s['hours']*wage_map.get(s['member_name'],0)) for s in shifts_data)
    profit     = total_with_npo - material - labor_cost - expense
    labor_prod = (total_with_npo/total_hours) if total_hours>0 else 0

    return {'date':target_date,'total_sales':total,'material_cost':material,'labor_cost':labor_cost,
            'expense':expense,'profit':profit,'labor_productivity':round(labor_prod,1),
            'total_hours':total_hours,'west_sales':west,'south_sales':south,'other_sales':other,
            'separate_orders':separate_orders}

def build_snapshot(date_str, did):
    """date_str の actuals_detail / shifts / channels を現在のマスタで組み立てて返す"""
    active_chs  = sb.table('hq_channels').select('*').eq('department_id',did).eq('active',1).order('sort_order').execute().data
    active_cids = {c['id'] for c in active_chs}
    actuals = sb.table('hq_shipping_actuals').select('*').eq('department_id',did).eq('date',date_str).gt('actual_qty',0).execute().data
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
    else:
        detail = []
    shifts = sb.table('hq_shifts').select('*').eq('department_id',did).eq('date',date_str).order('member_name').execute().data
    return {'actuals_detail':detail,'shifts':shifts,'channels':active_chs}

def save_daily_report_row(data):
    """hq_daily_reports への upsert を on_conflict に依存せず行う。
    PostgREST のスキーマキャッシュ状態（複合主キーを認識できない等）に
    左右されないよう、明示的に存在チェック → update / insert する。"""
    did  = data.get('department_id')
    date = data.get('date')
    existing = sb.table('hq_daily_reports').select('date')\
        .eq('department_id', did).eq('date', date).execute().data
    if existing:
        sb.table('hq_daily_reports').update(data).eq('department_id', did).eq('date', date).execute()
    else:
        sb.table('hq_daily_reports').insert(data).execute()

def finalize_report(date_str, did, cfg):
    """date_str の日報を確定する。既に確定済みなら False。"""
    stored = sb.table('hq_daily_reports').select('expense,weather,note,finalized_at').eq('department_id',did).eq('date',date_str).execute().data
    if stored and stored[0].get('finalized_at'):
        return False
    expense_override = stored[0].get('expense') if stored else None
    calc = calc_daily_report(date_str, did, cfg, expense_override=expense_override)
    snap = build_snapshot(date_str, did)
    data = {
        'date': date_str,
        'department_id': did,
        'weather': stored[0].get('weather','') if stored else '',
        'note':    stored[0].get('note','')    if stored else '',
        **{k: calc[k] for k in ('total_sales','material_cost','labor_cost','expense','profit',
                                'labor_productivity','total_hours','west_sales','south_sales',
                                'other_sales','separate_orders')},
        'actuals_snapshot':  snap['actuals_detail'],
        'shifts_snapshot':   snap['shifts'],
        'channels_snapshot': snap['channels'],
        'finalized_at':      finalize_ts(date_str),
    }
    save_daily_report_row(data)
    return True

@app.route('/api/daily-reports/<date_str>', methods=['GET'])
def get_daily_report(date_str):
    dep = get_dept(); did = dep['id']; cfg = dep.get('config') or {}
    stored_rows = sb.table('hq_daily_reports').select('*').eq('department_id',did).eq('date',date_str).execute().data
    stored = stored_rows[0] if stored_rows else None

    # 確定済み → snapshot をそのまま返す（再計算しない）
    if stored and stored.get('finalized_at'):
        result = dict(stored)
        result['actuals_detail'] = stored.get('actuals_snapshot')  or []
        result['shifts']         = stored.get('shifts_snapshot')   or []
        result['channels']       = stored.get('channels_snapshot') or []
        return jsonify(result)

    # 過去日かつ未確定 → 即時確定（遅延確定）してから再読込
    # ただし以前確定→明示解除された日（snapshot 残存）は再確定しない（編集中扱い）
    # 製造フロー部署（漬物部）は「確定後も修正可」のため自動確定しない。
    feats = (cfg.get('features') or {})
    if (not feats.get('production')) and date_str < today_jst().isoformat() and not (stored and stored.get('actuals_snapshot')):
        finalize_report(date_str, did, cfg)
        return get_daily_report(date_str)

    # 当日以降 → 従来通り再計算
    calc = calc_daily_report(date_str, did, cfg, expense_override=stored.get('expense') if stored else None)
    if stored:
        result = {**stored, **calc}
        sync_keys = ['total_sales','material_cost','labor_cost','profit','labor_productivity',
                     'total_hours','west_sales','south_sales','other_sales','separate_orders']
        if any(stored.get(k) != calc.get(k) for k in sync_keys):
            sb.table('hq_daily_reports').update({k:calc[k] for k in sync_keys}).eq('department_id',did).eq('date',date_str).execute()
    else:
        result = calc

    snap = build_snapshot(date_str, did)
    result['actuals_detail'] = snap['actuals_detail']
    result['channels']       = snap['channels']
    result['shifts']         = snap['shifts']
    return jsonify(result)

@app.route('/api/daily-reports/<date_str>/finalize', methods=['POST'])
def finalize_daily_report(date_str):
    dep = get_dept()
    if finalize_report(date_str, dep['id'], dep.get('config') or {}):
        return jsonify({'ok': True, 'finalized_at': now_jst_iso()})
    return jsonify({'ok': False, 'error': 'already finalized'}), 409

@app.route('/api/daily-reports/<date_str>/unfinalize', methods=['POST'])
def unfinalize_daily_report(date_str):
    """確定済み日報のロックを解除して再編集可能にする。
    finalized_at を NULL に戻すが、snapshot 列は履歴として残す。"""
    did = dept_id()
    stored = sb.table('hq_daily_reports').select('finalized_at').eq('department_id',did).eq('date',date_str).execute().data
    if not stored:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    if not stored[0].get('finalized_at'):
        return jsonify({'ok': False, 'error': 'not finalized'}), 409
    sb.table('hq_daily_reports').update({'finalized_at': None}).eq('department_id',did).eq('date',date_str).execute()
    return jsonify({'ok': True})

@app.route('/api/admin/finalize-pending', methods=['POST'])
def finalize_pending():
    """過去日かつ未確定の日報を一括で確定する（cron用）"""
    secret = os.environ.get('CRON_SECRET','')
    if secret and request.headers.get('X-Cron-Secret') != secret:
        return jsonify({'error':'unauthorized'}), 401
    cutoff = today_jst().isoformat()
    # 全部署について過去日の未確定日報を確定する
    depts = sb.table('hq_departments').select('*').eq('active',1).execute().data or []
    if not depts:
        depts = [{'id':1,'code':'bento','config':{}}]
    finalized = []
    for dp in depts:
        did = dp['id']; cfg = dp.get('config') or {}
        # 製造フロー部署（漬物部）は「確定後も修正可」のため自動確定の対象外
        if (cfg.get('features') or {}).get('production'):
            continue
        rows = sb.table('hq_daily_reports').select('date').eq('department_id',did).lt('date',cutoff).is_('finalized_at','null').execute().data
        for r in rows:
            if finalize_report(r['date'], did, cfg):
                finalized.append({'dept': dp.get('code'), 'date': r['date']})
    return jsonify({'ok': True, 'finalized': finalized, 'count': len(finalized)})

@app.route('/api/admin/fix-finalized-timestamps', methods=['POST'])
def fix_finalized_timestamps():
    """既存の確定済み日報のうち過去日の finalized_at を、その営業日の
    終了時刻(23:59:59 JST)へ補正する一回限りのメンテ用エンドポイント。
    以前『確定時刻＝処理時刻(現在時刻)』で保存された行を営業日ベースに直す。"""
    secret = os.environ.get('CRON_SECRET','')
    if secret and request.headers.get('X-Cron-Secret') != secret:
        return jsonify({'error':'unauthorized'}), 401
    cutoff = today_jst().isoformat()
    rows = sb.table('hq_daily_reports').select('date,finalized_at,department_id').lt('date',cutoff).execute().data
    fixed = []
    for r in rows:
        cur = r.get('finalized_at')
        if not cur:
            continue
        want = f"{r['date']}T23:59:59+09:00"
        if str(cur).startswith(f"{r['date']}T23:59:59"):
            continue  # 既に営業日終了時刻になっている
        q = sb.table('hq_daily_reports').update({'finalized_at': want}).eq('date', r['date'])
        if r.get('department_id') is not None:
            q = q.eq('department_id', r['department_id'])
        q.execute()
        fixed.append(r['date'])
    return jsonify({'ok': True, 'fixed': len(fixed)})

@app.route('/api/daily-info/<date_str>', methods=['POST'])
def save_daily_info(date_str):
    d = request.json
    did = dept_id()
    existing = sb.table('hq_daily_reports').select('date,finalized_at').eq('department_id',did).eq('date',date_str).execute().data
    if existing:
        # 確定済みは weather / note のみ更新可
        if existing[0].get('finalized_at'):
            sb.table('hq_daily_reports').update({'weather':d.get('weather',''),'note':d.get('note','')}).eq('department_id',did).eq('date',date_str).execute()
        else:
            sb.table('hq_daily_reports').update({'weather':d.get('weather',''),'note':d.get('note',''),'separate_orders':d.get('separate_orders',0)}).eq('department_id',did).eq('date',date_str).execute()
    else:
        sb.table('hq_daily_reports').insert({'date':date_str,'department_id':did,'weather':d.get('weather',''),'note':d.get('note',''),'separate_orders':d.get('separate_orders',0),'total_sales':0,'material_cost':0,'labor_cost':0,'expense':0,'profit':0,'labor_productivity':0,'total_hours':0,'west_sales':0,'south_sales':0,'other_sales':0}).execute()
    return jsonify({'ok': True})

@app.route('/api/daily-reports/<date_str>', methods=['POST'])
def save_daily_report(date_str):
    d = request.json
    did = dept_id()
    existing = sb.table('hq_daily_reports').select('finalized_at').eq('department_id',did).eq('date',date_str).execute().data
    # 確定済みは weather / note のみ更新可
    if existing and existing[0].get('finalized_at'):
        sb.table('hq_daily_reports').update({'weather':d.get('weather',''),'note':d.get('note','')}).eq('department_id',did).eq('date',date_str).execute()
        return jsonify({'ok': True, 'locked': True})
    data = {'date':date_str,'department_id':did,'weather':d.get('weather'),'total_sales':d.get('total_sales',0),'separate_orders':d.get('separate_orders',0),
            'material_cost':d.get('material_cost',0),'labor_cost':d.get('labor_cost',0),'expense':d.get('expense',0),
            'profit':d.get('profit',0),'labor_productivity':d.get('labor_productivity',0),'total_hours':d.get('total_hours',0),
            'west_sales':d.get('west_sales',0),'south_sales':d.get('south_sales',0),'other_sales':d.get('other_sales',0),'note':d.get('note','')}
    save_daily_report_row(data)
    return jsonify({'ok': True})

@app.route('/api/daily-reports/<date_str>/generate', methods=['POST'])
def generate_daily_report(date_str):
    dep = get_dept(); did = dep['id']; cfg = dep.get('config') or {}
    existing = sb.table('hq_daily_reports').select('note,finalized_at').eq('department_id',did).eq('date',date_str).execute().data
    if existing and existing[0].get('finalized_at'):
        return jsonify({'ok': False, 'error': 'finalized'}), 409
    calc    = calc_daily_report(date_str, did, cfg)
    weather = request.json.get('weather','') if request.json else ''
    note    = request.json.get('note','')    if request.json else ''
    saved_note = existing[0]['note'] if existing else note
    data = {'date':date_str,'department_id':did,'weather':weather,'total_sales':calc['total_sales'],'separate_orders':calc['separate_orders'],
            'material_cost':calc['material_cost'],'labor_cost':calc['labor_cost'],'expense':calc['expense'],
            'profit':calc['profit'],'labor_productivity':calc['labor_productivity'],'total_hours':calc['total_hours'],
            'west_sales':calc['west_sales'],'south_sales':calc['south_sales'],'other_sales':calc['other_sales'],'note':saved_note}
    save_daily_report_row(data)
    return jsonify({'ok': True, **calc})

# ─── 売上実績の一括取込（過去データ・日次CSV由来） ──
@app.route('/api/import/sales-actuals', methods=['POST'])
def import_sales_actuals():
    """過去の売上実績を日次で hq_daily_reports に取込む。
    フロントで CSV をパースして得た rows(JSON) を受け取る。
    月次/年次サマリは hq_daily_reports の各列を直接合算しているため、
    日別行をそのまま入れるだけで集計に反映される（追加の集計ロジック不要）。
    取込んだ行は確定済み(finalized_at)として保存する。これをしないと
    get_daily_report の遅延確定で総売上等が 0 に再計算され消える。
    """
    if not is_hq_request():
        return jsonify({'ok': False, 'error': '売上実績取込は本部のみ可能です'}), 403
    payload   = request.json or {}
    rows      = payload.get('rows') or []
    overwrite = bool(payload.get('overwrite'))
    did       = dept_id()
    if not rows:
        return jsonify({'ok': False, 'error': 'no rows'}), 400

    # 既存の確定済み日付（実運用で確定済みの本物の日報）は保護する
    dates = list({str(r.get('date', '')).strip() for r in rows if r.get('date')})
    existing_finalized = set()
    for j in range(0, len(dates), 300):
        chunk = dates[j:j+300]
        ex = sb.table('hq_daily_reports').select('date,finalized_at').eq('department_id',did).in_('date', chunk).execute().data
        existing_finalized |= {e['date'] for e in ex if e.get('finalized_at')}

    def num(v):
        if v is None:
            return None
        s = re.sub(r'[,¥円\s]', '', str(v)).strip()
        if s == '':
            return None
        try:
            return float(s)
        except ValueError:
            return None

    imported, skipped, errors, upserts, seen = [], [], [], [], set()
    for i, r in enumerate(rows):
        rownum = r.get('_row', i + 1)
        raw = str(r.get('date', '')).strip().replace('/', '-')
        m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', raw)
        if not m:
            errors.append({'row': rownum, 'date': raw, 'msg': '日付形式が不正（YYYY-MM-DD）'})
            continue
        d = f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
        if d in seen:
            errors.append({'row': rownum, 'date': d, 'msg': 'CSV内で日付が重複'})
            continue
        seen.add(d)
        if d in existing_finalized and not overwrite:
            skipped.append(d)
            continue
        total_sales   = int(round(num(r.get('total_sales'))   or 0))
        west_sales    = int(round(num(r.get('west_sales'))    or 0))
        south_sales   = int(round(num(r.get('south_sales'))   or 0))
        other_sales   = int(round(num(r.get('other_sales'))   or 0))
        labor_cost    = int(round(num(r.get('labor_cost'))    or 0))
        total_hours   = round(num(r.get('total_hours'))       or 0, 2)
        material_cost = int(round(num(r.get('material_cost')) or 0))
        expense       = int(round(num(r.get('expense'))       or 0))
        profit_raw    = num(r.get('profit'))
        # 利益が空欄なら 売上 − 原価 − 人件費 − 経費 で自動計算する
        profit = int(round(profit_raw)) if profit_raw is not None \
            else (total_sales - material_cost - labor_cost - expense)
        labor_prod = round(total_sales / total_hours, 0) if total_hours > 0 else 0
        upserts.append({
            'date': d, 'department_id': did, 'weather': str(r.get('weather', '') or ''),
            'total_sales': total_sales, 'west_sales': west_sales,
            'south_sales': south_sales, 'other_sales': other_sales,
            'labor_cost': labor_cost, 'total_hours': total_hours,
            'material_cost': material_cost, 'expense': expense,
            'profit': profit, 'labor_productivity': labor_prod,
            'actuals_snapshot': [], 'shifts_snapshot': [], 'channels_snapshot': [],
            'finalized_at': finalize_ts(d),
        })
        imported.append(d)

    for j in range(0, len(upserts), 500):
        chunk = upserts[j:j+500]
        try:
            sb.table('hq_daily_reports').upsert(chunk, on_conflict='department_id,date').execute()
        except Exception:
            # PostgREST が複合主キーを認識できない等で on_conflict が失敗する場合の保険
            for row in chunk:
                save_daily_report_row(row)

    return jsonify({'ok': True, 'imported': len(imported), 'imported_dates': imported,
                    'skipped': skipped, 'errors': errors, 'overwrite': overwrite})

# ─── 月次サマリ ────────────────────────────────
def _month_range(ym):
    """ 'YYYY-MM' → (start, next_month_start) を ISO 文字列で返す """
    y, m = int(ym[:4]), int(ym[5:7])
    ny, nm = (y, m+1) if m < 12 else (y+1, 1)
    return f'{y:04d}-{m:02d}-01', f'{ny:04d}-{nm:02d}-01'

def _year_range(year):
    y = int(year)
    return f'{y:04d}-01-01', f'{y+1:04d}-01-01'

@app.route('/api/monthly-summary', methods=['GET'])
def monthly_summary():
    ym   = request.args.get('month')
    dep = get_dept(); did = dep['id']; feats = (dep.get('config') or {}).get('features') or {}
    only_cats = (dep.get('config') or {}).get('dx_instore_only')   # 餅部=["餅"]
    rows = sb.table('hq_daily_reports').select('*').eq('department_id',did).like('date',ym+'%').order('date').execute().data
    # 注文売上を期間集計。店頭注文は dx_orders 機能のある部署、bento アプリ注文は弁当部のみ
    instore_by_date = {}
    bento_by_date = {}
    if feats.get('dx_orders'):
        instore_rows = sb.table('hq_instore_orders').select('date,quantity,price').eq('department_id',did).like('date',ym+'%').execute().data
        for r in instore_rows:
            amt = int(round(float(r.get('quantity') or 0)) * round(float(r.get('price') or 0)))
            instore_by_date[r['date']] = instore_by_date.get(r['date'],0) + amt
    # bento システムの orders を月内範囲で取得（product_id 経由で price を引く）
    # delivery_date は DATE 型のため like ではなく gte/lt で範囲指定する
    start, end = _month_range(ym)
    try:
        bento_orders = sb.table('orders').select('delivery_date,product_id,quantity')\
            .gte('delivery_date',start).lt('delivery_date',end).execute().data if (feats.get('dx_orders') and not only_cats) else []
    except Exception:
        bento_orders = []
    if bento_orders:
        bento_pids = list({o['product_id'] for o in bento_orders if o.get('product_id')})
        try:
            bento_prods = sb.table('products').select('*').in_('id', bento_pids).execute().data
        except Exception:
            bento_prods = []
        def _bp_price(p):
            for k in ('price','unit_price'):
                v = p.get(k)
                if v is not None:
                    try:    return int(round(float(v)))
                    except: pass
            return 0
        bp_price = {p['id']: _bp_price(p) for p in bento_prods}
        for o in bento_orders:
            d   = (o.get('delivery_date') or '')[:10]   # timestamp の場合に備えて先頭 10 文字
            amt = int(round(float(o.get('quantity') or 0))) * bp_price.get(o.get('product_id'), 0)
            if d: bento_by_date[d] = bento_by_date.get(d,0) + amt
    if not rows:
        return jsonify({'month':ym,'days':[],'summary':{'total_instore_sales':0,'total_bento_sales':0}})
    days = rows
    for d in days:
        d['instore_sales'] = instore_by_date.get(d['date'], 0)
        d['bento_sales']   = bento_by_date.get(d['date'], 0)
    # 合計は「表示中の per-day 行」の総和にする
    # （by_date は daily_report がない日（将来の先付け注文等）も拾ってしまうため
    #   total と日別行の合計が乖離してしまう。表示と合計を必ず一致させる）
    total_instore = sum(d['instore_sales'] for d in days)
    total_bento   = sum(d['bento_sales']   for d in days)
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
               'west_sales':sum(d['west_sales'] for d in days),'south_sales':sum(d['south_sales'] for d in days),'other_sales':sum(d['other_sales'] for d in days),
               'total_instore_sales':total_instore,
               'total_bento_sales':total_bento}
    return jsonify({'month':ym,'days':days,'summary':summary})

# ─── 年次サマリ ────────────────────────────────
@app.route('/api/yearly-summary', methods=['GET'])
def yearly_summary():
    year = request.args.get('year')
    dep = get_dept(); did = dep['id']; feats = (dep.get('config') or {}).get('features') or {}
    only_cats = (dep.get('config') or {}).get('dx_instore_only')   # 餅部=["餅"]
    rows = sb.table('hq_daily_reports').select('*').eq('department_id',did).like('date',year+'%').order('date').execute().data
    # 注文売上を日別集計（月別ではなく）。日報がある日付の分だけ後で月集計に組み入れる
    instore_by_date = {}
    bento_by_date = {}
    if feats.get('dx_orders'):
        instore_rows = sb.table('hq_instore_orders').select('date,quantity,price').eq('department_id',did).like('date',year+'%').execute().data
        for r in instore_rows:
            d = r.get('date')
            if not d: continue
            amt = int(round(float(r.get('quantity') or 0)) * round(float(r.get('price') or 0)))
            instore_by_date[d] = instore_by_date.get(d,0) + amt
    # delivery_date は DATE 型のため like ではなく gte/lt で範囲指定する
    start, end = _year_range(year)
    try:
        bento_orders = sb.table('orders').select('delivery_date,product_id,quantity')\
            .gte('delivery_date',start).lt('delivery_date',end).execute().data if (feats.get('dx_orders') and not only_cats) else []
    except Exception:
        bento_orders = []
    if bento_orders:
        bento_pids = list({o['product_id'] for o in bento_orders if o.get('product_id')})
        try:
            bento_prods = sb.table('products').select('*').in_('id', bento_pids).execute().data
        except Exception:
            bento_prods = []
        def _bp_price(p):
            for k in ('price','unit_price'):
                v = p.get(k)
                if v is not None:
                    try:    return int(round(float(v)))
                    except: pass
            return 0
        bp_price = {p['id']: _bp_price(p) for p in bento_prods}
        for o in bento_orders:
            d   = (o.get('delivery_date') or '')[:10]
            amt = int(round(float(o.get('quantity') or 0))) * bp_price.get(o.get('product_id'), 0)
            if d: bento_by_date[d] = bento_by_date.get(d,0) + amt
    if not rows:
        return jsonify({'year':year,'months':[],'summary':{'total_instore_sales':0,'total_bento_sales':0}})
    months = {}
    for d in rows:
        m = d['date'][:7]
        if m not in months:
            months[m] = {'month':m,'total_sales':0,'total_labor':0,'total_profit':0,'total_hours':0,
                         'op_days':0,'west_sales':0,'south_sales':0,'other_sales':0,
                         'instore_sales':0,'bento_sales':0}
        months[m]['total_sales']  += d.get('total_sales',0)  or 0
        months[m]['total_labor']  += d.get('labor_cost',0)   or 0
        months[m]['total_profit'] += d.get('profit',0)       or 0
        months[m]['total_hours']  += d.get('total_hours',0)  or 0
        months[m]['op_days']      += 1
        months[m]['west_sales']   += d.get('west_sales',0)   or 0
        months[m]['south_sales']  += d.get('south_sales',0)  or 0
        months[m]['other_sales']  += d.get('other_sales',0)  or 0
        # 注文/アプリ注文は「日報のある日」だけ加算（先付け注文や未作成日のキャッシュは除外）
        months[m]['instore_sales'] += instore_by_date.get(d['date'], 0)
        months[m]['bento_sales']   += bento_by_date.get(d['date'], 0)
    months_list = sorted(months.values(), key=lambda x: x['month'])
    for m in months_list:
        ts = m['total_sales']
        m['profit_rate']    = round(m['total_profit']/ts*100,1) if ts else 0
        m['labor_rate']     = round(m['total_labor']/ts*100,1)  if ts else 0
        m['avg_labor_prod'] = round(ts/m['total_hours'],0)      if m['total_hours'] else 0
    # 合計は「表示中の per-month 行」の総和にする（daily_report のない月を含めない）
    total_instore = sum(m['instore_sales'] for m in months_list)
    total_bento   = sum(m['bento_sales']   for m in months_list)
    total_sales = sum(d.get('total_sales',0) or 0 for d in rows)
    total_labor = sum(d.get('labor_cost',0)  or 0 for d in rows)
    total_profit= sum(d.get('profit',0)      or 0 for d in rows)
    total_hours = sum(d.get('total_hours',0) or 0 for d in rows)
    summary = {'total_sales':total_sales,'total_profit':total_profit,
               'profit_rate':round(total_profit/total_sales*100,1) if total_sales else 0,
               'total_labor':total_labor,'labor_rate':round(total_labor/total_sales*100,1) if total_sales else 0,
               'op_days':len(rows),'avg_daily_sales':int(total_sales/len(rows)) if rows else 0,
               'avg_labor_prod':round(total_sales/total_hours,0) if total_hours else 0,
               'west_sales':sum(d.get('west_sales',0)   or 0 for d in rows),
               'south_sales':sum(d.get('south_sales',0) or 0 for d in rows),
               'other_sales':sum(d.get('other_sales',0) or 0 for d in rows),
               'total_instore_sales':total_instore,
               'total_bento_sales':total_bento}
    return jsonify({'year':year,'months':months_list,'summary':summary})

# ─── 印刷用データ ─────────────────────────────
@app.route('/api/print/shipping-plan', methods=['GET'])
def print_shipping_plan():
    target_date = request.args.get('date')
    did = dept_id()
    channels = sb.table('hq_channels').select('*').eq('department_id',did).eq('active',1).order('sort_order').execute().data
    products = sb.table('hq_products').select('*').eq('department_id',did).eq('active',1).order('category').order('id').execute().data
    plans    = sb.table('hq_shipping_plans').select('product_id,channel_id,planned_qty,note').eq('department_id',did).eq('date',target_date).execute().data
    plan_map = {(p['product_id'],p['channel_id']):p['planned_qty'] for p in plans}
    note_map = {p['product_id']:p['note'] for p in plans if p.get('note')}
    dt     = datetime.strptime(target_date,'%Y-%m-%d')
    monday = (dt-timedelta(days=dt.weekday())).strftime('%Y-%m-%d')
    menus  = sb.table('hq_weekly_menus').select('*').eq('department_id',did).eq('week_start',monday).eq('day_of_week',dt.weekday()+1).order('category').execute().data
    result = {'date':target_date,'channels':channels,'products':[],'menus':menus}
    for p in products:
        row = {'id':p['id'],'name':p['name'],'price':p['price'],'category':p['category'],'note':note_map.get(p['id'],''),'quantities':{}}
        for c in channels:
            row['quantities'][c['id']] = plan_map.get((p['id'],c['id']),0)
        row['total'] = sum(row['quantities'].values())
        if row['total']>0:
            result['products'].append(row)
    return jsonify(result)

# ─── 注文弁当（bento app 連携：orders/offices/members/products JOIN）────────────
# 店頭注文 (/api/instore/orders) は dx 由来。こちらは別系統の bento システム由来で
# office_name / member_name / payment_method / note などを返す。
@app.route('/api/bento/orders', methods=['GET'])
def get_bento_orders():
    """指定日(?date=YYYY-MM-DD)、または無指定なら明日以降の弁当注文を取得"""
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
    # products は price / category のカラム名揺れに耐えるよう全カラム取得
    products = safe_in('products', product_ids, fields='*')

    def _prod_price(prod):
        # price / unit_price のどちらでも拾う
        for k in ('price', 'unit_price'):
            v = prod.get(k)
            if v is not None:
                try:    return int(round(float(v)))
                except: pass
        return 0

    def _prod_name(prod):
        return prod.get('name') or prod.get('product_name') or ''

    FREE_OFFICE_ID = '19ab2b37-f610-46f7-b050-ce6bf3f8037e'
    result = []
    for o in orders:
        prod = products.get(o.get('product_id'), {})
        office_id   = o.get('office_id')
        member_id   = o.get('member_id')
        office_name = offices.get(office_id, {}).get('name', '')
        is_free     = (office_id == FREE_OFFICE_ID) or (office_name == 'フリー会員')
        result.append({
            'id':             o['id'],
            'delivery_date':  o['delivery_date'],
            'office_id':      office_id,
            'office_name':    office_name,
            'member_id':      member_id,
            'member_name':    members.get(member_id, {}).get('name', ''),
            'is_free_member': is_free,
            'product_name':   _prod_name(prod),
            'price':          _prod_price(prod),
            'category':       prod.get('category') or '弁当',
            'quantity':       int(o.get('quantity') or 0),
            'payment_method': o.get('payment_method', ''),
            'note':           o.get('note', ''),
        })
    return jsonify(result)

# ─── 店頭注文（dx スキーマ連携）─────────────────
# 前提：
#   dx.InstoreOrder ＝ id, storeId, productName, quantity, customerName, deliveryDate
#   dx.OrderProduct ＝ productName, category, price
#   storeId は hq_channels.id と一致
# dx 側カラム名が異なる場合は下記の SELECT/参照キーを調整してください。
DX_INSTORE_TABLE = 'InstoreOrder'
DX_PRODUCT_TABLE = 'OrderProduct'
DX_DATE_COL      = 'deliveryDate'

@app.route('/api/instore/orders', methods=['GET'])
def get_instore_orders():
    """dx.InstoreOrder + dx.OrderProduct から取得し hq_instore_orders に mirror して返す。
    過去日は dx を叩かず hq の保存値だけ返す（履歴凍結）。"""
    target_date = request.args.get('date')
    dep = get_dept(); did = dep['id']
    only_cats = (dep.get('config') or {}).get('dx_instore_only')  # 餅部=["餅"]、弁当部=None
    if not target_date:
        return jsonify({'error':'date required'}), 400

    def _normalize(rows):
        # store_id→店名を全社横断で解決して付与（フロントは店名で店舗列に振り分ける）
        sids = list({r.get('store_id') for r in rows if r.get('store_id') is not None})
        name_by_sid = {}
        if sids:
            try:
                for c in sb.table('hq_channels').select('id,name').in_('id', sids).execute().data:
                    name_by_sid[c['id']] = c['name']
            except Exception:
                pass
        for r in rows:
            if r.get('price')    is not None: r['price']    = int(round(float(r['price'])))
            if r.get('quantity') is not None: r['quantity'] = int(r['quantity'])
            r['store_name'] = name_by_sid.get(r.get('store_id'), '')
        return rows

    # 過去日 → hq のキャッシュだけ返す
    if target_date < today_jst().isoformat():
        rows = sb.table('hq_instore_orders').select('*').eq('department_id',did).eq('date',target_date).order('id').execute().data
        return jsonify(_normalize(rows))

    # 当日以降 → dx から取得して mirror（sb_dx は dx 専用クライアント）
    # status='active' のみ取り込み（cancelled 等は除外）
    try:
        dx_orders = sb_dx.table(DX_INSTORE_TABLE)\
            .select(f'id,storeId,productName,quantity,customerName,status,price,category,{DX_DATE_COL}')\
            .eq(DX_DATE_COL, target_date).eq('status','active').execute().data
    except Exception as e:
        # dx 接続失敗時は hq のキャッシュにフォールバック
        rows = sb.table('hq_instore_orders').select('*').eq('department_id',did).eq('date',target_date).order('id').execute().data
        return jsonify(_normalize(rows))

    if not dx_orders:
        return jsonify([])

    names = list({o['productName'] for o in dx_orders})
    try:
        dx_prods = sb_dx.table(DX_PRODUCT_TABLE)\
            .select('productName,category,price').in_('productName', names).execute().data
    except Exception:
        dx_prods = []
    prod_map = {p['productName']: p for p in dx_prods}

    # カテゴリ判定: マスタを優先、無ければ注文側（カスタム追加商品）
    def _category_of(o):
        return (prod_map.get(o['productName']) or {}).get('category') or o.get('category')

    # カテゴリで部署へ振り分け：餅部(only_cats=["餅"])は餅のみ、弁当部は餅以外を取り込む
    if only_cats:
        dx_orders = [o for o in dx_orders if _category_of(o) in only_cats]
    else:
        dx_orders = [o for o in dx_orders if _category_of(o) != '餅']

    mirror_rows = []
    for o in dx_orders:
        p = prod_map.get(o['productName'], {})
        src = o.get('id')
        source_id = str(src) if src is not None else f'{target_date}|{o["storeId"]}|{o["productName"]}|{o.get("customerName","")}'
        # 単価: マスタ価格 → 注文側 price（カスタム追加商品の救済）→ 0
        unit_price = p.get('price') or o.get('price') or 0
        mirror_rows.append({
            'date':          target_date,
            'department_id': did,
            'store_id':      o['storeId'],
            'product_name':  o['productName'],
            'customer_name': o.get('customerName') or '',
            'quantity':      int(o.get('quantity') or 0),
            'price':         int(round(float(unit_price))),
            'category':      p.get('category') or o.get('category') or '弁当',
            'source_id':     source_id,
        })
    # 既存キャッシュのうち、今回の active セットに無いもの（＝dx 側で cancelled/削除済）を除去
    existing = sb.table('hq_instore_orders').select('source_id').eq('department_id',did).eq('date',target_date).execute().data
    active_ids = {r['source_id'] for r in mirror_rows}
    stale_ids  = [r['source_id'] for r in existing if r.get('source_id') not in active_ids]
    if stale_ids:
        sb.table('hq_instore_orders').delete().in_('source_id', stale_ids).execute()

    if mirror_rows:
        sb.table('hq_instore_orders').upsert(mirror_rows, on_conflict='source_id').execute()

    rows = sb.table('hq_instore_orders').select('*').eq('department_id',did).eq('date',target_date).order('id').execute().data
    return jsonify(_normalize(rows))

if __name__ == '__main__':
    app.run(debug=True, port=5050)
