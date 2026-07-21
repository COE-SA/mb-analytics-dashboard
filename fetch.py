#!/usr/bin/env python3
"""
Munch Bakery — Full Analytics ETL v2
جلب كامل لبيانات التحليل المالي من Odoo مع إصلاح عدد الطلبات والتوقعات
"""
import os, json, sys, xmlrpc.client
from datetime import datetime, timedelta, timezone
from collections import defaultdict

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

def sr(model, domain=[], fields=[], limit=2000, order=''):
    kw = {'limit': limit}
    if fields: kw['fields'] = fields
    if order:  kw['order']  = order
    return models.execute_kw(ODOO_DB, uid, ODOO_KEY, model, 'search_read', [domain], kw)

def rg(model, domain, fields, groupby, limit=500):
    return models.execute_kw(ODOO_DB, uid, ODOO_KEY, model, 'read_group',
        [domain], {'fields': fields, 'groupby': groupby, 'limit': limit})

def count(model, domain):
    return models.execute_kw(ODOO_DB, uid, ODOO_KEY, model, 'search_count', [domain])

# ─── Time boundaries ──────────────────────────────────────────────────────────
now_ksa   = datetime.now(KSA)
today     = now_ksa.date()
yesterday = today - timedelta(days=1)
d7        = (today - timedelta(days=7)).isoformat()
d30       = (today - timedelta(days=30)).isoformat()
d90       = (today - timedelta(days=90)).isoformat()
d365      = (today - timedelta(days=365)).isoformat()
this_month_start  = today.replace(day=1).isoformat()
last_month_end    = (today.replace(day=1) - timedelta(days=1)).isoformat()
last_month_start  = (today.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()
this_year_start   = today.replace(month=1, day=1).isoformat()
last_year_start   = today.replace(year=today.year-1, month=1, day=1).isoformat()
last_year_end     = today.replace(year=today.year-1, month=12, day=31).isoformat()

# Same period last year
lytd_start = today.replace(year=today.year-1, month=1, day=1).isoformat()
lytd_end   = (today.replace(year=today.year-1)).isoformat()

# Same month last year
last_year_same_month_start = today.replace(year=today.year-1, day=1).isoformat()
last_year_same_month_end   = (today.replace(year=today.year-1)).isoformat()

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

AGG_DISPLAY = {
    'Hunger Station (POSZ) ***': 'Hunger Station',
    'Keeta(POSZ) ***': 'Keeta',
    'Taker Website(POSZ) ***': 'Taker Website',
    'ToYou (POSZ) ***': 'ToYou',
    'Ninja(POSZ) ***': 'Ninja',
    'Marsool (POSZ) ***': 'Marsool',
    'JAHEZ (POSZ) ***': 'JAHEZ',
    'The Chefz (POSZ) ***': 'The Chefz',
    'Noon (POSZ) ***': 'Noon',
    'Careem (POSZ) ***': 'Careem',
    'Mr.Mandoob (POSZ) ***': 'Mr.Mandoob',
    'COE Marketing (POSZ) ***': 'COE Marketing',
    'Tamara (POSZ) ***': 'Tamara',
}

# ─── Core helpers ─────────────────────────────────────────────────────────────
def pos_total(domain_extra=[]):
    """Revenue + order count for a domain"""
    base = [['state','!=','cancel']] + domain_extra
    res = rg('pos.order', base, ['amount_total'], [])
    rev = round(res[0].get('amount_total', 0), 2) if res else 0
    cnt = count('pos.order', base)
    return {'revenue': rev, 'orders': cnt}

def pos_daily_range(since, until=None):
    """Daily aggregation via search_read (Python-side groupby)"""
    base = [['state','!=','cancel'], ['date_order','>=', since]]
    if until:
        base.append(['date_order','<', until])
    rows = sr('pos.order', base, ['date_order','amount_total','config_id','partner_id'], limit=5000, order='date_order asc')
    daily = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    for r in rows:
        day = r.get('date_order','')[:10]
        daily[day]['revenue'] = round(daily[day]['revenue'] + r.get('amount_total', 0), 2)
        daily[day]['orders'] += 1
    return [{'day': k, 'revenue': v['revenue'], 'orders': v['orders']} for k, v in sorted(daily.items())]

def pos_hourly_range(since):
    """Hourly aggregation (KSA time)"""
    base = [['state','!=','cancel'], ['date_order','>=', since]]
    rows = sr('pos.order', base, ['date_order','amount_total'], limit=5000)
    hourly = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    for r in rows:
        dt_str = r.get('date_order', '')
        try:
            dt_utc = datetime.strptime(dt_str[:19], '%Y-%m-%d %H:%M:%S')
            h = (dt_utc + timedelta(hours=3)).hour
        except:
            h = 0
        hourly[h]['revenue'] = round(hourly[h]['revenue'] + r.get('amount_total', 0), 2)
        hourly[h]['orders'] += 1
    return [{'hour': k, 'revenue': v['revenue'], 'orders': v['orders']} for k, v in sorted(hourly.items())]

def pos_monthly_range(since, until=None):
    """Monthly aggregation via paginated search_read (handles 200k+ records)"""
    base = [['state','!=','cancel'], ['date_order','>=', since]]
    if until:
        base.append(['date_order','<=', until])
    monthly = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    offset = 0
    batch = 5000
    while True:
        kw = {'limit': batch, 'offset': offset, 'fields': ['date_order','amount_total']}
        rows = models.execute_kw(ODOO_DB, uid, ODOO_KEY, 'pos.order', 'search_read', [base], kw)
        if not rows:
            break
        for r in rows:
            mo = r.get('date_order','')[:7]  # YYYY-MM
            monthly[mo]['revenue'] = round(monthly[mo]['revenue'] + r.get('amount_total', 0), 2)
            monthly[mo]['orders'] += 1
        if len(rows) < batch:
            break
        offset += batch
    return [{'month': k, 'revenue': v['revenue'], 'orders': v['orders']} for k, v in sorted(monthly.items())]

def pos_by_branch_range(domain_extra=[]):
    """Branch breakdown via search_read"""
    base = [['state','!=','cancel']] + domain_extra
    rows = sr('pos.order', base, ['config_id','amount_total'], limit=5000)
    branches = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    for r in rows:
        name = r.get('config_id', [None, 'Unknown'])[1] if r.get('config_id') else 'Unknown'
        short = name.split('(')[-1].replace(')', '').strip() if '(' in name else name
        branches[short]['revenue'] = round(branches[short]['revenue'] + r.get('amount_total', 0), 2)
        branches[short]['orders'] += 1
    return dict(branches)

def pos_by_aggregator_range(domain_extra=[]):
    """Aggregator breakdown via search_read"""
    base = [['state','!=','cancel'], ['partner_id','!=',False]] + domain_extra
    rows = sr('pos.order', base, ['partner_id','amount_total'], limit=5000)
    aggs = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    for r in rows:
        raw_name = r.get('partner_id', [None, ''])[1] if r.get('partner_id') else ''
        if raw_name not in AGGREGATORS and 'POSZ' not in raw_name:
            continue
        display = AGG_DISPLAY.get(raw_name, raw_name.replace(' (POSZ) ***','').replace('(POSZ) ***','').strip())
        aggs[display]['revenue'] = round(aggs[display]['revenue'] + r.get('amount_total', 0), 2)
        aggs[display]['orders'] += 1
    return dict(aggs)

def payment_breakdown_range(domain_extra=[]):
    """Payment methods breakdown"""
    base = [] + domain_extra
    rows = sr('pos.payment', base, ['payment_method_id','amount'], limit=5000)
    methods = defaultdict(lambda: {'amount': 0, 'count': 0})
    for r in rows:
        name = r.get('payment_method_id', [None, 'Unknown'])[1] if r.get('payment_method_id') else 'Unknown'
        methods[name]['amount'] = round(methods[name]['amount'] + r.get('amount', 0), 2)
        methods[name]['count'] += 1
    return [{'method': k, 'amount': v['amount'], 'count': v['count']}
            for k, v in sorted(methods.items(), key=lambda x: -x[1]['amount'])]

def top_products_range(domain_extra=[], limit=30):
    """Top products with COGS"""
    base = [['order_id.state','!=','cancel']] + domain_extra
    rows = sr('pos.order.line', base, ['product_id','qty','price_subtotal_incl'], limit=10000)
    prod_agg = defaultdict(lambda: {'qty': 0, 'revenue': 0})
    for r in rows:
        if not r.get('product_id'): continue
        pid = r['product_id'][0]
        prod_agg[pid]['name'] = r['product_id'][1]
        prod_agg[pid]['qty'] = round(prod_agg[pid]['qty'] + r.get('qty', 0), 2)
        prod_agg[pid]['revenue'] = round(prod_agg[pid]['revenue'] + r.get('price_subtotal_incl', 0), 2)
    # sort by revenue, take top N
    top = sorted(prod_agg.items(), key=lambda x: -x[1]['revenue'])[:limit]
    prod_ids = [pid for pid, _ in top]
    costs = {}
    if prod_ids:
        prods = sr('product.product', [['id','in',prod_ids]],
                   ['id','standard_price','categ_id'], limit=limit)
        costs = {p['id']: p for p in prods}
    out = []
    for pid, v in top:
        pd = costs.get(pid, {})
        cost_u = pd.get('standard_price', 0)
        cat = pd.get('categ_id', [None,'?'])[1] if pd.get('categ_id') else '?'
        qty = v['qty']
        rev = v['revenue']
        cogs = round(cost_u * qty, 2)
        gp = round(rev - cogs, 2)
        margin = round(gp / rev * 100, 1) if rev > 0 else 0
        out.append({'product': v['name'], 'category': cat,
                    'qty': qty, 'revenue': rev, 'cogs': cogs,
                    'gross_profit': gp, 'margin_pct': margin})
    return out

def pl_monthly_range(since):
    """P&L from account.move.line"""
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
    # Odoo returns month as "Month YYYY" string — convert to YYYY-MM for sorting
    from datetime import datetime as _dt
    def _to_ym(s):
        try:
            return _dt.strptime(s, '%B %Y').strftime('%Y-%m')
        except:
            return s
    rev_map = {_to_ym(r.get('date:month','?')): round(r.get('credit',0)-r.get('debit',0),2) for r in rev}
    exp_map = {_to_ym(r.get('date:month','?')): round(r.get('debit',0)-r.get('credit',0),2) for r in exp}
    months = sorted(set(list(rev_map.keys())+list(exp_map.keys())))
    result = []
    for mo in months:
        r = rev_map.get(mo, 0)
        e = exp_map.get(mo, 0)
        result.append({'month': mo, 'revenue': r, 'expenses': e,
                       'gross_profit': round(r-e, 2),
                       'margin_pct': round((r-e)/r*100, 1) if r > 0 else 0})
    return result

def purchase_monthly(since):
    rows = sr('purchase.order',
        [['state','in',['purchase','done']], ['date_order','>=',since]],
        ['amount_total','date_order'], limit=500, order='date_order asc')
    monthly = defaultdict(float)
    for r in rows:
        mo = r.get('date_order','?')[:7]
        monthly[mo] += r.get('amount_total', 0)
    return [{'month': k, 'amount': round(v, 2)} for k, v in sorted(monthly.items())]

def stock_summary():
    rows = sr('stock.quant',
        [['location_id.usage','=','internal'], ['quantity','>',0]],
        ['product_id','quantity','reserved_quantity','location_id'], limit=500)
    by_loc = defaultdict(list)
    for r in rows:
        loc = r.get('location_id', [None,'?'])[1] if r.get('location_id') else '?'
        prod = r.get('product_id', [None,'?'])[1] if r.get('product_id') else '?'
        by_loc[loc].append({'product': prod,
                            'qty': round(r.get('quantity', 0), 1),
                            'reserved': round(r.get('reserved_quantity', 0), 1)})
    return dict(by_loc)

# ─── Forecast helpers ─────────────────────────────────────────────────────────
def forecast_next_day(daily_data):
    """Simple 7-day moving average forecast"""
    if len(daily_data) < 3:
        return None
    recent = daily_data[-min(7, len(daily_data)):]
    avg_rev = round(sum(r['revenue'] for r in recent) / len(recent), 2)
    avg_ord = round(sum(r['orders'] for r in recent) / len(recent), 0)
    return {'revenue': avg_rev, 'orders': int(avg_ord), 'method': '7-day avg'}

def forecast_month(monthly_data, current_month_key):
    """Forecast remaining days in current month"""
    if not monthly_data:
        return None
    # Find current month data
    current = next((m for m in monthly_data if m['month'] == current_month_key), None)
    if not current:
        return None
    days_elapsed = today.day
    days_in_month = 31  # approximate
    if days_elapsed < 3:
        return None
    daily_run_rate = current['revenue'] / days_elapsed
    projected = round(daily_run_rate * days_in_month, 2)
    return {'projected_month_revenue': projected,
            'daily_run_rate': round(daily_run_rate, 2),
            'days_elapsed': days_elapsed}

def forecast_year(monthly_data_ytd):
    """Forecast full year based on YTD run rate"""
    if not monthly_data_ytd:
        return None
    total_ytd = sum(m['revenue'] for m in monthly_data_ytd)
    months_elapsed = today.month + (today.day / 30)
    if months_elapsed < 1:
        return None
    monthly_run_rate = total_ytd / months_elapsed
    projected_year = round(monthly_run_rate * 12, 2)
    return {'projected_year_revenue': projected_year,
            'monthly_run_rate': round(monthly_run_rate, 2),
            'ytd_revenue': round(total_ytd, 2),
            'months_elapsed': round(months_elapsed, 1)}

# ─── BUILD DATA ───────────────────────────────────────────────────────────────
log("📊 جلب البيانات الأساسية (KPIs)...")

data = {
    'meta': {
        'generated_at': now_ksa.strftime('%Y-%m-%d %H:%M:%S KSA'),
        'generated_at_iso': now_ksa.isoformat(),
        'today': today.isoformat(),
        'yesterday': yesterday.isoformat(),
        'this_month': today.strftime('%B %Y'),
        'schema_version': 4,
    },
}

# ── KPIs ──────────────────────────────────────────────────────────────────────
log("  KPIs...")
data['kpis'] = {
    'today':      pos_total([['date_order','>=', today.isoformat()]]),
    'yesterday':  pos_total([['date_order','>=', yesterday.isoformat()],
                              ['date_order','<',  today.isoformat()]]),
    'this_week':  pos_total([['date_order','>=', d7]]),
    'this_month': pos_total([['date_order','>=', this_month_start]]),
    'last_month': pos_total([['date_order','>=', last_month_start],
                              ['date_order','<=', last_month_end]]),
    'this_year':  pos_total([['date_order','>=', this_year_start]]),
    'last_year':  pos_total([['date_order','>=', last_year_start],
                              ['date_order','<=', last_year_end]]),
    'all_time':   pos_total([]),
}

# ── Daily sales (last 60 days, newest first) ──────────────────────────────────
log("  Daily sales (60d)...")
d60 = (today - timedelta(days=60)).isoformat()
daily_raw = pos_daily_range(d60)
data['daily_sales'] = list(reversed(daily_raw))  # newest first

# ── Daily comparison: today vs yesterday vs same day last week ────────────────
log("  Daily comparison...")
same_day_last_week_start = (today - timedelta(days=7)).isoformat()
same_day_last_week_end   = (today - timedelta(days=6)).isoformat()
same_day_last_month_start = (today - timedelta(days=30)).isoformat()
same_day_last_month_end   = (today - timedelta(days=29)).isoformat()

data['daily_comparison'] = {
    'today':               data['kpis']['today'],
    'yesterday':           data['kpis']['yesterday'],
    'same_day_last_week':  pos_total([['date_order','>=', same_day_last_week_start],
                                       ['date_order','<',  same_day_last_week_end]]),
    'same_day_last_month': pos_total([['date_order','>=', same_day_last_month_start],
                                       ['date_order','<',  same_day_last_month_end]]),
    'forecast_tomorrow':   forecast_next_day(daily_raw),
}

# ── Monthly sales (all history, newest first) ─────────────────────────────────
log("  Monthly sales (all time)...")
monthly_all_raw = pos_monthly_range('2024-01-01')
data['monthly_sales_all'] = list(reversed(monthly_all_raw))  # newest first

# ── Monthly sales last 12m ────────────────────────────────────────────────────
monthly_12m_raw = [m for m in monthly_all_raw if m['month'] >= d365[:7]]
data['monthly_sales_12m'] = list(reversed(monthly_12m_raw))

# ── Monthly comparison ────────────────────────────────────────────────────────
log("  Monthly comparison...")
current_month_key = today.strftime('%Y-%m')
prev_month_key    = (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
same_month_ly_key = today.replace(year=today.year-1).strftime('%Y-%m')

def find_month(data_list, key):
    return next((m for m in data_list if m['month'] == key), {'revenue': 0, 'orders': 0})

data['monthly_comparison'] = {
    'this_month':       find_month(monthly_all_raw, current_month_key),
    'last_month':       find_month(monthly_all_raw, prev_month_key),
    'same_month_ly':    find_month(monthly_all_raw, same_month_ly_key),
    'forecast_month':   forecast_month(monthly_all_raw, current_month_key),
}

# ── Yearly comparison ─────────────────────────────────────────────────────────
log("  Yearly comparison...")
ytd_months = [m for m in monthly_all_raw if m['month'] >= this_year_start[:7]]
lytd_months = [m for m in monthly_all_raw
               if m['month'] >= last_year_start[:7] and m['month'] <= lytd_end[:7]]

data['yearly_comparison'] = {
    'this_year':    data['kpis']['this_year'],
    'last_year':    data['kpis']['last_year'],
    'lytd':         {'revenue': round(sum(m['revenue'] for m in lytd_months), 2),
                     'orders':  sum(m['orders'] for m in lytd_months)},
    'forecast_year': forecast_year(ytd_months),
}

# ── Hourly sales (last 14 days) ───────────────────────────────────────────────
log("  Hourly sales...")
d14 = (today - timedelta(days=14)).isoformat()
data['hourly_sales'] = pos_hourly_range(d14)

# ── Branches ──────────────────────────────────────────────────────────────────
log("  Branches...")
data['branches'] = {
    'today':        pos_by_branch_range([['date_order','>=', today.isoformat()]]),
    'yesterday':    pos_by_branch_range([['date_order','>=', yesterday.isoformat()],
                                          ['date_order','<',  today.isoformat()]]),
    'this_month':   pos_by_branch_range([['date_order','>=', this_month_start]]),
    'last_month':   pos_by_branch_range([['date_order','>=', last_month_start],
                                          ['date_order','<=', last_month_end]]),
    'this_year':    pos_by_branch_range([['date_order','>=', this_year_start]]),
    'all_time':     pos_by_branch_range([]),
}

# ── Branch monthly detail (newest first) ─────────────────────────────────────
log("  Branch monthly detail...")
branch_rows = sr('pos.order',
    [['state','!=','cancel'], ['date_order','>=', '2024-01-01']],
    ['date_order','amount_total','config_id'], limit=10000, order='date_order asc')
bm = defaultdict(lambda: defaultdict(lambda: {'revenue': 0, 'orders': 0}))
for r in branch_rows:
    name = r.get('config_id', [None,'Unknown'])[1] if r.get('config_id') else 'Unknown'
    short = name.split('(')[-1].replace(')','').strip() if '(' in name else name
    mo = r.get('date_order','')[:7]
    bm[short][mo]['revenue'] = round(bm[short][mo]['revenue'] + r.get('amount_total',0), 2)
    bm[short][mo]['orders'] += 1
data['branch_monthly'] = {
    branch: {mo: v for mo, v in sorted(months.items(), reverse=True)}
    for branch, months in bm.items()
}

# ── Aggregators ───────────────────────────────────────────────────────────────
log("  Aggregators...")
data['aggregators'] = {
    'all_time':  pos_by_aggregator_range([]),
    'last_30d':  pos_by_aggregator_range([['date_order','>=', d30]]),
    'this_month': pos_by_aggregator_range([['date_order','>=', this_month_start]]),
    'this_year': pos_by_aggregator_range([['date_order','>=', this_year_start]]),
}

# Aggregator monthly (newest first)
agg_rows = sr('pos.order',
    [['state','!=','cancel'], ['partner_id','!=',False], ['date_order','>=', '2024-01-01']],
    ['date_order','amount_total','partner_id'], limit=10000, order='date_order asc')
agg_monthly = defaultdict(lambda: defaultdict(float))
for r in agg_rows:
    raw = r.get('partner_id', [None,''])[1] if r.get('partner_id') else ''
    if raw not in AGGREGATORS and 'POSZ' not in raw:
        continue
    display = AGG_DISPLAY.get(raw, raw.replace(' (POSZ) ***','').strip())
    mo = r.get('date_order','')[:7]
    agg_monthly[display][mo] += r.get('amount_total', 0)
data['aggregators']['monthly'] = {
    app: {mo: round(v, 2) for mo, v in sorted(months.items(), reverse=True)}
    for app, months in agg_monthly.items()
}

# ── Payment methods ───────────────────────────────────────────────────────────
log("  Payment methods...")
data['payment_methods'] = {
    'last_30d':   payment_breakdown_range([['payment_date','>=', d30]]),
    'this_month': payment_breakdown_range([['payment_date','>=', this_month_start]]),
    'this_year':  payment_breakdown_range([['payment_date','>=', this_year_start]]),
}

# ── Top products ──────────────────────────────────────────────────────────────
log("  Top products...")
data['top_products'] = {
    'last_30d':   top_products_range([['order_id.date_order','>=', d30]]),
    'this_month': top_products_range([['order_id.date_order','>=', this_month_start]]),
    'this_year':  top_products_range([['order_id.date_order','>=', this_year_start]]),
    'all_time':   top_products_range([]),
}

# ── P&L ───────────────────────────────────────────────────────────────────────
log("  P&L...")
pl_raw = pl_monthly_range('2024-01-01')
data['pl_monthly'] = list(reversed(pl_raw))  # newest first

# ── Purchases ─────────────────────────────────────────────────────────────────
log("  Purchases...")
purch_raw = purchase_monthly('2024-01-01')
data['purchases'] = {
    'monthly': list(reversed(purch_raw)),
    'total': round(sum(p['amount'] for p in purch_raw), 2),
    'recent': sr('purchase.order',
        [['state','in',['purchase','done']]],
        ['name','partner_id','amount_total','date_order'], limit=20, order='date_order desc'),
}

# ── Stock ─────────────────────────────────────────────────────────────────────
log("  Stock...")
data['stock'] = stock_summary()

# ── Summary stats ─────────────────────────────────────────────────────────────
total_agg_rev = sum(v.get('revenue',0) for v in data['aggregators']['all_time'].values())
total_rev = data['kpis']['all_time']['revenue']
data['summary'] = {
    'total_revenue':    total_rev,
    'total_orders':     data['kpis']['all_time']['orders'],
    'aggregator_share': round(total_agg_rev / total_rev * 100, 1) if total_rev > 0 else 0,
    'aggregator_total': round(total_agg_rev, 2),
}

# ─── SAVE ─────────────────────────────────────────────────────────────────────
log(f"💾 حفظ البيانات في {OUT_FILE}...")
with open(OUT_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, separators=(',',':'))
size_kb = os.path.getsize(OUT_FILE) / 1024
log(f"✅ تم الحفظ ({size_kb:.1f} KB)")
log("")
log("📈 ملخص سريع:")
log(f"   اليوم:              {data['kpis']['today']['revenue']:>12,.0f} ر.س  ({data['kpis']['today']['orders']} طلب)")
log(f"   أمس:                {data['kpis']['yesterday']['revenue']:>12,.0f} ر.س  ({data['kpis']['yesterday']['orders']} طلب)")
log(f"   هذا الشهر:          {data['kpis']['this_month']['revenue']:>12,.0f} ر.س  ({data['kpis']['this_month']['orders']} طلب)")
log(f"   الشهر الماضي:       {data['kpis']['last_month']['revenue']:>12,.0f} ر.س  ({data['kpis']['last_month']['orders']} طلب)")
log(f"   هذا العام:          {data['kpis']['this_year']['revenue']:>12,.0f} ر.س  ({data['kpis']['this_year']['orders']} طلب)")
log(f"   الكل:               {data['kpis']['all_time']['revenue']:>12,.0f} ر.س  ({data['kpis']['all_time']['orders']} طلب)")
log(f"   توقع الغد:          {data['daily_comparison']['forecast_tomorrow']['revenue'] if data['daily_comparison']['forecast_tomorrow'] else 'N/A':>12} ر.س")
