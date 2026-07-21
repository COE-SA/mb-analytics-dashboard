#!/usr/bin/env python3
"""
Munch Bakery — Full Analytics ETL
جلب كامل لبيانات التحليل المالي من Odoo
"""
import os, json, sys, xmlrpc.client
from datetime import datetime, timedelta, timezone

ODOO_URL  = os.environ.get('ODOO_URL',  'https://munchbakerydev-compass.odoo.com')
ODOO_DB   = os.environ.get('ODOO_DB',   'munchbakerydev-compass-live-15510994')
ODOO_USER = os.environ.get('ODOO_USER', 'HASSAN')
ODOO_KEY  = os.environ.get('ODOO_API_KEY', '6db155ab7f2b2ad269803fac22e621f8fc3081a2')
OUT_FILE  = os.environ.get('OUTPUT_FILE', 'data.json')

KSA = timezone(timedelta(hours=3))

def log(msg): print(f"[{datetime.now(KSA).strftime('%H:%M:%S')}] {msg}", flush=True)

log("🔌 الاتصال بـ Odoo...")
common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object",  allow_none=True)
uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_KEY, {})
if not uid:
    log("❌ فشل تسجيل الدخول"); sys.exit(1)
log(f"✅ UID={uid}")

def sr(model, domain=[], fields=[], limit=500, order=''):
    kw = {'limit': limit}
    if fields: kw['fields'] = fields
    if order:  kw['order']  = order
    return models.execute_kw(ODOO_DB, uid, ODOO_KEY, model, 'search_read', [domain], kw)

def rg(model, domain, fields, groupby, limit=500):
    return models.execute_kw(ODOO_DB, uid, ODOO_KEY, model, 'read_group',
        [domain], {'fields': fields, 'groupby': groupby, 'limit': limit})

