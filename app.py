import os
import uuid
import re
import pandas as pd
import hashlib
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload
from sqlalchemy import or_, and_, text
from sqlalchemy.pool import NullPool
from datetime import datetime
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecretkey'
import os
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///shipping.db'

if db_url:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_size': 5,
        'max_overflow': 10,
        'connect_args': {
            'connect_timeout': 10
        }
    }
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.template_filter('string_color')
def string_color(s):
    if not s:
        return 'hsl(0, 0%, 80%)'
    h = int(hashlib.md5(s.encode('utf-8')).hexdigest(), 16) % 360
    return f'hsl({h}, 70%, 85%)'

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'يرجى تسجيل الدخول للوصول إلى هذه الصفحة'
login_manager.login_message_category = 'warning'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='admin')

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

class Courier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    phone = db.Column(db.String(150))

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracking_number = db.Column(db.String(50), unique=True, nullable=False)
    client_name = db.Column(db.Text)
    phone = db.Column(db.Text)
    address = db.Column(db.Text)
    region = db.Column(db.Text)
    cod = db.Column(db.Float, default=0.0)
    shipping_fee = db.Column(db.Float, default=70.0)
    status = db.Column(db.String(50), default='مخزن')
    batch_id = db.Column(db.String(50))
    
    # New fields for accounting & partial delivery
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    content = db.Column(db.Text)
    collected_amount = db.Column(db.Float, nullable=True) # For partial delivery
    courier_fee = db.Column(db.Float, nullable=True) # Agreed courier fee
    courier_settled = db.Column(db.Boolean, default=False)
    company_settled = db.Column(db.Boolean, default=False)
    is_copied = db.Column(db.Boolean, default=False)
    
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'))
    company = db.relationship('Company', backref=db.backref('orders', lazy=True))
    
    courier_id = db.Column(db.Integer, db.ForeignKey('courier.id'))
    courier = db.relationship('Courier', backref=db.backref('orders', lazy=True))


class TreasuryTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, default=0.0)
    method = db.Column(db.String(50)) # كاش or تحويل
    tx_type = db.Column(db.String(50)) # تحصيل_من_مندوب, صرف_لشركة, مصروفات
    entity_id = db.Column(db.Integer) # courier_id or company_id
    notes = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.now)

_ARABIC_TO_ENGLISH_DIGITS = str.maketrans(
    '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹',
    '01234567890123456789'
)

def clean_phone_smart(val):
    """
    Cleans, normalizes, and extracts Egyptian/Arab phone numbers intelligently:
    - Converts Eastern Arabic (٠-٩) and Persian (۰-۹) numerals to ASCII (0-9).
    - Strips Excel float decimals (.0).
    - Splits multiple numbers separated by any delimiter (/ \\ | , ، ; ؛ _ newlines, words like او/أو/و/or/and/مع/بديل).
    - Distinguishes formatting dashes/spaces within a number from separators between two numbers.
    - Extracts glued Egyptian mobile numbers (e.g. 20-22 digits).
    - Strips Egypt country codes (+20, 0020, 20) and ensures missing leading zero on 10-digit mobiles.
    - Joins multiple distinct valid phone numbers with ' - '.
    """
    if val is None:
        return ''
    if isinstance(val, float):
        if pd.isna(val):
            return ''
        if val.is_integer():
            val = int(val)
        else:
            val = str(val).rstrip('0').rstrip('.')

    s = str(val).strip()
    if not s or s.lower() in ('nan', 'none', 'null'):
        return ''

    # 1. Translate Arabic/Persian digits to English ASCII digits
    s = s.translate(_ARABIC_TO_ENGLISH_DIGITS)

    # 2. Handle float string .0
    if s.endswith('.0'):
        s = s[:-2]

    # 3. Handle explicit word separators: أو, او, و, or, and, w, مع, بديل, اخر, آخر
    s = re.sub(r'(?i)([\d\s])(?:أو|او|\bو\b|or|and|\bw\b|مع|بديل|اخر|آخر)([\d\s])', r'\1 / \2', s)

    # 4. Handle standard symbol separators: / \ | , ، ; ؛ _ and newlines
    s = re.sub(r'[/\\|,،;؛_\r\n]+', ' / ', s)

    # 5. Handle dashes separating two numbers (either surrounded by spaces or between two digit sequences >= 7 digits)
    s = re.sub(r'\s+-\s+', ' / ', s)
    s = re.sub(r'(\d{7,})\s*-\s*(\d+)', r'\1 / \2', s)
    s = re.sub(r'(\d+)\s*-\s*(\d{7,})', r'\1 / \2', s)

    # 6. Handle spaces separating two complete phone numbers (e.g. 01012345678 01198765432)
    s = re.sub(r'(\d{9,11})\s+((?:01|1[0125]|\+20|0020)\d{7,})', r'\1 / \2', s)

    # 7. Split into potential phone number chunks
    raw_chunks = [c.strip() for c in s.split('/') if c.strip()]

    valid_phones = []

    for chunk in raw_chunks:
        # Extract all digits from chunk
        digits = re.sub(r'[^\d]', '', chunk)
        if not digits:
            continue

        # Check if chunk contains two glued Egyptian mobile numbers (>= 20 digits)
        sub_list = []
        if len(digits) >= 20:
            found_mobiles = re.findall(r'(?:0020|20)?(01[0125]\d{8})', digits)
            if len(found_mobiles) >= 2:
                sub_list = found_mobiles
            else:
                sub_list = [digits]
        else:
            sub_list = [digits]

        for d in sub_list:
            # Strip Egyptian country code
            if d.startswith('0020') and len(d) >= 14:
                d = d[4:]
            elif d.startswith('20') and len(d) in (12, 13) and d[2] in '12':
                d = d[2:]

            # Add missing leading 0 for 10-digit mobile numbers starting with 10, 11, 12, 15
            if len(d) == 10 and d.startswith(('10', '11', '12', '15')):
                d = '0' + d
            # Landlines missing leading 0 (e.g. Cairo 2... with 8 digits -> 02...)
            elif len(d) in (8, 9) and not d.startswith('0') and d.startswith(('2', '3')):
                d = '0' + d

            # Minimum 7 digits for a valid number
            if len(d) >= 7 and d not in valid_phones:
                valid_phones.append(d)

    if valid_phones:
        return ' - '.join(valid_phones)

    only_digits = re.sub(r'[^\d]', '', s)
    return only_digits if only_digits else s.strip()

