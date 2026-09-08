import os
import uuid
import re
import pandas as pd
import hashlib
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload
from sqlalchemy import or_, text
from datetime import datetime
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecretkey'
import os
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///shipping.db'
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
    phone = db.Column(db.String(20))

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracking_number = db.Column(db.String(50), unique=True, nullable=False)
    client_name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    address = db.Column(db.String(255))
    region = db.Column(db.String(100))
    cod = db.Column(db.Float, default=0.0)
    shipping_fee = db.Column(db.Float, default=0.0)
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

with app.app_context():

    db.create_all()
    
    # Create default admin if not exists
    if not User.query.filter_by(username='admin').first():
        hashed = generate_password_hash('admin123')
        default_admin = User(username='admin', password_hash=hashed, role='admin')
        db.session.add(default_admin)
        db.session.commit()
    # Safely add columns if they don't exist (SQLite)
    new_columns = [
        'batch_id VARCHAR(50)',
        'courier_id INTEGER',
        'created_at DATETIME',
        'content TEXT',
        'collected_amount FLOAT',
        'courier_fee FLOAT'
    ]
    for col_def in new_columns:
        col_name = col_def.split()[0]
        try:
            db.session.execute(text(f'ALTER TABLE "order" ADD COLUMN {col_def}'))
            db.session.commit()
        except Exception:
            db.session.rollback()
    
    # Add a default courier for testing if none exists
    Order.query.filter_by(status='جديد بالمخزن').update({'status': 'مخزن'})
    Order.query.filter_by(status='قيد التوصيل').update({'status': 'مع المندوب'})
    db.session.commit()

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

