import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Imports
if 'from flask_login' not in content:
    imports = '''from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
'''
    content = content.replace('from sqlalchemy import or_, text', imports + 'from sqlalchemy import or_, text')

# 2. LoginManager setup
if 'login_manager =' not in content:
    setup = '''
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'يرجى تسجيل الدخول للوصول إلى هذه الصفحة'
login_manager.login_message_category = 'warning'
'''
    content = content.replace('db = SQLAlchemy(app)', 'db = SQLAlchemy(app)' + setup)

# 3. User Model
if 'class User(' not in content:
    user_model = '''
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='admin')
    
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))
'''
    content = content.replace('class Company(db.Model):', user_model + '\nclass Company(db.Model):')

# 4. Default Admin User creation
if 'User(username=' not in content:
    admin_setup = '''
    if not User.query.filter_by(username='admin').first():
        hashed = generate_password_hash('admin123')
        default_admin = User(username='admin', password_hash=hashed, role='admin')
        db.session.add(default_admin)
        db.session.commit()
'''
    content = content.replace('db.create_all()', 'db.create_all()' + admin_setup)

# 5. Auth Routes
if '@app.route(\'/login\')' not in content:
    auth_routes = '''
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
'''
    content = content.replace('def index():', auth_routes + '\n@app.route(\'/\')\ndef index():')

# 6. Add @login_required to core routes
routes_to_protect = [
    'def index():',
    'def bulk_action():',
    'def scan_post():',
    'def upload_post():',
    'def new_order():',
    'def api_reset_db():',
    'def print_orders():',
    'def delete_order',
    'def api_order',
    'def edit_order',
    'def courier_accounting():',
    'def courier_pay',
    'def company_accounting(',
    'def company_pay('
]
for route in routes_to_protect:
    func_name = route.split('def ')[1]
    if f'@login_required\ndef {func_name}' not in content:
        content = content.replace(route, f'@login_required\ndef {func_name}')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)