with app.app_context():
    try:
        if not os.environ.get('DATABASE_URL'):
            db.create_all()
            if not User.query.filter_by(username='admin').first():
                hashed = generate_password_hash('admin123')
                default_admin = User(username='admin', password_hash=hashed, role='admin')
                db.session.add(default_admin)
                db.session.commit()
        # Migrate any legacy 'مرتجع بشحن' status to standard 'مرتجع'
        Order.query.filter_by(status='مرتجع بشحن').update({'status': 'مرتجع'})
        db.session.commit()
    except Exception:
        pass

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash('تم تسجيل الدخول بنجاح!', 'success')
            return redirect(url_for('index'))
        flash('اسم المستخدم أو كلمة المرور غير صحيحة', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('تم تسجيل الخروج', 'info')
    return redirect(url_for('login'))

def get_company_profit_data():
    """
    Calculate collected net shipping profit strictly from settled orders (courier_settled == True).
    Returns (company_profit, total_earned, total_profit_resets, profit_orders).
    """
    settled_orders = Order.query.options(
        joinedload(Order.courier),
        joinedload(Order.company)
    ).filter(
        Order.courier_settled == True,
        or_(
            Order.status.in_(['تم التوصيل', 'تسليم جزئي / مرتجع']),
            and_(Order.status.in_(['مرتجع', 'مرتجع شركة']), Order.collected_amount > 0)
        )
    ).order_by(Order.id.desc()).all()

    total_earned = 0.0
    profit_orders = []
    for o in settled_orders:
        ship = o.shipping_fee or 0.0
        fee = o.courier_fee or 0.0
        if o.status in ['تم التوصيل', 'تسليم جزئي / مرتجع']:
            profit = ship - fee
        else:
            coll = o.collected_amount or ship
            profit = min(coll, ship) - fee
        total_earned += profit
        profit_orders.append({
            'order': o,
            'profit': profit
        })

    total_profit_resets = db.session.query(
        db.func.sum(TreasuryTransaction.amount)
    ).filter_by(tx_type='تصفير_أرباح').scalar() or 0.0

    company_profit = max(0.0, total_earned - total_profit_resets)
    return company_profit, total_earned, total_profit_resets, profit_orders

@app.route('/')
@login_required
def index():
    # Active tab and scan state
    scanned_tracking = request.args.get('scanned', None)
    search_query = request.args.get('search', '').strip()
    filter_company = request.args.get('company_id') or request.args.get('company', '')
    filter_status = request.args.get('status', '')
    filter_courier = request.args.get('courier_id', '')
    filter_region = request.args.get('region', '')
    filter_duplicates = request.args.get('duplicates', '')
    
    req_tab = request.args.get('tab', 'dashboard')
    if search_query or filter_company or filter_status or filter_courier or filter_region or filter_duplicates or scanned_tracking:
        active_tab = 'orders'
    else:
        active_tab = req_tab

    # 1. Fetch dashboard stats for all standard page loads (skip only for AJAX infinite scroll)
    is_ajax_scroll = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if not is_ajax_scroll:
        metrics = db.session.query(
            Order.status,
            db.func.count(Order.id),
            db.func.sum(Order.cod),
            db.func.sum(Order.shipping_fee),
            db.func.sum(Order.collected_amount)
        ).group_by(Order.status).all()
        
        status_dict = {}
        total_orders = 0
        full_goods = 0.0
        partial_goods = 0.0
        
        for st, count, cod_sum, shipping_sum, collected_sum in metrics:
            cnt = count or 0
            cod_s = cod_sum or 0.0
            ship_s = shipping_sum or 0.0
            coll_s = collected_sum or 0.0
            
            status_dict[st] = cnt
            total_orders += cnt
            
            if st not in ['تم التوصيل', 'تسليم جزئي / مرتجع', 'مرتجع شركة']:
                full_goods += (cod_s - ship_s)
            if st == 'تسليم جزئي / مرتجع':
                partial_goods += (cod_s - coll_s)
                
        company_profit, profit_total_earned, profit_total_resets, profit_orders = get_company_profit_data()

        total_goods = full_goods + partial_goods
        new_in_warehouse = status_dict.get('مخزن', 0)
        postponed = status_dict.get('مؤجل', 0)
        returned = status_dict.get('مرتجع', 0)
        returned_company = status_dict.get('مرتجع شركة', 0)
        with_courier = status_dict.get('مع المندوب', 0)
        
        treasury_sums = db.session.query(TreasuryTransaction.method, db.func.sum(TreasuryTransaction.amount)).group_by(TreasuryTransaction.method).all()
        treasury_dict = dict(treasury_sums)
        treasury_cash = treasury_dict.get('كاش', 0.0)
        treasury_transfer = treasury_dict.get('تحويل', 0.0)
        total_capital = treasury_cash + treasury_transfer + total_goods
        
        active_couriers = Courier.query.count()
        
        region_stats = db.session.query(
            Order.region,
            db.func.count(Order.id).label('count'),
            db.func.sum(Order.cod - Order.shipping_fee).label('net')
        ).group_by(Order.region).all()
        
        company_stats = db.session.query(
            Company.name,
            db.func.count(Order.id).label('count'),
            db.func.sum(Order.cod - Order.shipping_fee).label('net')
        ).join(Order).group_by(Company.name).all()
        
        deposit_history = TreasuryTransaction.query.filter(
            TreasuryTransaction.tx_type.in_(['إيداع_يدوي', 'مصروفات', 'سحب_محفظة', 'تصفير_أرباح'])
        ).order_by(TreasuryTransaction.created_at.desc()).limit(50).all()
    else:
        total_orders = 0
        total_goods = 0.0
        total_capital = 0.0
        treasury_cash = 0.0
        treasury_transfer = 0.0
        company_profit = 0.0
        profit_orders = []
        profit_total_earned = 0.0
        profit_total_resets = 0.0
        active_couriers = 0
        new_in_warehouse = 0
        postponed = 0
        returned = 0
        returned_company = 0
        with_courier = 0
        partial_goods = 0.0
        region_stats = []
        company_stats = []
        deposit_history = []

    # Orders & Search & Filters
    query = Order.query.options(joinedload(Order.company), joinedload(Order.courier))
    
    # Duplicate phone filtering: only run full table scan if explicitly requested
    if filter_duplicates == '1':
        duplicate_phones_query = db.session.query(Order.phone).group_by(Order.phone).having(db.func.count(Order.id) > 1).all()
        duplicate_phones = set([r[0] for r in duplicate_phones_query if r[0]])
        if duplicate_phones:
            query = query.filter(Order.phone.in_(list(duplicate_phones)))
        else:
            query = query.filter(Order.id == -1)
    else:
        duplicate_phones = set()
    
    if search_query:
        clean_search = search_query.strip()
        eng_search = clean_search.translate(_ARABIC_TO_ENGLISH_DIGITS)
        query = query.filter(or_(
            Order.tracking_number.ilike(f'%{clean_search}%'),
            Order.tracking_number.ilike(f'%{eng_search}%'),
            Order.client_name.ilike(f'%{clean_search}%'),
            Order.client_name.ilike(f'%{eng_search}%'),
            Order.phone.ilike(f'%{clean_search}%'),
            Order.phone.ilike(f'%{eng_search}%'),
            Order.address.ilike(f'%{clean_search}%'),
            Order.content.ilike(f'%{clean_search}%'),
            Order.batch_id == clean_search,
            Order.batch_id == eng_search
        ))
        
    if filter_company:
        if filter_company == 'none':
            query = query.filter(Order.company_id.is_(None))
        elif filter_company == 'all':
            query = query.filter(Order.company_id.isnot(None))
        else:
            query = query.filter_by(company_id=filter_company)
        
    if filter_status:
        if filter_status == 'none':
            query = query.filter(or_(Order.status.is_(None), Order.status == ''))
        elif filter_status == 'all_returns':
            query = query.filter(Order.status.in_(['مرتجع', 'تسليم جزئي / مرتجع', 'مرتجع بشحن']))
        elif filter_status == 'all_inclusive':
            pass
        else:
            query = query.filter_by(status=filter_status)
    else:
        if not search_query:
            query = query.filter(or_(~Order.status.in_(['مرتجع شركة', 'تم التوصيل']), Order.status.is_(None)))
        
    if filter_courier:
        if filter_courier == 'none':
            query = query.filter(Order.courier_id.is_(None))
        elif filter_courier == 'all':
            query = query.filter(Order.courier_id.isnot(None))
        else:
            query = query.filter_by(courier_id=filter_courier)
        
    if filter_region:
        if filter_region == 'none':
            query = query.filter(or_(Order.region.is_(None), Order.region == '', Order.region == 'غير محدد'))
        elif filter_region == 'all':
            query = query.filter(and_(Order.region.isnot(None), Order.region != '', Order.region != 'غير محدد'))
        else:
            query = query.filter_by(region=filter_region)
        
    # Get aggregates efficiently from DB in 1 single fast query
    cod_expr = db.case(
        (Order.status == 'تسليم جزئي / مرتجع', db.func.coalesce(Order.cod, 0.0) - db.func.coalesce(Order.collected_amount, 0.0)),
        else_=db.func.coalesce(Order.cod, 0.0)
    )
    net_expr = db.case(
        (Order.status == 'تسليم جزئي / مرتجع', db.func.coalesce(Order.cod, 0.0) - db.func.coalesce(Order.collected_amount, 0.0)),
        else_=db.func.coalesce(Order.cod, 0.0) - db.func.coalesce(Order.shipping_fee, 0.0)
    )
    agg = query.with_entities(
        db.func.count(Order.id),
        db.func.sum(cod_expr),
        db.func.sum(Order.shipping_fee),
        db.func.sum(net_expr)
    ).first()
    
    filtered_orders_count = agg[0] or 0
    filtered_cod = agg[1] or 0.0
    filtered_shipping = agg[2] or 0.0
    filtered_net = agg[3] or 0.0
    
    # Pagination: default 500 orders so full company lists load instantly and select all works across all 200+ orders
    per_page = request.args.get('per_page', 500, type=int)
    page = request.args.get('page', 1, type=int)
    pagination = query.order_by(Order.id.desc()).paginate(page=page, per_page=per_page, error_out=False, count=False)
    pagination.total = filtered_orders_count
    all_orders = pagination.items
    
    # Check duplicate phones ONLY in memory for current page orders (0 database queries)
    from collections import Counter
    if filter_duplicates != '1' and all_orders:
        phone_counts = Counter(o.phone for o in all_orders if o.phone)
        duplicate_phones = {p for p, c in phone_counts.items() if c > 1}
    elif filter_duplicates != '1':
        duplicate_phones = set()

    # Couriers & Companies for Modals
    couriers = Courier.query.order_by(Courier.name).all()
    companies = Company.query.order_by(Company.name).all()
    
    # Canonical statuses for modals and forms
    all_statuses = ['مخزن', 'مع المندوب', 'تم التوصيل', 'تسليم جزئي / مرتجع', 'مرتجع', 'مرتجع شركة', 'مؤجل']
    statuses = all_statuses
    
    # All registered regions in DB (for datalists in modals)
    regions = [r[0] for r in db.session.query(Order.region).filter(and_(Order.region.isnot(None), Order.region != '', Order.region != 'غير محدد')).distinct().all()]

    # Filter-specific active entities (exclude empty regions, couriers, companies, and statuses with 0 active orders)
    if filter_status:
        if filter_status == 'none':
            status_scope = or_(Order.status.is_(None), Order.status == '')
        elif filter_status == 'all_returns':
            status_scope = Order.status.in_(['مرتجع', 'تسليم جزئي / مرتجع', 'مرتجع بشحن'])
        elif filter_status == 'all_inclusive':
            status_scope = None
        else:
            status_scope = (Order.status == filter_status)
    else:
        status_scope = or_(~Order.status.in_(['مرتجع شركة', 'تم التوصيل']), Order.status.is_(None))

    # 1. Active Regions (omit empty regions)
    active_regions_q = db.session.query(Order.region, db.func.count(Order.id))\
        .filter(and_(Order.region.isnot(None), Order.region != '', Order.region != 'غير محدد'))
    if status_scope is not None:
        active_regions_q = active_regions_q.filter(status_scope)
    filter_regions = [{'name': r[0], 'count': r[1]} for r in active_regions_q.group_by(Order.region).having(db.func.count(Order.id) > 0).order_by(Order.region).all()]
    if filter_region and filter_region not in ['all', 'none']:
        if not any(r['name'] == filter_region for r in filter_regions):
            filter_regions.append({'name': filter_region, 'count': 0})

    # 2. Active Couriers (omit empty couriers)
    active_couriers_q = db.session.query(Courier.id, Courier.name, db.func.count(Order.id))\
        .join(Order, Courier.id == Order.courier_id)
    if status_scope is not None:
        active_couriers_q = active_couriers_q.filter(status_scope)
    filter_couriers = [{'id': c[0], 'name': c[1], 'count': c[2]} for c in active_couriers_q.group_by(Courier.id, Courier.name).having(db.func.count(Order.id) > 0).order_by(Courier.name).all()]
    if filter_courier and filter_courier not in ['all', 'none']:
        if not any(str(c['id']) == str(filter_courier) for c in filter_couriers):
            selected_c = db.session.get(Courier, int(filter_courier)) if str(filter_courier).isdigit() else None
            if selected_c:
                filter_couriers.append({'id': selected_c.id, 'name': selected_c.name, 'count': 0})

    # 3. Active Companies (omit empty companies)
    active_companies_q = db.session.query(Company.id, Company.name, db.func.count(Order.id))\
        .join(Order, Company.id == Order.company_id)
    if status_scope is not None:
        active_companies_q = active_companies_q.filter(status_scope)
    filter_companies = [{'id': comp[0], 'name': comp[1], 'count': comp[2]} for comp in active_companies_q.group_by(Company.id, Company.name).having(db.func.count(Order.id) > 0).order_by(Company.name).all()]
    if filter_company and filter_company not in ['all', 'none']:
        if not any(str(comp['id']) == str(filter_company) for comp in filter_companies):
            selected_comp = db.session.get(Company, int(filter_company)) if str(filter_company).isdigit() else None
            if selected_comp:
                filter_companies.append({'id': selected_comp.id, 'name': selected_comp.name, 'count': 0})

    # 4. Active Statuses (omit statuses with 0 orders)
    active_statuses_q = db.session.query(Order.status, db.func.count(Order.id))\
        .filter(and_(Order.status.isnot(None), Order.status != ''))\
        .group_by(Order.status).having(db.func.count(Order.id) > 0).all()
    status_order_map = {'مخزن': 1, 'مع المندوب': 2, 'تم التوصيل': 3, 'تسليم جزئي / مرتجع': 4, 'مرتجع': 5, 'مرتجع شركة': 6, 'مؤجل': 7}
    filter_statuses = [
        {'name': s[0], 'count': s[1]}
        for s in sorted(active_statuses_q, key=lambda x: status_order_map.get(x[0], 99))
    ]
    if filter_status and filter_status not in ['all_returns', 'all_inclusive', 'none']:
        if not any(s['name'] == filter_status for s in filter_statuses):
            filter_statuses.append({'name': filter_status, 'count': 0})
    
    # Scanned order logic
    scanned_orders = []
    if scanned_tracking:
        scanned_orders = Order.query.filter(or_(
            Order.tracking_number == scanned_tracking,
            Order.phone.contains(scanned_tracking),
            Order.client_name.contains(scanned_tracking)
        )).order_by(Order.id.desc()).all()
        
        if not scanned_orders:
            flash('لا يوجد أوردر مطابق للبحث!', 'danger')
    
    return render_template('index.html', 
                           total_orders=total_orders, 
                           total_goods=total_goods,
                           total_capital=total_capital,
                           treasury_cash=treasury_cash,
                           treasury_transfer=treasury_transfer,
                           company_profit=company_profit,
                           profit_orders=profit_orders,
                           profit_total_earned=profit_total_earned,
                           profit_total_resets=profit_total_resets,
                           active_couriers=active_couriers,
                           new_in_warehouse=new_in_warehouse,
                           postponed=postponed,
                           returned=returned,
                           returned_company=returned_company,
                           with_courier=with_courier,
                           orders=all_orders,
                           pagination=pagination,
                           search_query=search_query,
                           filter_company=filter_company,
                           filter_status=filter_status,
                           filter_courier=filter_courier,
                           filter_region=filter_region,
                           filter_duplicates=filter_duplicates,
                           regions=regions,
                           filtered_orders_count=filtered_orders_count,
                           filtered_cod=filtered_cod,
                           filtered_net=filtered_net,
                           region_stats=region_stats,
                           company_stats=company_stats,
                           couriers=couriers,
                           companies=companies,
                           statuses=statuses,
                           all_statuses=all_statuses,
                           filter_companies=filter_companies,
                           filter_couriers=filter_couriers,
                           filter_regions=filter_regions,
                           filter_statuses=filter_statuses,
                           partial_goods=partial_goods,
                           active_tab=active_tab,
                           scanned_orders=scanned_orders,
                           scanned_query=scanned_tracking,
                           deposit_history=deposit_history,
                           duplicate_phones=duplicate_phones)

@app.route('/export_excel', methods=['GET', 'POST'])
@login_required
def export_excel():
    order_ids = request.form.getlist('order_ids') or request.args.getlist('order_ids')

    query = Order.query.options(joinedload(Order.company), joinedload(Order.courier))
    
    if order_ids:
        query = query.filter(Order.id.in_(order_ids))
    elif request.method == 'POST' and request.form.get('action') == 'export_excel' and not order_ids:
        flash('لم يتم تحديد أي أوردر لتصديره للإكسيل!', 'warning')
        return redirect(url_for('index', tab='orders'))
    else:
        search_query = (request.values.get('search') or '').strip()
        filter_company = request.values.get('company_id') or request.values.get('company', '')
        filter_status = request.values.get('status', '')
        filter_courier = request.values.get('courier_id', '')
        filter_region = request.values.get('region', '')
        filter_duplicates = request.values.get('duplicates', '')

        if search_query:
            clean_search = search_query.strip()
            query = query.filter(or_(
                Order.tracking_number.ilike(f'%{clean_search}%'),
                Order.client_name.ilike(f'%{clean_search}%'),
                Order.phone.ilike(f'%{clean_search}%'),
                Order.address.ilike(f'%{clean_search}%'),
                Order.content.ilike(f'%{clean_search}%'),
                Order.batch_id == clean_search
            ))
        if filter_company:
            if filter_company == 'none':
                query = query.filter(Order.company_id.is_(None))
            elif filter_company == 'all':
                query = query.filter(Order.company_id.isnot(None))
            else:
                query = query.filter_by(company_id=filter_company)
        if filter_status:
            if filter_status == 'none':
                query = query.filter(or_(Order.status.is_(None), Order.status == ''))
            elif filter_status == 'all_returns':
                query = query.filter(Order.status.in_(['مرتجع', 'تسليم جزئي / مرتجع', 'مرتجع بشحن']))
            elif filter_status == 'all_inclusive':
                pass
            else:
                query = query.filter_by(status=filter_status)
        else:
            if not search_query:
                query = query.filter(or_(~Order.status.in_(['مرتجع شركة', 'تم التوصيل']), Order.status.is_(None)))
        if filter_courier:
            if filter_courier == 'none':
                query = query.filter(Order.courier_id.is_(None))
            elif filter_courier == 'all':
                query = query.filter(Order.courier_id.isnot(None))
            else:
                query = query.filter_by(courier_id=filter_courier)
        if filter_region:
            if filter_region == 'none':
                query = query.filter(or_(Order.region.is_(None), Order.region == '', Order.region == 'غير محدد'))
            elif filter_region == 'all':
                query = query.filter(and_(Order.region.isnot(None), Order.region != '', Order.region != 'غير محدد'))
            else:
                query = query.filter_by(region=filter_region)
        if filter_duplicates == '1':
            duplicate_phones_query = db.session.query(Order.phone).group_by(Order.phone).having(db.func.count(Order.id) > 1).all()
            duplicate_phones = set([r[0] for r in duplicate_phones_query if r[0]])
            if duplicate_phones:
                query = query.filter(Order.phone.in_(list(duplicate_phones)))
            
    orders = query.order_by(Order.id.desc()).all()
    
    columns = [
        'رقم البوليصة', 'العميل', 'رقم التليفون', 'المنطقة', 'العنوان',
        'الشركة', 'المندوب', 'الإجمالي', 'الشحن', 'الصافي',
        'عمولة المندوب', 'الحالة', 'التاريخ'
    ]
    
    data = []
    for o in orders:
        # Use collected_amount if present (partial delivery), otherwise cod
        total_cod = (o.collected_amount if o.collected_amount is not None else o.cod) or 0.0
        shipping = o.shipping_fee or 0.0
        net = total_cod - shipping
        
        status_display = o.status or ''
        if o.status == 'مع المندوب' and o.courier:
            status_display = f"مع المندوب ({o.courier.name})"
            
        data.append({
            'رقم البوليصة': o.tracking_number or '',
            'العميل': o.client_name or '',
            'رقم التليفون': o.phone or '',
            'المنطقة': o.region or '',
            'العنوان': o.address or '',
            'الشركة': o.company.name if o.company else '',
            'المندوب': o.courier.name if o.courier else '',
            'الإجمالي': total_cod,
            'الشحن': shipping,
            'الصافي': net,
            'عمولة المندوب': o.courier_fee or 0.0,
            'الحالة': status_display,
            'التاريخ': o.created_at.strftime('%Y-%m-%d') if o.created_at else ''
        })
        
    df = pd.DataFrame(data, columns=columns)
    
    if not df.empty:
        totals = {
            'رقم البوليصة': 'الإجمالي الكلي',
            'العميل': '',
            'رقم التليفون': '',
            'المنطقة': '',
            'العنوان': '',
            'الشركة': '',
            'المندوب': '',
            'الإجمالي': df['الإجمالي'].sum(),
            'الشحن': df['الشحن'].sum(),
            'الصافي': df['الصافي'].sum(),
            'عمولة المندوب': df['عمولة المندوب'].sum(),
            'الحالة': '',
            'التاريخ': ''
        }
        df = pd.concat([df, pd.DataFrame([totals])], ignore_index=True)
    
    output = BytesIO()
    # use ExcelWriter
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Orders')
        
    output.seek(0)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"orders_export_{timestamp}.xlsx"
    
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/order/new', methods=['POST'])
def new_order():
    client_name = request.form.get('client_name', '').strip()
    phone = clean_phone_smart(request.form.get('phone', ''))
    address = request.form.get('address', '')
    region = request.form.get('region', '')
    content = request.form.get('content', '')
    company_name = request.form.get('company_name', '').strip()
    
    company_id = None
    if company_name:
        company = Company.query.filter_by(name=company_name).first()
        if not company:
            company = Company(name=company_name)
            db.session.add(company)
            db.session.flush()
        company_id = company.id

    courier_name = request.form.get('courier_name', '').strip()
    courier_id = None
    status = 'مخزن'
    if courier_name:
        courier = Courier.query.filter_by(name=courier_name).first()
        if not courier:
            courier = Courier(name=courier_name)
            db.session.add(courier)
            db.session.flush()
        courier_id = courier.id
        status = 'مع المندوب'
        
    try:
        cod = float(request.form.get('cod', 0.0))
        shipping_fee = float(request.form.get('shipping_fee', 0.0))
    except ValueError:
        cod, shipping_fee = 0.0, 0.0
        
    tracking_number = f"SHP-{uuid.uuid4().hex[:6].upper()}"
    
    order = Order(
        tracking_number=tracking_number,
        client_name=client_name,
        phone=phone,
        address=address,
        region=region,
        content=content,
        cod=cod,
        shipping_fee=shipping_fee,
        status=status,
        company_id=company_id,
        courier_id=courier_id
    )
    db.session.add(order)
    db.session.commit()
    flash('تمت إضافة الأوردر اليدوي بنجاح!', 'success')
    return redirect(url_for('index', tab='orders'))

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash('لم يتم العثور على ملف', 'danger')
        return redirect(url_for('index'))
    file = request.files['file']
    if file.filename == '':
        flash('لم يتم اختيار ملف', 'danger')
        return redirect(url_for('index'))
    if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        company_name_input = request.form.get('company_name', '').strip()
        if not company_name_input:
            flash('يجب كتابة اسم الشركة!', 'danger')
            return redirect(url_for('index'))
            
        try:
            # Read directly from the file stream (works on serverless like Vercel)
            df = pd.read_excel(file, header=None)
            success_count = 0
            
            def normalize_arabic(text):
                if not isinstance(text, str): return ""
                text = str(text).replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا')
                text = text.replace('ة', 'ه')
                return text.lower().strip()

            col_map = {'name': -1, 'phone': -1, 'address': -1, 'region': -1, 'city': -1, 'cod': -1, 'shipping': -1, 'company': -1, 'content': -1}
            header_row_idx = 0
            
            # Scan first 20 rows to find the headers
            for idx, row in df.head(20).iterrows():
                found_keys = 0
                temp_map = {'name': -1, 'phone': -1, 'address': -1, 'region': -1, 'city': -1, 'cod': -1, 'shipping': -1, 'company': -1, 'content': -1}
                cod_priority = 999
                
                for c_idx, val in enumerate(row):
                    norm_val = normalize_arabic(val)
                    if not norm_val or norm_val == 'nan': continue
                    
                    # Split into words to avoid substring matches in data (like 'قاسم' matching 'اسم')
                    words = norm_val.split()
                    
                    if temp_map['name'] == -1 and any(k in words for k in ['عميل', 'العميل', 'مستلم', 'المستلم', 'اسم', 'الاسم']) and 'صفح' not in norm_val and 'شرك' not in norm_val:
                        temp_map['name'] = c_idx; found_keys += 1
                    elif temp_map['phone'] == -1 and any(k in norm_val for k in ['تليفون', 'موبايل', 'هاتف', 'phone', 'رقم']):
                        if 'فاتور' not in norm_val and 'بوليص' not in norm_val and 'سيريال' not in norm_val and 'حساب' not in norm_val and 'قوم' not in norm_val:
                            temp_map['phone'] = c_idx; found_keys += 1
                    elif temp_map['address'] == -1 and 'عنوان' in norm_val:
                        temp_map['address'] = c_idx; found_keys += 1
                    elif temp_map['region'] == -1 and ('محافظ' in norm_val or 'منطق' in norm_val):
                        temp_map['region'] = c_idx; found_keys += 1
                    elif temp_map['city'] == -1 and ('مدين' in norm_val or 'مركز' in norm_val):
                        temp_map['city'] = c_idx; found_keys += 1
                    elif temp_map['shipping'] == -1 and any(k in norm_val for k in ['شحن', 'توصيل', 'shipping']):
                        temp_map['shipping'] = c_idx; found_keys += 1
                    elif temp_map['company'] == -1 and any(k in norm_val for k in ['شرك', 'صفح', 'بيدج', 'page', 'company']):
                        temp_map['company'] = c_idx; found_keys += 1
                    elif temp_map['content'] == -1 and any(k in norm_val for k in ['محتوى', 'منتج', 'الاوردر', 'تفاصيل', 'بيان', 'مطلوب']):
                        temp_map['content'] = c_idx; found_keys += 1
                    else:
                        # COD priority: 0 is highest
                        cod_keywords = ['متبقى', 'المتبقى', 'متبقي', 'المتبقي', 'صافى', 'الصافى', 'صافي', 'الصافي', 'تحصيل', 'اجمالى', 'الاجمالى', 'اجمالي', 'الاجمالي', 'مطلوب', 'المطلوب']
                        for p, k in enumerate(cod_keywords):
                            if k in norm_val:
                                if p < cod_priority:
                                    if cod_priority == 999: found_keys += 1
                                    temp_map['cod'] = c_idx
                                    cod_priority = p
                                break
                
                # Require at least 3 columns to match to confidently call it a header row
                if found_keys >= 3:
                    col_map = temp_map
                    header_row_idx = idx
                    break

            # Find or create the company provided by the user
            company = Company.query.filter_by(name=company_name_input).first()
            if not company:
                company = Company(name=company_name_input)
                db.session.add(company)
                db.session.commit()

            batch_id = str(uuid.uuid4().hex[:8])
            
            def get_cell(row, c_idx, default=''):
                if c_idx != -1 and c_idx < len(row):
                    val = row.iloc[c_idx]
                    return val if pd.notna(val) else default
                return default

            for index, row in df.iterrows():
                # Skip the header row and anything above it
                if index <= header_row_idx:
                    continue
                    
                try:
                    client_name = str(get_cell(row, col_map['name'])).strip()
                    raw_phone = get_cell(row, col_map['phone'])
                    phone = clean_phone_smart(raw_phone)
                    
                    if client_name.lower() == 'nan': client_name = ''
                    if not client_name and not phone:
                        continue
                    
                    tracking_number = f"SHP-{uuid.uuid4().hex[:6].upper()}"
                        
                    address_part1 = str(get_cell(row, col_map['region'])).strip()
                    address_part2 = str(get_cell(row, col_map['city'])).strip()
                    address_part3 = str(get_cell(row, col_map['address'])).strip()
                    
                    full_address = " - ".join([p for p in [address_part1, address_part2, address_part3] if p and p.lower() != 'nan'])
                    region = 'غير محدد'
                    
                    cod_val = get_cell(row, col_map['cod'], 0.0)
                    shipping_val = get_cell(row, col_map['shipping'], 70.0)
                    content_val = str(get_cell(row, col_map['content'])).strip()
                    if content_val.lower() == 'nan': content_val = ''
                    def parse_float(val, default_val=0.0):
                        if pd.isna(val) or str(val).strip() == '': return default_val
                        if isinstance(val, (int, float)): return float(val)
                        cleaned = re.sub(r'[^\d.]', '', str(val))
                        try:
                            return float(cleaned) if cleaned else default_val
                        except ValueError:
                            return default_val

                    cod = parse_float(cod_val, 0.0)
                    shipping_fee = parse_float(shipping_val, 70.0)
                    
                    order_company_id = company.id
                    order = Order(
                        tracking_number=tracking_number,
                        client_name=client_name,
                        phone=phone,
                        address=full_address,
                        region=region,
                        cod=cod,
                        shipping_fee=shipping_fee,
                        status='مخزن',
                        company_id=order_company_id,
                        batch_id=batch_id,
                        content=content_val,
                        created_at=datetime.now(),
                        collected_amount=cod,
                        courier_fee=0.0
                    )
                    db.session.add(order)
                    success_count += 1
                except Exception as e:
                    app.logger.error(f"Error processing row {index}: {e}")
                    continue
                    
            db.session.commit()
            flash(f'تم رفع وحفظ {success_count} أوردر بنجاح! راجع الأوردرات في هذا الجدول ثم يمكنك تحديدها جميعاً والضغط على (طباعة البوالص).', 'success')
            return redirect(url_for('index', search=batch_id, tab='orders'))
            
        except Exception as e:
            flash(f'حدث خطأ أثناء قراءة الملف: {str(e)}', 'danger')
            return redirect(url_for('index'))
    else:
        flash('صيغة الملف غير مدعومة، يرجى رفع ملف Excel (.xlsx أو .xls)', 'danger')
        return redirect(url_for('index'))

@app.route('/print/<batch_id>')
def print_batch(batch_id):
    orders_to_print = Order.query.options(joinedload(Order.company), joinedload(Order.courier)).filter_by(batch_id=batch_id).all()
    if not orders_to_print:
        flash('لا توجد أوردرات في هذه الدفعة لطباعتها', 'warning')
        return redirect(url_for('index', tab='orders'))
    return render_template('print.html', orders=orders_to_print)

@app.route('/api/order/<int:order_id>', methods=['GET'])
def api_order(order_id):
    order = Order.query.get_or_404(order_id)
    return {
        'id': order.id,
        'tracking_number': order.tracking_number,
        'client_name': order.client_name,
        'phone': order.phone,
        'region': order.region,
        'address': order.address,
        'cod': order.cod,
        'shipping_fee': order.shipping_fee,
        'content': order.content or '',
        'company_name': order.company.name if order.company else '',
        'courier_name': order.courier.name if order.courier else '',
        'status': order.status,
        'collected_amount': order.collected_amount if order.collected_amount is not None else order.cod,
        'courier_fee': order.courier_fee if order.courier_fee is not None else 0
    }

@app.route('/api/scan', methods=['GET'])
def api_scan():
    query = request.args.get('q', '').strip()
    if not query:
        return {'error': 'No query provided'}, 400
        
    eng_query = query.translate(_ARABIC_TO_ENGLISH_DIGITS)
    orders = Order.query.options(joinedload(Order.company), joinedload(Order.courier)).filter(or_(
        Order.tracking_number.ilike(f'%{query}%'),
        Order.tracking_number.ilike(f'%{eng_query}%'),
        Order.phone.ilike(f'%{query}%'),
        Order.phone.ilike(f'%{eng_query}%'),
        Order.client_name.ilike(f'%{query}%'),
        Order.client_name.ilike(f'%{eng_query}%')
    )).all()
    
    if not orders:
        return {'error': 'لا يوجد أوردر مطابق'}, 404
        
    result = []
    for o in orders:
        result.append({
            'id': o.id,
            'tracking_number': o.tracking_number,
            'created_at': o.created_at.strftime('%Y-%m-%d') if o.created_at else '',
            'company_name': o.company.name if o.company else '',
            'client_name': o.client_name or '',
            'phone': o.phone or '',
            'address': o.address or '',
            'region': o.region or '',
            'content': o.content or '',
            'cod': o.cod or 0.0,
            'shipping_fee': o.shipping_fee or 0.0,
            'collected_amount': o.collected_amount,
            'status': o.status or 'مخزن',
            'courier_name': o.courier.name if o.courier else '',
            'is_copied': bool(o.is_copied)
        })
    return {'orders': result}

@app.route('/api/order/<int:order_id>/copy', methods=['POST'])
def api_order_copy(order_id):
    order = Order.query.get_or_404(order_id)
    order.is_copied = True
    db.session.commit()
    return {'success': True}

def reverse_order_treasury_collection(order, reason="", amount=None):
    """
    If an order was settled (delivered, partial delivery, etc.) and net cash entered the treasury,
    reverse that collection from treasury and reset settlement fields.
    """
    if amount is not None:
        net_amount = amount
    else:
        net_amount = (order.collected_amount or 0.0) - (order.courier_fee or 0.0)

    if (order.courier_settled or amount is not None) and net_amount > 0:
        tx_type = 'استرجاع_مخزن' if 'مخزن' in reason else 'إلغاء_تسليم'
        reversal_tx = TreasuryTransaction(
            amount=-net_amount,
            method='كاش',
            tx_type=tx_type,
            entity_id=order.courier_id,
            notes=f'خصم من الخزينة للأوردر {order.tracking_number} ({reason})'
        )
        db.session.add(reversal_tx)
    order.courier_settled = False
    order.company_settled = False
    order.collected_amount = None
    order.courier_fee = None

@app.route('/scan', methods=['POST'])
def scan():
    tracking_number = request.form.get('tracking_number', '').strip()
    action = request.form.get('action')
    courier_id = request.form.get('courier_id')
    
    if tracking_number and action:
        order = Order.query.filter_by(tracking_number=tracking_number).first()
        if order:
            if action == 'assign_courier' and courier_id:
                if order.courier_settled:
                    reverse_order_treasury_collection(order, reason='إعادة تسليم للمندوب عبر الباركود')
                order.status = 'مع المندوب'
                order.courier_id = int(courier_id)
                db.session.commit()
                flash(f'تم تسليم الأوردر {tracking_number} للمندوب بنجاح!', 'success')
            elif action == 'set_region':
                region_name = request.form.get('region_name')
                if not region_name:
                    flash('يجب كتابة اسم المنطقة.', 'danger')
                    return redirect(url_for('index', tab='orders'))
                order.region = region_name
                db.session.commit()
                flash('تم تحديد المنطقة بنجاح.', 'success')
            elif action == 'return_company':
                if order.status == 'تم التوصيل' and order.courier_settled:
                    reverse_order_treasury_collection(order, reason='تحويل لمرتجع شركة عبر الباركود')
                order.status = 'مرتجع شركة'
                db.session.commit()
                flash(f'تم تحويل الأوردر {tracking_number} لمرتجع شركة', 'dark')
            elif action == 'return':
                if order.courier_settled:
                    reverse_order_treasury_collection(order, reason='تحويل لمرتجع عبر الباركود')
                order.status = 'مرتجع'
                db.session.commit()
                flash(f'تم تحويل الأوردر {tracking_number} لمرتجع', 'danger')
            return redirect(url_for('index', tab='scan'))
    
    # If it's just a scan without action (initial scan) or failed action
    return redirect(url_for('index', tab='scan', scanned=tracking_number))

@app.route('/order/<int:order_id>/delete', methods=['POST'])
def delete_order(order_id):
    order = Order.query.get_or_404(order_id)
    reverse_order_treasury_collection(order, reason='مسح الأوردر')
    db.session.delete(order)
    db.session.commit()
    flash('تم مسح الأوردر بنجاح', 'success')
    return redirect(url_for('index', tab='orders'))

@app.route('/orders/bulk_action', methods=['POST'])
def bulk_action():
    action = request.form.get('action')
    order_ids = request.form.getlist('order_ids')
    courier_name = request.form.get('courier_name', '').strip()
    region_name = request.form.get('bulk_region_name', '').strip()
    fee_raw = request.form.get('bulk_shipping_fee', '').strip()
    
    if not order_ids:
        flash('لم يتم تحديد أي أوردر!', 'danger')
        return redirect(url_for('index', tab='orders'))
        
    orders_to_update = Order.query.options(joinedload(Order.company), joinedload(Order.courier)).filter(Order.id.in_(order_ids)).all()
        
    if action == 'delete':
        for order in orders_to_update:
            reverse_order_treasury_collection(order, reason='مسح الأوردر')
            db.session.delete(order)
        db.session.commit()
        flash(f'تم مسح {len(order_ids)} أوردر بنجاح', 'success')
        return redirect(url_for('index', tab='orders'))

    if action == 'print_selected':
        return render_template('print.html', orders=orders_to_update)

    if action == 'export_excel':
        return export_excel()

    updated_details = []

    # 1. Assign courier if courier_name is provided
    if courier_name:
        courier = Courier.query.filter_by(name=courier_name).first()
        if not courier:
            courier = Courier(name=courier_name)
            db.session.add(courier)
            db.session.flush() # get ID
            
        for order in orders_to_update:
            if order.courier_settled:
                reverse_order_treasury_collection(order, reason=f'إعادة تسليم للمندوب {courier_name}')
            order.courier_id = courier.id
            if action not in ['warehouse', 'delivered', 'return', 'return_company']:
                order.status = 'مع المندوب'
        updated_details.append(f'المندوب: {courier_name}')

    # 2. Set region if provided
    if region_name:
        for order in orders_to_update:
            order.region = region_name
        updated_details.append(f'المنطقة: {region_name}')

    # 3. Set shipping fee if provided
    if fee_raw:
        try:
            new_fee = float(fee_raw)
            for order in orders_to_update:
                order.shipping_fee = new_fee
            updated_details.append(f'سعر الشحن: {new_fee} ج.م')
        except ValueError:
            flash('سعر الشحن يجب أن يكون رقماً صحيحاً.', 'danger')

    # 4. Handle specific status actions
    if action == 'warehouse':
        for order in orders_to_update:
            reverse_order_treasury_collection(order, reason='استرجاع للمخزن')
            order.status = 'مخزن'
            order.courier_id = None
        updated_details.append('الحالة: مخزن')
    elif action == 'return_company':
        for order in orders_to_update:
            if order.status == 'تم التوصيل' and order.courier_settled:
                reverse_order_treasury_collection(order, reason='تحويل لمرتجع شركة')
            else:
                order.collected_amount = None
                order.courier_fee = None
            order.status = 'مرتجع شركة'
        updated_details.append('الحالة: مرتجع شركة')
    elif action == 'delivered':
        for order in orders_to_update:
            order.status = 'تم التوصيل'
            if order.collected_amount is None:
                order.collected_amount = order.cod
        updated_details.append('الحالة: تم التوصيل')
    elif action == 'return':
        for order in orders_to_update:
            if order.courier_settled:
                reverse_order_treasury_collection(order, reason='تحويل لمرتجع')
            else:
                order.collected_amount = None
                order.courier_fee = None
            order.status = 'مرتجع'
        updated_details.append('الحالة: مرتجع')
    elif action == 'unmark_copied':
        for order in orders_to_update:
            order.is_copied = False
        updated_details.append('إلغاء علامة النسخ')

    db.session.commit()

    if updated_details:
        details_str = ' | '.join(updated_details)
        flash(f'تم تطبيق التعديلات على {len(order_ids)} أوردر بنجاح ({details_str})', 'success')
    else:
        flash('لم تقم بتحديد أي تعديل لتطبيقه على الأوردرات المحددة.', 'warning')

    return redirect(url_for('index', tab='orders'))

@app.route('/order/<int:order_id>/edit', methods=['POST'])
def edit_order(order_id):
    order = Order.query.get_or_404(order_id)
    old_status = order.status
    old_settled = order.courier_settled
    old_collected = order.collected_amount or 0.0
    old_fee = order.courier_fee or 0.0
    old_net = old_collected - old_fee
    
    order.client_name = request.form.get('client_name', order.client_name)
    raw_phone = request.form.get('phone')
    if raw_phone is not None:
        order.phone = clean_phone_smart(raw_phone)
    order.address = request.form.get('address', order.address)
    order.region = request.form.get('region', order.region)
    new_status = request.form.get('status', order.status)
    order.content = request.form.get('content', order.content)
    
    company_name = request.form.get('company_name', '').strip()
    if company_name:
        c = Company.query.filter_by(name=company_name).first()
        if not c:
            c = Company(name=company_name)
            db.session.add(c)
            db.session.flush()
        order.company_id = c.id
    else:
        order.company_id = None
    
    try:
        order.cod = float(request.form.get('cod') or order.cod)
        order.shipping_fee = float(request.form.get('shipping_fee') or order.shipping_fee)
        
        col_amt = request.form.get('collected_amount')
        if col_amt and col_amt.strip() != "":
            order.collected_amount = float(col_amt)
        else:
            order.collected_amount = None
            
        c_fee = request.form.get('courier_fee')
        if c_fee and c_fee.strip() != "":
            order.courier_fee = float(c_fee)
        else:
            order.courier_fee = None
            
        courier_name = request.form.get('courier_name', '').strip()
        if courier_name:
            c = Courier.query.filter_by(name=courier_name).first()
            if not c:
                c = Courier(name=courier_name)
                db.session.add(c)
                db.session.flush()
            order.courier_id = c.id
            if new_status in ['مخزن', 'جديد بالمخزن']:
                new_status = 'مع المندوب'
        elif 'courier_name' in request.form and not courier_name:
            order.courier_id = None
            
    except ValueError:
        pass

    order.status = new_status
    if new_status in ['مرتجع', 'مرتجع شركة']:
        if order.collected_amount is not None and order.cod and order.collected_amount >= order.cod and order.cod > (order.shipping_fee or 0):
            order.collected_amount = None

    # Status transitions & Treasury accounting logic
    new_collected = order.collected_amount or 0.0
    new_fee = order.courier_fee or 0.0
    new_net = new_collected - new_fee

    if new_status in ['مخزن', 'مع المندوب', 'مؤجل']:
        # Leaving delivered/return settlement to go back to warehouse or courier:
        if old_settled and old_net > 0:
            reverse_order_treasury_collection(order, reason=f'تغيير الحالة إلى {new_status}', amount=old_net)
        else:
            order.courier_settled = False
            order.company_settled = False
            order.collected_amount = None
            order.courier_fee = None
            
        if new_status == 'مخزن' and not courier_name:
            order.courier_id = None
            
    elif new_status in ['تم التوصيل', 'تسليم جزئي / مرتجع', 'مرتجع']:
        if old_settled:
            # Already settled before, adjust if amounts changed
            diff = new_net - old_net
            if diff != 0:
                tx = TreasuryTransaction(
                    amount=diff,
                    method='كاش',
                    tx_type='تعديل_تحصيل',
                    entity_id=order.courier_id,
                    notes=f'تعديل تحصيل للأوردر {order.tracking_number} (فارق {diff:+.2f} ج.م)'
                )
                db.session.add(tx)
        else:
            if new_net > 0:
                # First-time settlement via edit modal
                tx = TreasuryTransaction(
                    amount=new_net,
                    method='كاش',
                    tx_type='تحصيل_من_مندوب',
                    entity_id=order.courier_id,
                    notes=f'تحصيل نقدي للأوردر {order.tracking_number} ({new_status})'
                )
                db.session.add(tx)
                order.courier_settled = True
            elif new_status == 'مرتجع':
                order.courier_settled = True

    elif new_status == 'مرتجع شركة':
        if old_settled and old_net > 0 and old_status == 'تم التوصيل':
            reverse_order_treasury_collection(order, reason='تحويل لمرتجع شركة', amount=old_net)
    
    db.session.commit()
    flash('تم تعديل الأوردر بنجاح', 'success')
    return redirect(url_for('index', tab='orders'))

@app.route('/order/<int:order_id>/settlement_edit', methods=['POST'])
@login_required
def order_settlement_edit(order_id):
    order = Order.query.get_or_404(order_id)
    old_status = order.status
    old_settled = order.courier_settled
    old_collected = order.collected_amount or 0.0
    old_fee = order.courier_fee or 0.0
    old_net = old_collected - old_fee
    
    status_choice = request.form.get('status_choice', order.status).strip()
    
    try:
        shipping_fee = float(request.form.get('shipping_fee', order.shipping_fee or 70.0))
        order.shipping_fee = shipping_fee
    except:
        pass
        
    courier_name = request.form.get('courier_name', '').strip()
    if courier_name:
        c = Courier.query.filter_by(name=courier_name).first()
        if not c:
            c = Courier(name=courier_name)
            db.session.add(c)
            db.session.flush()
        order.courier_id = c.id

    if status_choice == 'مخزن':
        # Revert completely to warehouse:
        if old_settled and old_net > 0:
            reverse_order_treasury_collection(order, reason='إعادة للمخزن من تفاصيل الأرباح', amount=old_net)
        else:
            order.courier_settled = False
            order.company_settled = False
            order.collected_amount = None
            order.courier_fee = None
        order.status = 'مخزن'
        flash(f'تمت إعادة الأوردر {order.tracking_number} إلى المخزن وإلغاء تسليمه.', 'success')
    else:
        if status_choice == 'مرتجع_بدون_شحن':
            order.status = 'مرتجع'
            order.collected_amount = 0.0
            try:
                order.courier_fee = float(request.form.get('courier_fee', 0.0) or 0.0)
            except:
                order.courier_fee = 0.0
        elif status_choice == 'مرتجع_بشحن':
            order.status = 'مرتجع'
            try:
                col = float(request.form.get('collected_amount', order.shipping_fee or 0.0) or 0.0)
            except:
                col = order.shipping_fee or 0.0
            order.collected_amount = col if col > 0 else (order.shipping_fee or 70.0)
            try:
                order.courier_fee = float(request.form.get('courier_fee', 0.0) or 0.0)
            except:
                order.courier_fee = 0.0
        else:
            order.status = status_choice
            try:
                order.collected_amount = float(request.form.get('collected_amount', 0.0) or 0.0)
            except:
                order.collected_amount = order.cod if status_choice == 'تم التوصيل' else 0.0
            try:
                order.courier_fee = float(request.form.get('courier_fee', 0.0) or 0.0)
            except:
                order.courier_fee = 0.0

        order.courier_settled = True
        new_collected = order.collected_amount or 0.0
        new_fee = order.courier_fee or 0.0
        new_net = new_collected - new_fee
        
        # Adjust treasury
        if old_settled:
            diff = new_net - old_net
            if diff != 0:
                tx = TreasuryTransaction(
                    amount=diff,
                    method='كاش',
                    tx_type='تعديل_تحصيل',
                    entity_id=order.courier_id,
                    notes=f'تعديل تسليم للأوردر {order.tracking_number} (فارق {diff:+.2f} ج.م)'
                )
                db.session.add(tx)
        else:
            if new_net > 0:
                tx = TreasuryTransaction(
                    amount=new_net,
                    method='كاش',
                    tx_type='تحصيل_من_مندوب',
                    entity_id=order.courier_id,
                    notes=f'تحصيل نقدي للأوردر {order.tracking_number} ({order.status})'
                )
                db.session.add(tx)
        flash(f'تم تعديل تسليم الأوردر {order.tracking_number} وتحديث الأرباح بنجاح!', 'success')

    db.session.commit()
    return redirect(url_for('index', tab='dashboard'))

@app.route('/api/reset_db', methods=['POST'])
def reset_db():
    try:
        # Delete all records from all tables
        db.session.query(TreasuryTransaction).delete()
        db.session.query(Order).delete()
        db.session.query(Courier).delete()
        db.session.query(Company).delete()
        db.session.commit()
        flash('تم تصفير قاعدة البيانات ومسح كل البيانات بنجاح! السيستم الآن جديد تماماً.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('حدث خطأ أثناء تصفير قاعدة البيانات.', 'danger')
    return redirect(url_for('index'))


@app.route('/accounting/company', methods=['GET', 'POST'])
def company_accounting():
    companies = Company.query.all()
    selected_company_id = request.args.get('company_id')
    selected_company = None
    orders = []
    
    company_debt = 0.0
    company_paid = 0.0
    company_balance = 0.0
    
    if selected_company_id:
        selected_company = Company.query.get(selected_company_id)
        if selected_company:
            # 1. Total Debt (Owed to Company) = Sum(Collected - Shipping) for orders that are company_settled=True
            # Wait, if an order is returned with shipping fee, we collected shipping fee from customer, 
            # so the company owes US the shipping fee, or we take it from the collected amount.
            # Usually: Net = Collected_Amount - Shipping_Fee
            order_profits = db.session.query(
                db.func.sum(Order.collected_amount - Order.shipping_fee)
            ).filter_by(company_id=selected_company.id, company_settled=True).scalar() or 0.0
            
            # 1.5 Manual Debt Added to Company
            manual_debt = db.session.query(
                db.func.sum(TreasuryTransaction.amount)
            ).filter_by(tx_type='إضافة_رصيد_لشركة', entity_id=selected_company.id).scalar() or 0.0
            
            company_debt = order_profits + manual_debt
            
            # 2. Total Paid to Company (Negative transactions in Treasury)
            # When we pay the company, we insert a negative amount into TreasuryTransaction
            # So the total paid is the absolute sum of these negative transactions
            paid_sum = db.session.query(
                db.func.sum(TreasuryTransaction.amount)
            ).filter_by(tx_type='صرف_لشركة', entity_id=selected_company.id).scalar() or 0.0
            
            company_paid = abs(paid_sum)
            
            # 3. Current Balance
            company_balance = company_debt - company_paid
            
            # Fetch transaction history for this company (Both payouts and manual additions)
            transactions = TreasuryTransaction.query.filter(
                TreasuryTransaction.entity_id == selected_company.id,
                TreasuryTransaction.tx_type.in_(['صرف_لشركة', 'إضافة_رصيد_لشركة'])
            ).order_by(TreasuryTransaction.created_at.desc()).all()
            
            # 4. Fetch orders ready to be settled (Delivered, Partial, Returned with shipping)
            # We only settle orders that have reached a final status.
            orders = Order.query.options(joinedload(Order.courier)).filter(
                Order.company_id == selected_company.id,
                Order.company_settled == False,
                Order.status.in_(['تم التوصيل', 'تسليم جزئي / مرتجع', 'مرتجع بشحن', 'مرتجع شركة'])
            ).all()
            
            if request.method == 'POST':
                action = request.form.get('action')
                if action == 'settle':
                    settled_count = 0
                    for order in orders:
                        check = request.form.get(f'order_{order.id}')
                        if check == 'on':
                            order.company_settled = True
                            settled_count += 1
                    
                    if settled_count > 0:
                        db.session.commit()
                        flash(f'تم ترحيل {settled_count} أوردر لحساب الشركة بنجاح!', 'success')
                        return redirect(url_for('company_accounting', company_id=selected_company.id))

    return render_template(
        'company_accounting.html', 
        companies=companies, 
        selected_company=selected_company, 
        orders=orders,
        company_debt=company_debt,
        order_profits=order_profits if 'order_profits' in locals() else 0.0,
        manual_debt=manual_debt if 'manual_debt' in locals() else 0.0,
        company_paid=company_paid,
        company_balance=company_balance,
        transactions=transactions if 'transactions' in locals() else []
    )

@app.route('/api/company/<int:company_id>/pay', methods=['POST'])
def company_pay(company_id):
    try:
        amount = float(request.form.get('amount', 0))
        method = request.form.get('method', 'كاش')
        notes = request.form.get('notes', '')
        
        if amount > 0:
            # Payment TO company is a withdrawal from our treasury, so amount is negative!
            tx = TreasuryTransaction(
                amount=-amount,
                method=method,
                tx_type='صرف_لشركة',
                entity_id=company_id,
                notes=notes
            )
            db.session.add(tx)
            db.session.commit()
            flash(f'تم تسجيل سداد للشركة بمبلغ {amount} ({method}) بنجاح!', 'success')
    except Exception as e:
        flash('حدث خطأ أثناء تسجيل الدفعة.', 'danger')
        
    return redirect(url_for('company_accounting', company_id=company_id))

@app.route('/api/company/<int:company_id>/add_balance', methods=['POST'])
def company_add_balance(company_id):
    try:
        amount = float(request.form.get('amount', 0))
        notes = request.form.get('notes', '')
        
        if amount > 0:
            # Adding balance to the company means we owe them more, so it's positive.
            # We use 'مديونية' method so it doesn't affect Cash/Wallet totals.
            tx = TreasuryTransaction(
                amount=amount,
                method='مديونية',
                tx_type='إضافة_رصيد_لشركة',
                entity_id=company_id,
                notes=notes
            )
            db.session.add(tx)
            db.session.commit()
            flash(f'تمت إضافة مديونية بقيمة {amount} ج.م لرصيد الشركة بنجاح', 'success')
        else:
            flash('يجب إدخال مبلغ أكبر من الصفر', 'danger')
    except Exception as e:
        flash('حدث خطأ أثناء الإضافة', 'danger')
        
    return redirect(url_for('company_accounting', company_id=company_id))

@app.route('/treasury/deposit', methods=['POST'])
@login_required
def treasury_deposit():
    try:
        amount = float(request.form.get('amount', 0))
        method = request.form.get('method', 'كاش')
        notes = request.form.get('notes', 'إيداع يدوي')
        
        if amount > 0:
            tx = TreasuryTransaction(
                amount=amount,
                method=method,
                tx_type='إيداع_يدوي',
                entity_id=None,
                notes=notes
            )
            db.session.add(tx)
            db.session.commit()
            flash(f'تم إيداع مبلغ {amount} ج.م في ({method}) بنجاح!', 'success')
    except Exception as e:
        db.session.rollback()
        flash('حدث خطأ أثناء تسجيل الإيداع.', 'danger')
        
    return redirect(url_for('index'))

@app.route('/treasury/expense', methods=['POST'])
@login_required
def treasury_expense():
    try:
        amount = float(request.form.get('amount', 0))
        notes = request.form.get('notes', 'مصروفات عامة').strip()
        
        if amount > 0:
            tx = TreasuryTransaction(
                amount=-amount,
                method='كاش',
                tx_type='مصروفات',
                entity_id=None,
                notes=notes or 'مصروفات عامة'
            )
            db.session.add(tx)
            db.session.commit()
            flash(f'تم تسجيل مصروفات بقيمة {amount:,.2f} ج.م وخصمها من الخزينة بنجاح!', 'success')
        else:
            flash('يجب إدخال مبلغ أكبر من الصفر.', 'danger')
    except Exception as e:
        db.session.rollback()
        flash('حدث خطأ أثناء تسجيل المصروفات.', 'danger')
        
    return redirect(url_for('index'))

@app.route('/treasury/withdraw_wallet', methods=['POST'])
@login_required
def treasury_withdraw_wallet():
    try:
        amount = float(request.form.get('amount', 0))
        notes = request.form.get('notes', 'سحب من المحفظة').strip()
        
        if amount > 0:
            tx = TreasuryTransaction(
                amount=-amount,
                method='تحويل',
                tx_type='سحب_محفظة',
                entity_id=None,
                notes=notes or 'سحب من المحفظة'
            )
            db.session.add(tx)
            db.session.commit()
            flash(f'تم سحب مبلغ {amount:,.2f} ج.م من المحفظة بنجاح!', 'success')
        else:
            flash('يجب إدخال مبلغ أكبر من الصفر.', 'danger')
    except Exception as e:
        db.session.rollback()
        flash('حدث خطأ أثناء تسجيل السحب من المحفظة.', 'danger')
        
    return redirect(url_for('index'))

@app.route('/treasury/reset_profit', methods=['POST'])
@login_required
def treasury_reset_profit():
    try:
        current_displayed_profit, total_earned, total_reset, _ = get_company_profit_data()
        notes = request.form.get('notes', 'تصفير أرباح الدورة الحالية').strip()
        
        if current_displayed_profit > 0:
            tx = TreasuryTransaction(
                amount=current_displayed_profit,
                method='أرباح',
                tx_type='تصفير_أرباح',
                entity_id=None,
                notes=notes or 'تصفير أرباح الدورة الحالية'
            )
            db.session.add(tx)
            db.session.commit()
            flash(f'تم تصفير الأرباح المحصلة بقيمة ({current_displayed_profit:,.2f} ج.م) بنجاح دون أي مساس برصيد الخزينة!', 'success')
        else:
            flash('الأرباح مصفرة بالفعل.', 'info')
    except Exception as e:
        db.session.rollback()
        flash('حدث خطأ أثناء تصفير الأرباح.', 'danger')
        
    return redirect(url_for('index'))

# --- Backwards Compatibility Routes for old URLs ---

@app.route('/orders')
def orders():
    return redirect(url_for('index', tab='orders'))

@app.route('/scan', methods=['GET'])
def scan_get():
    return redirect(url_for('index', tab='scan'))

@app.route('/upload', methods=['GET'])
def upload_get():
    return redirect(url_for('index'))

@app.route('/order/new', methods=['GET'])
def new_order_get():
    return redirect(url_for('index'))

@app.route('/export_excel/courier/<int:courier_id>', methods=['GET'])
@login_required
def export_courier_excel(courier_id):
    courier = Courier.query.get_or_404(courier_id)
    orders = Order.query.filter(
        Order.courier_id == courier_id,
        Order.courier_settled == False
    ).order_by(Order.id.desc()).all()
    
    data = []
    for o in orders:
        status_display = o.status
        if o.status == 'مع المندوب' and o.courier:
            status_display = f"مع المندوب ({o.courier.name})"
            
        data.append({
            'رقم البوليصة': o.tracking_number,
            'العميل': o.client_name,
            'رقم التليفون': o.phone,
            'المنطقة': o.region,
            'العنوان': o.address,
            'مبلغ التحصيل (COD)': o.cod,
            'الحالة': status_display,
            'الشركة': o.company.name if o.company else '',
            'المحتوى': o.content
        })
        
    df = pd.DataFrame(data)
    
    if not df.empty:
        totals = {
            'رقم البوليصة': 'الإجمالي',
            'العميل': '',
            'رقم التليفون': '',
            'المنطقة': '',
            'العنوان': '',
            'مبلغ التحصيل (COD)': df['مبلغ التحصيل (COD)'].sum(),
            'الحالة': '',
            'الشركة': '',
            'المحتوى': ''
        }
        df = pd.concat([df, pd.DataFrame([totals])], ignore_index=True)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Orders')
        
    output.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d")
    filename = f"courier_{courier.name}_{timestamp}.xlsx"
    
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/accounting/courier', methods=['GET', 'POST'])
def courier_accounting():
    # Fetch all couriers so the user can always find the courier they want
    couriers = Courier.query.all()
    selected_courier_id = request.args.get('courier_id')
    selected_courier = None
    orders = []
    
    total_cod = 0.0
    total_commission = 0.0
    transfers = 0.0
    net_cash = 0.0
    
    if selected_courier_id:
        selected_courier = Courier.query.get(selected_courier_id)
        if selected_courier:
            # Fetch orders that are NOT settled yet.
            orders = Order.query.options(joinedload(Order.company)).filter(
                Order.courier_id == selected_courier.id,
                Order.courier_settled == False,
            ).all()
            
            if request.method == 'POST':
                action = request.form.get('action')
                if action == 'settle':
                    try:
                        transfers = float(request.form.get('transfers', 0))
                    except:
                        transfers = 0.0
                        
                    settled_count = 0
                    batch_collected = 0.0
                    batch_fee = 0.0
                    
                    for order in orders:
                        new_status = request.form.get(f'status_{order.id}')
                        if new_status and new_status != 'مع المندوب' and new_status != 'مؤجل':
                            if new_status == 'مرتجع_مخزن':
                                order.status = 'مخزن'
                                order.courier_id = None
                                order.collected_amount = None
                                order.courier_fee = None
                                order.courier_settled = True
                                settled_count += 1
                            elif new_status in ['تم التوصيل', 'تسليم جزئي / مرتجع', 'مرتجع بشحن']:
                                order.status = 'مرتجع' if new_status == 'مرتجع بشحن' else new_status
                                try:
                                    order.collected_amount = float(request.form.get(f'collected_{order.id}', 0))
                                except:
                                    order.collected_amount = order.cod if new_status == 'تم التوصيل' else (order.shipping_fee if new_status == 'مرتجع بشحن' else 0)
                                try:
                                    order.courier_fee = float(request.form.get(f'courier_fee_{order.id}', 0))
                                except:
                                    order.courier_fee = 0.0
                                order.courier_settled = True
                                settled_count += 1
                                batch_collected += (order.collected_amount or 0.0)
                                batch_fee += (order.courier_fee or 0.0)
                            elif new_status in ['مرتجع', 'مرتجع شركة']:
                                order.status = new_status
                                order.collected_amount = 0.0
                                try:
                                    order.courier_fee = float(request.form.get(f'courier_fee_{order.id}', 0))
                                except:
                                    order.courier_fee = 0.0
                                order.courier_settled = True
                                settled_count += 1
                                batch_fee += (order.courier_fee or 0.0)
                            
                    if settled_count > 0:
                        db.session.commit()
                        
                        net_cash_settled = batch_collected - batch_fee
                        final_cash = net_cash_settled - transfers
                        
                        # Add treasury transactions automatically!
                        if transfers > 0:
                            tx1 = TreasuryTransaction(amount=transfers, method='تحويل', tx_type='تحصيل_من_مندوب', entity_id=selected_courier.id, notes=f'تقفيل شيت مندوب ({settled_count} أوردر)')
                            db.session.add(tx1)
                        if final_cash > 0:
                            tx2 = TreasuryTransaction(amount=final_cash, method='كاش', tx_type='تحصيل_من_مندوب', entity_id=selected_courier.id, notes=f'تقفيل شيت مندوب ({settled_count} أوردر)')
                            db.session.add(tx2)
                        elif final_cash < 0:
                            tx2 = TreasuryTransaction(amount=final_cash, method='كاش', tx_type='صرف_لمندوب', entity_id=selected_courier.id, notes=f'صرف للمندوب عند تقفيل الشيت ({settled_count} أوردر)')
                            db.session.add(tx2)
                            
                        db.session.commit()
                        
                        flash(f'تم تقفيل {settled_count} أوردر بنجاح! وتم إدراج الدفعات للخزينة.', 'success')
                        return redirect(url_for('courier_accounting'))

    return render_template(
        'courier_accounting.html', 
        couriers=couriers, 
        selected_courier=selected_courier, 
        orders=orders,
        total_cod=total_cod,
        total_commission=total_commission,
        transfers=transfers,
        net_cash=net_cash
    )

if __name__ == '__main__':
    app.run(debug=True)