@app.route('/')
@login_required
def index():
    # Active tab and scan state
    active_tab = request.args.get('tab', 'dashboard')
    scanned_tracking = request.args.get('scanned', None)
    
    # --- OPTIMIZED QUERIES ---
    # 1. Fetch all Status counts in ONE query
    status_counts = db.session.query(Order.status, db.func.count(Order.id)).group_by(Order.status).all()
    status_dict = dict(status_counts)
    
    total_orders = sum(status_dict.values())
    new_in_warehouse = status_dict.get('مخزن', 0)
    postponed = status_dict.get('مؤجل', 0)
    returned = status_dict.get('مرتجع', 0)
    returned_company = status_dict.get('مرتجع شركة', 0)
    with_courier = status_dict.get('مع المندوب', 0)
    
    # 2. Fetch all Treasury sums in ONE query
    treasury_sums = db.session.query(TreasuryTransaction.method, db.func.sum(TreasuryTransaction.amount)).group_by(TreasuryTransaction.method).all()
    treasury_dict = dict(treasury_sums)
    treasury_cash = treasury_dict.get('كاش', 0.0)
    treasury_transfer = treasury_dict.get('تحويل', 0.0)
    
    active_couriers = Courier.query.count()
    
    # Active Goods & Profit (Need custom filters, so we keep these separate but they are only 3 queries now)
    full_goods = db.session.query(db.func.sum(Order.cod - Order.shipping_fee)).filter(
        ~Order.status.in_(['تم التوصيل', 'تسليم جزئي / مرتجع', 'مرتجع شركة', 'مرتجع بشحن'])
    ).scalar() or 0.0
    
    partial_goods = db.session.query(db.func.sum(Order.cod - Order.collected_amount)).filter(
        Order.status == 'تسليم جزئي / مرتجع'
    ).scalar() or 0.0
    
    total_goods = full_goods + partial_goods
    
    company_profit = db.session.query(
        db.func.sum(Order.shipping_fee - Order.courier_fee)
    ).filter(
        Order.status.in_(['تم التوصيل', 'تسليم جزئي / مرتجع', 'مرتجع بشحن'])
    ).scalar() or 0.0
    
    # Advanced Dashboard Stats
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
    
    # Orders & Search & Filters
    search_query = request.args.get('search', '').strip()
    filter_company = request.args.get('company', '')
    filter_status = request.args.get('status', '')
    filter_courier = request.args.get('courier_id', '')
    filter_region = request.args.get('region', '')
    filter_duplicates = request.args.get('duplicates', '')

    query = Order.query.options(joinedload(Order.company), joinedload(Order.courier))
    
    # Calculate duplicate phones globally
    duplicate_phones_query = db.session.query(Order.phone).group_by(Order.phone).having(db.func.count(Order.id) > 1).all()
    duplicate_phones = set([r[0] for r in duplicate_phones_query if r[0]])
    
    if search_query:
        query = query.filter(or_(
            Order.tracking_number.contains(search_query),
            Order.client_name.contains(search_query),
            Order.phone.contains(search_query)
        ))
        
    if filter_company:
        query = query.filter_by(company_id=filter_company)
        
    if filter_status:
        query = query.filter_by(status=filter_status)
        
    if filter_courier:
        query = query.filter_by(courier_id=filter_courier)
        
    if filter_region:
        query = query.filter_by(region=filter_region)
        
    if filter_duplicates == '1' and duplicate_phones:
        query = query.filter(Order.phone.in_(list(duplicate_phones)))
        
    # Get aggregates efficiently from DB instead of Python loop
    agg = query.with_entities(
        db.func.count(Order.id),
        db.func.sum(Order.cod),
        db.func.sum(Order.shipping_fee)
    ).first()
    
    filtered_orders_count = agg[0] or 0
    filtered_cod = agg[1] or 0.0
    filtered_shipping = agg[2] or 0.0
    filtered_net = filtered_cod - filtered_shipping
    
    # Pagination
    page = request.args.get('page', 1, type=int)
    pagination = query.order_by(Order.id.desc()).paginate(page=page, per_page=100, error_out=False)
    all_orders = pagination.items
    
    if search_query or filter_company or filter_status or filter_courier or filter_region:
        active_tab = 'orders' # Force orders tab if filtering
        
    # Couriers & Companies for Modals (Only companies/couriers with orders)
    couriers = Courier.query.join(Order).distinct().all()
    companies = Company.query.join(Order).distinct().all()
    
    # Active statuses in DB
    db_statuses = [r[0] for r in db.session.query(Order.status).distinct().all()]
    statuses = [s for s in ['مخزن', 'مع المندوب', 'تم التوصيل', 'تسليم جزئي / مرتجع', 'مرتجع', 'مرتجع شركة', 'مرتجع بشحن'] if s in db_statuses]
    
    # Active regions in DB based on current filters (excluding region itself)
    region_query = Order.query.options(joinedload(Order.company), joinedload(Order.courier))
    if search_query:
        region_query = region_query.filter(or_(
            Order.tracking_number.contains(search_query),
            Order.client_name.contains(search_query),
            Order.phone.contains(search_query)
        ))
    if filter_company: region_query = region_query.filter_by(company_id=filter_company)
    if filter_status: region_query = region_query.filter_by(status=filter_status)
    if filter_courier: region_query = region_query.filter_by(courier_id=filter_courier)
    
    regions = [r[0] for r in region_query.with_entities(Order.region).distinct().all() if r[0]]
    
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
            
    # Fetch manual deposit history
    deposit_history = TreasuryTransaction.query.filter_by(
        tx_type='إيداع_يدوي'
    ).order_by(TreasuryTransaction.created_at.desc()).all()
    
    return render_template('index.html', 
                           total_orders=total_orders, 
                           total_goods=total_goods,
                           treasury_cash=treasury_cash,
                           treasury_transfer=treasury_transfer,
                           company_profit=company_profit,
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
                           active_tab=active_tab,
                           scanned_orders=scanned_orders,
                           scanned_query=scanned_tracking,
                           deposit_history=deposit_history,
                           duplicate_phones=duplicate_phones)