now_ksa   = datetime.now(KSA)
today     = now_ksa.date()
yesterday = today - timedelta(days=1)
d7        = (today - timedelta(days=7)).isoformat()
d30       = (today - timedelta(days=30)).isoformat()
d90       = (today - timedelta(days=90)).isoformat()
d365      = (today - timedelta(days=365)).isoformat()
this_month_start = today.replace(day=1).isoformat()
last_month_end   = (today.replace(day=1) - timedelta(days=1)).isoformat()
last_month_start = (today.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()
this_year_start  = today.replace(month=1, day=1).isoformat()
last_year_start  = today.replace(year=today.year-1, month=1, day=1).isoformat()
last_year_end    = today.replace(year=today.year-1, month=12, day=31).isoformat()

AGGREGATORS = [
    'Hunger Station (POSZ) ***',
    'Keeta(POSZ) ***',
    'Taker Website(POSZ) ***',
    'ToYou (POSZ) ***',
    'Ninja(POSZ) ***',
    'Marsool (POSZ) ***',
    'JAHEZ (POSZ) ***',
    'The Chefz (POSZ) ***',
    'Noon (POSZ) ***',
    'Careem (POSZ) ***',
    'Mr.Mandoob (POSZ) ***',
    'COE Marketing (POSZ) ***',
    'Tamara (POSZ) ***',
]

BRANCH_MAP = {
    5: 'Hamadaniyah',
    6: 'Marwah',
    7: 'Ajaweed',
    8: 'Defaa',
    9: 'Waziriyah',
    10: 'Safa',
}

# ─── helpers ──────────────────────────────────────────────────────────────────
def pos_total(domain_extra=[]):
    base = [['state','!=','cancel']] + domain_extra
    res = rg('pos.order', base, ['amount_total','id'], [])
    if res: return {'revenue': round(res[0].get('amount_total',0),2),
                    'orders':  res[0].get('__count',0)}
    return {'revenue':0,'orders':0}

def pos_by_partner(domain_extra=[]):
    base = [['state','!=','cancel'],['partner_id','!=',False]] + domain_extra
    rows = rg('pos.order', base, ['partner_id','amount_total'], ['partner_id'], limit=50)
    out = {}
    for r in rows:
        name = r['partner_id'][1] if r.get('partner_id') else 'Unknown'
        out[name] = {'revenue': round(r.get('amount_total',0),2),
                     'orders':  r.get('__count',0)}
    return out

def pos_by_branch(domain_extra=[]):
    base = [['state','!=','cancel']] + domain_extra
    rows = rg('pos.order', base, ['config_id','amount_total'], ['config_id'], limit=20)
    out = {}
    for r in rows:
        name = r['config_id'][1] if r.get('config_id') else 'Unknown'
        # shorten name
        short = name.split('(')[-1].replace(')','').strip() if '(' in name else name
        out[short] = {'revenue': round(r.get('amount_total',0),2),
                      'orders':  r.get('__count',0)}
    return out

def pos_monthly(domain_extra=[], since=d365):
    base = [['state','!=','cancel'],['date_order','>=',since]] + domain_extra
    rows = rg('pos.order', base, ['amount_total'], ['date_order:month'], limit=60)
    return [{'month': r.get('date_order:month','?'),
             'revenue': round(r.get('amount_total',0),2),
             'orders': r.get('__count',0)} for r in rows]

def pos_daily(domain_extra=[], since=d30):
    """جلب المبيعات اليومية عبر search_read ثم التجميع في Python"""
    base = [['state','!=','cancel'],['date_order','>=',since]] + domain_extra
    rows = sr('pos.order', base, ['date_order','amount_total'], limit=2000, order='date_order asc')
    daily = {}
    for r in rows:
        day = r.get('date_order','')[:10]
        if day not in daily: daily[day] = {'revenue':0,'orders':0}
        daily[day]['revenue'] = round(daily[day]['revenue'] + r.get('amount_total',0), 2)
        daily[day]['orders'] += 1
    return [{'day': k, 'revenue': v['revenue'], 'orders': v['orders']} for k,v in sorted(daily.items())]

def pos_hourly(domain_extra=[], since=d7):
    """جلب المبيعات بالساعة عبر search_read ثم التجميع في Python (UTC+3)"""
    base = [['state','!=','cancel'],['date_order','>=',since]] + domain_extra
    rows = sr('pos.order', base, ['date_order','amount_total'], limit=5000, order='date_order asc')
    hourly = {}
    for r in rows:
        dt_str = r.get('date_order','')
        if len(dt_str) >= 13:
            # Odoo stores UTC, add 3h for KSA
            try:
                dt_utc = datetime.strptime(dt_str[:19], '%Y-%m-%d %H:%M:%S')
                dt_ksa = dt_utc + timedelta(hours=3)
                h = dt_ksa.hour
            except:
                h = int(dt_str[11:13])
        else:
            h = 0
        if h not in hourly: hourly[h] = {'revenue':0,'orders':0}
        hourly[h]['revenue'] = round(hourly[h]['revenue'] + r.get('amount_total',0), 2)
        hourly[h]['orders'] += 1
    return [{'hour': k, 'revenue': v['revenue'], 'orders': v['orders']} for k,v in sorted(hourly.items())]

def pl_monthly(since=d365):
    """P&L monthly from account.move.line"""
    rev = rg('account.move.line',
        [['date','>=',since],
         ['account_id.account_type','in',['income','income_other']],
         ['move_id.state','=','posted']],
        ['credit','debit'], ['date:month'], limit=60)
    exp = rg('account.move.line',
        [['date','>=',since],
         ['account_id.account_type','in',['expense','expense_direct_cost']],
         ['move_id.state','=','posted']],
        ['debit','credit'], ['date:month'], limit=60)
    rev_map = {r.get('date:month','?'): round(r.get('credit',0)-r.get('debit',0),2) for r in rev}
    exp_map = {r.get('date:month','?'): round(r.get('debit',0)-r.get('credit',0),2) for r in exp}
    months = sorted(set(list(rev_map.keys())+list(exp_map.keys())))
    result = []
    for mo in months:
        r = rev_map.get(mo,0)
        e = exp_map.get(mo,0)
        result.append({'month': mo, 'revenue': r, 'expenses': e,
                       'gross_profit': round(r-e,2),
                       'margin_pct': round((r-e)/r*100,1) if r>0 else 0})
    return result

def top_products(domain_extra=[], limit=20):
    base = [['order_id.state','!=','cancel']] + domain_extra
    rows = rg('pos.order.line', base,
              ['product_id','qty','price_subtotal_incl'], ['product_id'],
              limit=limit)
    # get cost
    prod_ids = [r['product_id'][0] for r in rows if r.get('product_id')]
    costs = {}
    if prod_ids:
        prods = sr('product.product',[['id','in',prod_ids]],
                   ['id','standard_price','list_price','categ_id'], limit=limit)
        costs = {p['id']: p for p in prods}
    out = []
    for r in rows:
        if not r.get('product_id'): continue
        pid   = r['product_id'][0]
        pname = r['product_id'][1]
        rev   = round(r.get('price_subtotal_incl',0),2)
        qty   = round(r.get('qty',0),1)
        pd    = costs.get(pid,{})
        cost_u= pd.get('standard_price',0)
        sale_u= pd.get('list_price',0)
        cat   = pd.get('categ_id',['?','?'])[1] if pd.get('categ_id') else '?'
        cogs  = round(cost_u * qty, 2)
        gp    = round(rev - cogs, 2)
        margin= round((gp/rev*100),1) if rev>0 else 0
        out.append({'product': pname, 'category': cat,
                    'qty': qty, 'revenue': rev, 'cogs': cogs,
                    'gross_profit': gp, 'margin_pct': margin})
    return out

def payment_breakdown(since=d30):
    rows = rg('pos.payment',
        [['payment_date','>=',since]],
        ['payment_method_id','amount'], ['payment_method_id'], limit=30)
    return [{'method': r.get('payment_method_id',['?','?'])[1],
             'amount': round(r.get('amount',0),2),
             'count':  r.get('__count',0)} for r in rows]

def aggregator_monthly(since=d365):
    """مبيعات التطبيقات شهرياً"""
    rows = rg('pos.order',
        [['state','!=','cancel'],['date_order','>=',since],
         ['partner_id','!=',False]],
        ['partner_id','amount_total'],
        ['partner_id','date_order:month'], limit=500)
    out = {}
    for r in rows:
        name = r['partner_id'][1] if r.get('partner_id') else 'Unknown'
        if name not in AGGREGATORS and 'POSZ' not in name: continue
        mo   = r.get('date_order:month','?')
        rev  = round(r.get('amount_total',0),2)
        if name not in out: out[name] = {}
        out[name][mo] = out[name].get(mo,0) + rev
    return out

def branch_monthly_detail(since=d365):
    rows = rg('pos.order',
        [['state','!=','cancel'],['date_order','>=',since]],
        ['config_id','amount_total'],
        ['config_id','date_order:month'], limit=500)
    out = {}
    for r in rows:
        bname = r['config_id'][1] if r.get('config_id') else 'Unknown'
        short = bname.split('(')[-1].replace(')','').strip() if '(' in bname else bname
        mo    = r.get('date_order:month','?')
        rev   = round(r.get('amount_total',0),2)
        cnt   = r.get('__count',0)
        if short not in out: out[short] = {}
        out[short][mo] = {'revenue': rev, 'orders': cnt}
    return out

def purchase_orders(since=d365):
    rows = sr('purchase.order',
        [['state','in',['purchase','done']],['date_order','>=',since]],
        ['name','partner_id','amount_total','date_order'], limit=200, order='date_order desc')
    monthly = {}
    total = 0
    for r in rows:
        mo = r.get('date_order','?')[:7]
        monthly[mo] = monthly.get(mo,0) + r.get('amount_total',0)
        total += r.get('amount_total',0)
    return {'total': round(total,2),
            'monthly': [{'month': k, 'amount': round(v,2)} for k,v in sorted(monthly.items())],
            'recent': [{'name': r['name'],
                        'supplier': r.get('partner_id',['?','?'])[1],
                        'amount': round(r.get('amount_total',0),2),
                        'date': r.get('date_order','')[:10]} for r in rows[:20]]}

def stock_summary():
    rows = sr('stock.quant',
        [['location_id.usage','=','internal'],['quantity','>',0]],
        ['product_id','quantity','reserved_quantity','location_id'], limit=500)
    by_loc = {}
    for r in rows:
        loc  = r.get('location_id',['?','?'])[1]
        prod = r.get('product_id',['?','?'])[1]
        qty  = r.get('quantity',0)
        if loc not in by_loc: by_loc[loc] = []
        by_loc[loc].append({'product': prod, 'qty': round(qty,1),
                            'reserved': round(r.get('reserved_quantity',0),1)})
    return by_loc

def all_time_totals():
    res = rg('pos.order', [['state','!=','cancel']], ['amount_total'], [])
    if res:
        return {'revenue': round(res[0].get('amount_total',0),2),
                'orders':  res[0].get('__count',0)}
    return {'revenue':0,'orders':0}

def all_time_monthly():
    rows = rg('pos.order', [['state','!=','cancel']],
              ['amount_total'], ['date_order:month'], limit=100)
    return [{'month': r.get('date_order:month','?'),
             'revenue': round(r.get('amount_total',0),2),
             'orders': r.get('__count',0)} for r in rows]

# ─── BUILD DATA ───────────────────────────────────────────────────────────────
log("📊 جلب البيانات...")

data = {
    'meta': {
        'generated_at': now_ksa.strftime('%Y-%m-%d %H:%M:%S KSA'),
        'generated_at_iso': now_ksa.isoformat(),
        'today': today.isoformat(),
        'yesterday': yesterday.isoformat(),
        'schema_version': 3,
    },

    # ── KPIs اليوم / أمس / الأسبوع / الشهر ──────────────────────────────────
    'kpis': {
        'today':        pos_total([['date_order','>=',today.isoformat()]]),
        'yesterday':    pos_total([['date_order','>=',yesterday.isoformat()],
                                   ['date_order','<', today.isoformat()]]),
        'this_week':    pos_total([['date_order','>=',d7]]),
        'this_month':   pos_total([['date_order','>=',this_month_start]]),
        'last_month':   pos_total([['date_order','>=',last_month_start],
                                   ['date_order','<=',last_month_end]]),
        'this_year':    pos_total([['date_order','>=',this_year_start]]),
        'last_year':    pos_total([['date_order','>=',last_year_start],
                                   ['date_order','<=',last_year_end]]),
        'all_time':     all_time_totals(),
    },

    # ── مبيعات يومية (آخر 30 يوم) ────────────────────────────────────────────
    'daily_sales': pos_daily(),

    # ── مبيعات شهرية (كل التاريخ) ────────────────────────────────────────────
    'monthly_sales_all': all_time_monthly(),

    # ── مبيعات شهرية (آخر 12 شهر) ───────────────────────────────────────────
    'monthly_sales_12m': pos_monthly(),

    # ── مبيعات بالساعة (آخر 7 أيام) ─────────────────────────────────────────
    'hourly_sales': pos_hourly(),

    # ── P&L شهري (آخر 12 شهر) ────────────────────────────────────────────────
    'pl_monthly': pl_monthly(),

    # ── أداء الفروع ──────────────────────────────────────────────────────────
    'branches': {
        'today':      pos_by_branch([['date_order','>=',today.isoformat()]]),
        'yesterday':  pos_by_branch([['date_order','>=',yesterday.isoformat()],
                                     ['date_order','<', today.isoformat()]]),
        'this_month': pos_by_branch([['date_order','>=',this_month_start]]),
        'last_month': pos_by_branch([['date_order','>=',last_month_start],
                                     ['date_order','<=',last_month_end]]),
        'this_year':  pos_by_branch([['date_order','>=',this_year_start]]),
        'all_time':   pos_by_branch(),
        'monthly_detail': branch_monthly_detail(),
    },

    # ── التطبيقات (Aggregators) ───────────────────────────────────────────────
    'aggregators': {
        'all_time':   pos_by_partner(),
        'last_30d':   pos_by_partner([['date_order','>=',d30]]),
        'this_month': pos_by_partner([['date_order','>=',this_month_start]]),
        'this_year':  pos_by_partner([['date_order','>=',this_year_start]]),
        'monthly':    aggregator_monthly(),
    },

    # ── طرق الدفع ────────────────────────────────────────────────────────────
    'payment_methods': {
        'last_30d':   payment_breakdown(d30),
        'this_month': payment_breakdown(this_month_start),
        'this_year':  payment_breakdown(this_year_start),
    },

    # ── المنتجات ─────────────────────────────────────────────────────────────
    'top_products': {
        'last_30d':   top_products([['order_id.date_order','>=',d30]], limit=30),
        'this_month': top_products([['order_id.date_order','>=',this_month_start]], limit=30),
        'this_year':  top_products([['order_id.date_order','>=',this_year_start]], limit=30),
        'all_time':   top_products([], limit=30),
    },

    # ── المشتريات ────────────────────────────────────────────────────────────
    'purchases': purchase_orders(),

    # ── المخزون ──────────────────────────────────────────────────────────────
    'stock': stock_summary(),
}

log(f"💾 حفظ البيانات في {OUT_FILE}...")
with open(OUT_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

size_kb = round(os.path.getsize(OUT_FILE)/1024, 1)
log(f"✅ تم الحفظ ({size_kb} KB)")

# ── ملخص سريع ────────────────────────────────────────────────────────────────
k = data['kpis']
log(f"\n📈 ملخص سريع:")
log(f"   اليوم:       {k['today']['revenue']:>12,.0f} ر.س  ({k['today']['orders']} طلب)")
log(f"   أمس:         {k['yesterday']['revenue']:>12,.0f} ر.س  ({k['yesterday']['orders']} طلب)")
log(f"   هذا الشهر:   {k['this_month']['revenue']:>12,.0f} ر.س  ({k['this_month']['orders']} طلب)")
log(f"   الشهر الماضي:{k['last_month']['revenue']:>12,.0f} ر.س  ({k['last_month']['orders']} طلب)")
log(f"   هذا العام:   {k['this_year']['revenue']:>12,.0f} ر.س  ({k['this_year']['orders']} طلب)")
log(f"   الكل:        {k['all_time']['revenue']:>12,.0f} ر.س  ({k['all_time']['orders']} طلب)")