@app.route('/export_excel', methods=['GET'])
@login_required
def export_excel():
    search_query = request.args.get('search', '').strip()
    filter_company = request.args.get('company', '')
    filter_status = request.args.get('status', '')
    filter_courier = request.args.get('courier_id', '')
    filter_region = request.args.get('region', '')
    filter_duplicates = request.args.get('duplicates', '')

    query = Order.query.options(joinedload(Order.company), joinedload(Order.courier))
    
    if search_query:
        query = query.filter(or_(
            Order.tracking_number.contains(search_query),
            Order.client_name.contains(search_query),
            Order.phone.contains(search_query)
        ))
    if filter_company:
        query = query.filter_by(company_id=filter_company)
    if filter_status:
        query = query.filter_by(status=filter_status)
    if filter_courier:
        query = query.filter_by(courier_id=filter_courier)
    if filter_region:
        query = query.filter_by(region=filter_region)
    if filter_duplicates == '1':
        duplicate_phones_query = db.session.query(Order.phone).group_by(Order.phone).having(db.func.count(Order.id) > 1).all()
        duplicate_phones = set([r[0] for r in duplicate_phones_query if r[0]])
        if duplicate_phones:
            query = query.filter(Order.phone.in_(list(duplicate_phones)))
            
    orders = query.order_by(Order.id.desc()).all()
    
    data = []
    for o in orders:
        # Use collected_amount if present (partial delivery), otherwise cod
        total_cod = o.collected_amount if o.collected_amount is not None else o.cod
        shipping = o.shipping_fee or 0
        net = total_cod - shipping
        
        data.append({
            'رقم البوليصة': o.tracking_number,
            'العميل': o.client_name,
            'رقم التليفون': o.phone,
            'المنطقة': o.region,
            'العنوان': o.address,
            'الشركة': o.company.name if o.company else '',
            'المندوب': o.courier.name if o.courier else '',
            'الإجمالي': total_cod,
            'الشحن': shipping,
            'الصافي': net,
            'عمولة المندوب': o.courier_fee or 0,
            'الحالة': o.status,
            'التاريخ': o.created_at.strftime('%Y-%m-%d') if o.created_at else ''
        })
        
    df = pd.DataFrame(data)
    
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
    phone = request.form.get('phone', '').strip()
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
        status='مخزن',
        company_id=company_id
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
                    raw_phone = str(get_cell(row, col_map['phone'])).strip()
                    

                    
                    def clean_phone(val):
                        s = str(val).strip()
                        if s.lower() == 'nan' or not s: return ''
                        if s.endswith('.0'): s = s[:-2]
                        s = re.sub(r'[^\d]', '', s)
                        if len(s) == 10 and s.startswith('1'): s = '0' + s
                        return s
                    
                    phone = clean_phone(raw_phone)
                    
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
                    shipping_val = get_cell(row, col_map['shipping'], 0.0)
                    content_val = str(get_cell(row, col_map['content'])).strip()
                    if content_val.lower() == 'nan': content_val = ''
                    def parse_float(val):
                        if pd.isna(val): return 0.0
                        if isinstance(val, (int, float)): return float(val)
                        cleaned = re.sub(r'[^\d.]', '', str(val))
                        try:
                            return float(cleaned) if cleaned else 0.0
                        except ValueError:
                            return 0.0

                    cod = parse_float(cod_val)
                    shipping_fee = parse_float(shipping_val)
                    
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
            flash(f'تم رفع وحفظ {success_count} أوردر بنجاح! تم تعيين بوالص جديدة لهم.', 'success')
            return redirect(url_for('print_batch', batch_id=batch_id))
            
        except Exception as e:
            flash(f'حدث خطأ أثناء قراءة الملف: {str(e)}', 'danger')
            return redirect(url_for('index'))
    else:
        flash('صيغة الملف غير مدعومة، يرجى رفع ملف Excel (.xlsx أو .xls)', 'danger')
        return redirect(url_for('index'))

@app.route('/print/<batch_id>')
def print_batch(batch_id):
    orders_to_print = Order.query.filter_by(batch_id=batch_id).all()
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
        
    orders = Order.query.filter(or_(
        Order.tracking_number == query,
        Order.phone.contains(query)
    )).all()
    
    if not orders:
        return {'error': 'لا يوجد أوردر مطابق'}, 404
        
    result = []
    for o in orders:
        result.append({
            'id': o.id,
            'tracking_number': o.tracking_number,
            'client_name': o.client_name,
            'phone': o.phone,
            'region': o.region,
            'cod': o.cod,
            'status': o.status
        })
    return {'orders': result}

@app.route('/api/order/<int:order_id>/copy', methods=['POST'])
def api_order_copy(order_id):
    order = Order.query.get_or_404(order_id)
    order.is_copied = True
    db.session.commit()
    return {'success': True}

@app.route('/scan', methods=['POST'])
def scan():
    tracking_number = request.form.get('tracking_number', '').strip()
    action = request.form.get('action')
    courier_id = request.form.get('courier_id')
    
    if tracking_number and action:
        order = Order.query.filter_by(tracking_number=tracking_number).first()
        if order:
            if action == 'assign_courier' and courier_id:
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
                order.status = 'مرتجع شركة'
                db.session.commit()
                flash(f'تم تحويل الأوردر {tracking_number} لمرتجع شركة', 'dark')
            elif action == 'return':
                order.status = 'مرتجع'
                db.session.commit()
                flash(f'تم تحويل الأوردر {tracking_number} لمرتجع', 'danger')
            return redirect(url_for('index', tab='scan'))
    
    # If it's just a scan without action (initial scan) or failed action
    return redirect(url_for('index', tab='scan', scanned=tracking_number))

@app.route('/order/<int:order_id>/delete', methods=['POST'])
def delete_order(order_id):
    order = Order.query.get_or_404(order_id)
    db.session.delete(order)
    db.session.commit()
    flash('تم مسح الأوردر بنجاح', 'success')
    return redirect(url_for('index', tab='orders'))

@app.route('/orders/bulk_action', methods=['POST'])
def bulk_action():
    action = request.form.get('action')
    order_ids = request.form.getlist('order_ids')
    courier_name = request.form.get('courier_name', '').strip()
    
    if not order_ids:
        flash('لم يتم تحديد أي أوردر!', 'danger')
        return redirect(url_for('index', tab='orders'))
        
    orders_to_update = Order.query.filter(Order.id.in_(order_ids)).all()
        
    if action == 'delete':
        Order.query.filter(Order.id.in_(order_ids)).delete(synchronize_session=False)
        flash(f'تم مسح {len(order_ids)} أوردر بنجاح', 'success')
    elif action == 'assign_courier' and courier_name:
        # Check if courier exists, otherwise create
        courier = Courier.query.filter_by(name=courier_name).first()
        if not courier:
            courier = Courier(name=courier_name)
            db.session.add(courier)
            db.session.flush() # get ID
            
        for order in orders_to_update:
            order.status = 'مع المندوب'
            order.courier_id = courier.id
        flash(f'تم تسليم {len(order_ids)} أوردر للمندوب ({courier_name}) بنجاح!', 'success')
    elif action == 'warehouse':
        for order in orders_to_update:
            order.status = 'مخزن'
            order.courier_id = None
        flash(f'تم إرجاع {len(order_ids)} أوردر للمخزن', 'info')
    elif action == 'set_region':
        region_name = request.form.get('bulk_region_name')
        if not region_name:
            flash('يجب كتابة اسم المنطقة.', 'danger')
            return redirect(url_for('index', tab='orders'))
        for order in orders_to_update:
            order.region = region_name
        flash(f'تم تعيين المنطقة ({region_name}) لـ {len(order_ids)} أوردر', 'success')
    elif action == 'return_company':
        for order in orders_to_update:
            order.status = 'مرتجع شركة'
        flash(f'تم تحويل {len(order_ids)} أوردر إلى مرتجع شركة', 'dark')
    elif action == 'delivered':
        for order in orders_to_update:
            order.status = 'تم التوصيل'
            # Assuming full delivery collected amount equals cod if it's not set
            if order.collected_amount is None:
                order.collected_amount = order.cod
        flash(f'تم تعيين {len(order_ids)} أوردر كـ (تم التوصيل)', 'success')
    elif action == 'return':
        for order in orders_to_update:
            order.status = 'مرتجع'
        flash(f'تم تحويل {len(order_ids)} أوردر إلى مرتجع', 'danger')
    elif action == 'unmark_copied':
        for order in orders_to_update:
            order.is_copied = False
        flash(f'تم إلغاء علامة النسخ لـ {len(order_ids)} أوردر', 'info')
        
    db.session.commit()
    return redirect(url_for('index', tab='orders'))

@app.route('/order/<int:order_id>/edit', methods=['POST'])
def edit_order(order_id):
    order = Order.query.get_or_404(order_id)
    order.client_name = request.form.get('client_name', order.client_name)
    order.phone = request.form.get('phone', order.phone)
    order.address = request.form.get('address', order.address)
    order.region = request.form.get('region', order.region)
    order.status = request.form.get('status', order.status)
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
        else:
            order.courier_id = None
            
    except ValueError:
        pass
    
    db.session.commit()
    flash('تم تعديل الأوردر بنجاح', 'success')
    return redirect(url_for('index', tab='orders'))

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
            company_debt = db.session.query(
                db.func.sum(Order.collected_amount - Order.shipping_fee)
            ).filter_by(company_id=selected_company.id, company_settled=True).scalar() or 0.0
            
            # 1.5 Manual Debt Added to Company
            manual_debt = db.session.query(
                db.func.sum(TreasuryTransaction.amount)
            ).filter_by(tx_type='إضافة_رصيد_لشركة', entity_id=selected_company.id).scalar() or 0.0
            
            company_debt += manual_debt
            
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
        data.append({
            'رقم البوليصة': o.tracking_number,
            'العميل': o.client_name,
            'رقم التليفون': o.phone,
            'المنطقة': o.region,
            'العنوان': o.address,
            'مبلغ التحصيل (COD)': o.cod,
            'الحالة': o.status,
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
                            order.status = new_status
                            
                            if new_status in ['تم التوصيل', 'تسليم جزئي / مرتجع', 'مرتجع بشحن']:
                                try:
                                    order.collected_amount = float(request.form.get(f'collected_{order.id}', 0))
                                except:
                                    order.collected_amount = order.cod if new_status == 'تم التوصيل' else 0
                                try:
                                    order.courier_fee = float(request.form.get(f'courier_fee_{order.id}', 0))
                                except:
                                    pass
                            elif new_status == 'مرتجع':
                                order.collected_amount = 0
                                order.courier_fee = 0
                            
                            order.courier_settled = True
                            settled_count += 1
                            batch_collected += (order.collected_amount or 0.0)
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
