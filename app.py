import os
import logging
import random
import secrets
import string
import base64
import time
import hashlib
import json
import re
import io
import zipfile
from datetime import datetime, timedelta
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, render_template_string, make_response, request, redirect, url_for, flash, session, jsonify, abort, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFProtect
from flask_talisman import Talisman
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import inspect, text, func
import xlsxwriter
from pywebpush import webpush, WebPushException
from flask import Flask, render_template, render_template_string, make_response, request, redirect, url_for, flash, session, jsonify, abort, send_from_directory, send_file

# ============================================================
#                    CONFIGURATION
# ============================================================

load_dotenv()
app = Flask(__name__)

import os

# Flask SECRET_KEY ko Environment Variable se load karein
app.config['SECRET_KEY'] = os.environ.get("FLASK_SECRET_KEY")

# Production safety check
if not app.config['SECRET_KEY']:
    raise ValueError("CRITICAL ERROR: FLASK_SECRET_KEY Environment Variable set nahi hai!")


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

is_production = os.environ.get('FLASK_ENV') == 'production'

# Session with retries
smm_session = requests.Session()
retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[502, 503, 504])
adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=retries)
smm_session.mount("https://", adapter)
smm_session.mount("http://", adapter)

# CSP Policy
csp_policy = {
    'default-src': '\'self\'',
    'script-src': ['\'self\'', '\'unsafe-inline\'', 'https://cdn.tailwindcss.com', 'https://cdn.jsdelivr.net', 'https://code.jquery.com', 'https://cdnjs.cloudflare.com', 'https://maxcdn.bootstrapcdn.com', 'https://kit.fontawesome.com'],
    'style-src': ['\'self\'', '\'unsafe-inline\'', 'https://cdn.tailwindcss.com', 'https://cdnjs.cloudflare.com', 'https://fonts.googleapis.com', 'https://cdn.jsdelivr.net', 'https://maxcdn.bootstrapcdn.com', 'https://use.fontawesome.com'],
    'font-src': ['\'self\'', 'data:', 'https://fonts.gstatic.com', 'https://cdnjs.cloudflare.com', 'https://cdn.jsdelivr.net', 'https://maxcdn.bootstrapcdn.com', 'https://ka-f.fontawesome.com'],
    'img-src': ['\'self\'', 'data:', 'https:'],
    'connect-src': ['\'self\'', 'https://ka-f.fontawesome.com'],
    'object-src': '\'none\'',
    'frame-ancestors': '\'none\''
}

# Talisman config ko force HTTPS par fix karein
Talisman(
    app,
    content_security_policy=csp_policy,
    force_https=True, # Production check ke bajaye hamesha force karein
    strict_transport_security=True,
    session_cookie_secure=True
)


csrf = CSRFProtect(app)


basedir = os.path.abspath(os.path.dirname(__file__))

MYSQL_USER = os.environ.get('MYSQL_USER')
MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD')
MYSQL_HOST = os.environ.get('MYSQL_HOST')
MYSQL_PORT = os.environ.get('MYSQL_PORT', '13461')
MYSQL_DB = os.environ.get('MYSQL_DB')

if not all([MYSQL_USER, MYSQL_PASSWORD, MYSQL_HOST, MYSQL_DB]):
    raise ValueError("CRITICAL ERROR: Database environment variables missing!")


# URI without ssl_mode parameter in query string
app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"

# PyMySQL setup: SSL parameters provided via engine connect options
engine_options = {
    'pool_recycle': 280,
    'pool_pre_ping': True
}

# Only pass SSL dictionary when connecting to remote servers (like Aiven), skip for local GitHub Runner (127.0.0.1)
if MYSQL_HOST != '127.0.0.1':
    engine_options['connect_args'] = {
        'ssl': {'ssl_mode': 'REQUIRED'}
    }

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)



import os

# Secrets se API Key aur URL load karein
EXCHANGE_API_KEY = os.environ.get("EXCHANGE_API_KEY")

# URL dynamic key ke sath GitHub Secrets se banega
if EXCHANGE_API_KEY:
    EXCHANGE_URL = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/latest/USD"
else:
    EXCHANGE_URL = os.environ.get("EXCHANGE_URL")

# Production safety check
if not EXCHANGE_API_KEY:
    raise ValueError("CRITICAL ERROR: EXCHANGE_API_KEY Environment Variable set nahi hai!")


app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

import os

# Sirf Environment Variables / Secrets se load hoga
API_KEY = os.environ.get("GODOFPANEL_API_KEY")
API_URL = os.environ.get("GODOFPANEL_API_URL")

# Production safety check (Agar secret set nahi hoga toh server crash/warn kar dega)
if not API_KEY or not API_URL:
    raise ValueError("CRITICAL ERROR: GODOFPANEL_API_KEY ya GODOFPANEL_API_URL Environment Variable set nahi hai!")


@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()

# ============================================================
#                    RATE LIMITING
# ============================================================

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri="memory://",
    strategy="fixed-window",
)

# ============================================================
#                    SERVICES CACHE (NO HUEY)
# ============================================================

SERVICES_FILE = os.path.join(basedir, 'services_cache.json')
SERVICES_CACHE = {'data': None, 'timestamp': 0}

def fetch_and_cache_services():
    """Fetch services directly from GodOfPanel API."""
    try:
        app.logger.info("Syncing services from provider API...")
        response = requests.post(API_URL, data={'key': API_KEY, 'action': 'services'}, timeout=20)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                cache_bundle = {'timestamp': time.time(), 'data': data}
                with open(SERVICES_FILE, 'w') as f:
                    json.dump(cache_bundle, f)
                SERVICES_CACHE['data'] = data
                SERVICES_CACHE['timestamp'] = cache_bundle['timestamp']
                return True
    except Exception as e:
        app.logger.error(f"Sync Logic Failed: {e}")
    return False

def get_cached_services_safe():
    """Retrieves services from cache or fetches fresh."""
    now = time.time()
    
    if SERVICES_CACHE['data'] and (now - SERVICES_CACHE['timestamp'] < 86400):
        return SERVICES_CACHE['data']

    if os.path.exists(SERVICES_FILE):
        try:
            with open(SERVICES_FILE, 'r') as f:
                bundle = json.load(f)
                SERVICES_CACHE['data'] = bundle['data']
                SERVICES_CACHE['timestamp'] = bundle['timestamp']
                return bundle['data']
        except Exception:
            pass

    app.logger.warning("Cache empty. Performing emergency sync.")
    fetch_and_cache_services()
    return SERVICES_CACHE['data']

# ============================================================
#                    DIRECT API FUNCTIONS
# ============================================================

def submit_order_direct(service_id, link, quantity, is_drip=False, runs=None, interval=None):
    """
    Submits order directly to GodOfPanel API - NO HUEY, NO BRIDGE.
    Returns: (success, result, error_message)
    """
    try:
        payload = {
            'key': API_KEY,
            'action': 'add',
            'service': service_id,
            'link': link,
            'quantity': quantity
        }
        
        if is_drip:
            payload.update({'runs': runs, 'interval': interval})
        
        response = smm_session.post(API_URL, data=payload, timeout=30)
        result = response.json()
        
        if 'order' in result:
            return True, result, None
        else:
            return False, None, result.get('error', 'Unknown Provider Error')
            
    except requests.exceptions.Timeout:
        return False, None, "Connection timeout. Please try again."
    except requests.exceptions.ConnectionError:
        return False, None, "Network error. Please check your connection."
    except Exception as e:
        return False, None, str(e)

def get_order_status_direct(api_order_id):
    """Fetches order status directly from GodOfPanel API."""
    try:
        payload = {
            'key': API_KEY,
            'action': 'status',
            'order': api_order_id
        }
        response = smm_session.post(API_URL, data=payload, timeout=10)
        result = response.json()
        
        if 'status' in result:
            return True, result['status'], None
        elif 'error' in result:
            return False, None, result['error']
        else:
            return False, None, "Invalid response from provider"
            
    except Exception as e:
        return False, None, str(e)

def get_provider_balance_direct():
    """Fetches provider balance directly from GodOfPanel API."""
    try:
        payload = {'key': API_KEY, 'action': 'balance'}
        response = smm_session.post(API_URL, data=payload, timeout=5)
        result = response.json()
        if 'balance' in result:
            return float(result['balance'])
        return None
    except Exception as e:
        app.logger.error(f"Balance fetch error: {e}")
        return None

# ============================================================
#                    MODELS
# ============================================================

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.Integer, unique=True, nullable=True, index=True)
    name = db.Column(db.String(100))
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    is_admin = db.Column(db.Boolean, default=False)
    referred_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    referral_code = db.Column(db.String(10), unique=True, nullable=True, index=True)
    preferred_currency = db.Column(db.String(3), default='PKR')
    new_f = db.Column(db.Boolean, default=False)
    device_fingerprint = db.Column(db.String(255), nullable=True, index=True)
    sub_plan = db.Column(db.String(20), default='none')
    sub_expiry = db.Column(db.DateTime, nullable=True)
    is_sub_active = db.Column(db.Boolean, default=False)
    last_checkin = db.Column(db.DateTime, nullable=True)
    streak_count = db.Column(db.Integer, default=0)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    service_id = db.Column(db.String(50))
    link = db.Column(db.String(500))
    quantity = db.Column(db.Integer)
    cost = db.Column(db.Float)
    status = db.Column(db.String(20), default='Pending')
    api_order_id = db.Column(db.String(100), nullable=True)
    api_response = db.Column(db.Text)
    is_refill_supported = db.Column(db.Boolean, default=False)
    refill_status = db.Column(db.String(20), default='None')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    amount = db.Column(db.Float)
    type = db.Column(db.String(50))
    details = db.Column(db.String(200), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class DepositRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    sender_account = db.Column(db.String(100), nullable=False)
    sender_name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    transaction_id = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='pending')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='deposit_requests')

class FreeTrialLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    link = db.Column(db.String(500), unique=True, nullable=False)
    device_fingerprint = db.Column(db.String(255), nullable=True, index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class ExchangeRate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    base_currency = db.Column(db.String(3), default='USD')
    target_currency = db.Column(db.String(3), unique=True, nullable=False)
    rate = db.Column(db.Float, nullable=False)
    source = db.Column(db.String(20), default='api')
    is_locked = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SystemSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True)
    value = db.Column(db.Boolean, default=True)

class PushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subscription_json = db.Column(db.Text, nullable=False)

# ============================================================
#                    HELPER FUNCTIONS
# ============================================================

@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except (TypeError, ValueError):
        return None

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

def sync_exchange_rates():
    try:
        response = requests.get(EXCHANGE_URL, timeout=10)
        data = response.json()
        if data.get('result') == 'success':
            rates = data.get('conversion_rates', {})
            for curr in ['PKR', 'INR', 'USD']:
                if curr in rates:
                    record = ExchangeRate.query.filter_by(target_currency=curr).first()
                    if record:
                        if not record.is_locked:
                            record.rate = rates[curr]
                            record.source = 'api'
                    else:
                        new_rate = ExchangeRate(target_currency=curr, rate=rates[curr], source='api')
                        db.session.add(new_rate)
            db.session.commit()
            return True
    except Exception as e:
        app.logger.error(f"Exchange API Failure: {e}")
    return False

def get_rate(currency_code):
    rate_rec = ExchangeRate.query.filter_by(target_currency=currency_code).first()
    if rate_rec:
        return rate_rec.rate
    fallbacks = {'PKR': 300.0, 'INR': 83.0, 'USD': 1.0}
    return fallbacks.get(currency_code, 1.0)

def clean_category_name(name):
    name = re.sub(r'\[.*?\]|\(.*?\)', '', name)
    jargon = ['fast', 'instant', 'non drop', 'nondrop', 's1', 's2', 's3', 'hq', 'real', 'best', 'working']
    words = name.lower().split()
    cleaned_words = [w for w in words if w not in jargon]
    result = " ".join(cleaned_words).title()
    return " ".join(result.split()[:4])

def get_subscription_multiplier(user, current_base_markup=1.5):
    if not user.is_sub_active or not user.sub_expiry:
        return 1.0
    if datetime.utcnow() > user.sub_expiry:
        user.is_sub_active = False
        db.session.commit()
        return 1.0
    if user.sub_plan == 'basic':
        return 1.3 / current_base_markup
    if user.sub_plan in ['pro', 'premium']:
        return 1.2 / current_base_markup
    return 1.0

def generate_referral_code():
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(6))

def generate_six_digit_uid():
    while True:
        new_uid = random.randint(100000, 999999)
        if not User.query.filter_by(uid=new_uid).first():
            return new_uid

# ============================================================
#                    CAPTCHA
# ============================================================

def generate_captcha_data():
    chars = string.ascii_letters + string.digits
    chars = chars.replace('0', '').replace('O', '').replace('I', '').replace('l', '')
    captcha_text = ''.join(secrets.choice(chars) for _ in range(4))
    
    svg = f"""
    <svg width="120" height="45" xmlns="http://www.w3.org/2000/svg">
        <rect width="100%" height="100%" fill="#1a1a1a"/>
        <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" 
              font-family="Arial, sans-serif" font-weight="bold" font-size="24" fill="#ffc107" 
              letter-spacing="5">{captcha_text}</text>
        <line x1="0" y1="20" x2="120" y2="30" stroke="#ffc107" stroke-width="1" opacity="0.3"/>
        <line x1="10" y1="40" x2="110" y2="5" stroke="#ffc107" stroke-width="1" opacity="0.3"/>
    </svg>
    """
    b64_svg = base64.b64encode(svg.encode()).decode()
    session['captcha_text'] = captcha_text
    return b64_svg

@app.route('/get_captcha')
def get_captcha():
    return jsonify({'img': generate_captcha_data()})

def validate_captcha():
    now = time.time()
    last_attempt = session.get('last_cap_time', 0)
    if now - last_attempt < 1:
        return False, "Too many attempts. Please slow down."
    session['last_cap_time'] = now

    user_input = request.form.get('captcha_input', '')
    actual = session.get('captcha_text', '')
    session.pop('captcha_text', None)
    
    if not actual or user_input.lower() != actual.lower():
        return False, "Invalid captcha. Please try again."
    return True, ""

# ============================================================
#                    ROUTES
# ============================================================

@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('home.html')

# ==================== AUTH ====================

@app.route('/register', methods=['GET', 'POST'])
def register():
    ref_code = request.args.get('ref')
    if ref_code:
        session['ref'] = ref_code

    if request.method == 'POST':
        is_valid, error_msg = validate_captcha()
        if not is_valid:
            flash(error_msg, 'danger')
            return redirect(url_for('register'))

        name = request.form.get('name')
        username = request.form.get('username')
        email = request.form.get('email', '').strip().lower()
        password_raw = request.form.get('password')

        if not name or not username or not email or not password_raw:
            flash('All fields are required.', 'danger')
            return redirect(url_for('register'))
        
        user_agent = request.headers.get('User-Agent', 'unknown')
        accept_lang = request.headers.get('Accept-Language', 'unknown')
        fingerprint_raw = f"{request.remote_addr}|{user_agent}|{accept_lang}"
        device_hash = hashlib.sha256(fingerprint_raw.encode()).hexdigest()

        device_account_count = User.query.filter_by(device_fingerprint=device_hash).count()
        if device_account_count >= 2:
            flash("Registration limit reached for this device. Maximum: 2 accounts.", "danger")
            return redirect(url_for('register'))

        password_hash = generate_password_hash(password_raw, method='scrypt')

        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'danger')
            return redirect(url_for('register'))

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))

        referred_by = None
        if 'ref' in session:
            ref_user = User.query.filter_by(referral_code=session['ref']).first()
            if ref_user:
                referred_by = ref_user.id

        user = User(
            uid=generate_six_digit_uid(),
            name=name,
            username=username,
            email=email,
            password=password_hash,
            referred_by=referred_by,
            referral_code=generate_referral_code(),
            preferred_currency='PKR',
            device_fingerprint=device_hash
        )
        
        try:
            db.session.add(user)
            db.session.commit()
            session.pop('ref', None)
            login_user(user, remember=True)
            flash('Registration successful.', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Registration Error: {str(e)}")
            flash('Internal error during registration.', 'danger')

    return render_template('register.html', captcha_img=generate_captcha_data())

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('new_order'))
        
    if request.method == 'POST':
        is_valid, error_msg = validate_captcha()
        if not is_valid:
            flash(error_msg, 'danger')
            return redirect(url_for('login'))

        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            session.clear()
            login_user(user, remember=True)
            return redirect(url_for('dashboard'))
        else:
            if not user:
                time.sleep(0.1)
            flash('Invalid credentials', 'danger')
            
    return render_template('login.html', captcha_img=generate_captcha_data())

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

# ==================== DASHBOARD ====================

@app.route('/dashboard')
@login_required
def dashboard():
    transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.id.desc()).all()
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.id.desc()).all()
    
    user_rate = get_rate(current_user.preferred_currency)
    pkr_to_usd = 1.0 / get_rate('PKR')
    display_balance = (current_user.balance * pkr_to_usd) * user_rate

    return render_template('dashboard.html',
                         user=current_user,
                         transactions=transactions,
                         orders=orders,
                         display_balance=round(display_balance, 2),
                         currency=current_user.preferred_currency)

# ==================== WALLET ====================

@app.route('/wallet', methods=['GET', 'POST'])
@login_required
def wallet():
    if request.method == 'POST':
        try:
            sender_account = request.form.get('sender_account', '').strip()
            sender_name = request.form.get('sender_name', '').strip()
            transaction_id = request.form.get('transaction_id', '').strip()
            amount_str = request.form.get('amount', '0')
            
            if not all([sender_account, sender_name, transaction_id, amount_str]):
                flash("All fields are required.", "danger")
                return redirect(url_for('wallet'))
            
            try:
                amount = float(amount_str)
            except ValueError:
                flash("Invalid amount format.", "danger")
                return redirect(url_for('wallet'))
            
            if amount <= 0:
                flash("Deposit amount must be greater than zero.", "danger")
                return redirect(url_for('wallet'))
            
            deposit = DepositRequest(
                user_id=current_user.id,
                sender_account=sender_account,
                sender_name=sender_name,
                amount=amount,
                transaction_id=transaction_id,
                status='pending'
            )
            db.session.add(deposit)
            db.session.commit()
            flash("Deposit request submitted successfully.", "success")
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Deposit Error: {str(e)}")
            flash("An internal error occurred.", "danger")
        return redirect(url_for('wallet'))
    return render_template('wallet.html', balance=current_user.balance)

# ==================== NEW ORDER (DIRECT API) ====================

@app.route('/das', methods=['GET', 'POST'])
@login_required
def new_order():
    usd_to_pkr = get_rate('PKR')
    user_curr = current_user.preferred_currency
    user_rate = get_rate(user_curr)
    
    if request.method == 'POST':
        ordering_status = SystemSetting.query.filter_by(key='is_ordering_enabled').first()
        if ordering_status and not ordering_status.value and not current_user.is_admin:
            flash("⚠️ System maintenance: Ordering is temporarily paused. Please try again in a few minutes.", "warning")
            return redirect(url_for('new_order'))

        now = time.time()
        order_attempts = session.get('order_attempts', [])
        order_attempts = [t for t in order_attempts if now - t < 300]
        if len(order_attempts) >= 10:
            flash("Too many attempts. Please wait.", "danger")
            return redirect(url_for('new_order'))
        order_attempts.append(now)
        session['order_attempts'] = order_attempts

        is_valid, error_msg = validate_captcha()
        if not is_valid:
            flash(error_msg, 'danger')
            return redirect(url_for('new_order'))

        try:
            submitted_service_id = request.form.get('service')
            link = request.form.get('link', '').strip()
            quantity_per_run = int(request.form.get('quantity', 0))
            is_drip = request.form.get('is_drip_feed') == 'on'
            runs = int(request.form.get('runs', 0)) if is_drip else 0
            interval = int(request.form.get('interval', 0)) if is_drip else 0
            is_lite_mode = request.form.get('is_lite_mode') == 'true'

            services = get_cached_services_safe()
            final_service_id = submitted_service_id
            service_info = next((s for s in services if str(s['service']) == str(final_service_id)), None)
            
            is_freetrial_logic_mode = False

            if is_lite_mode and "tiktok.com" in link.lower() and service_info:
                s_name = service_info.get('name', '').lower()
                s_cat = service_info.get('category', '').lower()
                
                if 'follower' in s_name or 'follower' in s_cat:
                    found_special_service = None
                    for s in services:
                        ft_name = s.get('name', '').lower()
                        ft_cat = s.get('category', '').lower()
                        
                        if 'tiktok' in ft_cat and 'follower' in ft_name and ('hq' in ft_name or 'refill' in ft_name):
                            rate_usd = float(s.get('rate', 0))
                            pkr_price_check = (rate_usd * usd_to_pkr) * 1.4
                            if 180 <= pkr_price_check <= 320:
                                found_special_service = s
                                break
                    
                    if found_special_service:
                        final_service_id = found_special_service['service']
                        service_info = found_special_service
                        is_freetrial_logic_mode = True
            
            if not service_info:
                flash("Invalid service selected.", "danger")
                return redirect(url_for('new_order'))

            if not is_lite_mode and not current_user.is_sub_active:
                s_name_lower = service_info['name'].lower()
                is_restricted = False
                if 'lifetime' in s_name_lower or 'never drop' in s_name_lower or 'non drop' in s_name_lower:
                    is_restricted = True
                if not is_restricted:
                    matches = re.findall(r'(?:refill|r)\s*(\d+)|(\d+)\s*(?:d|day)', s_name_lower)
                    for m in matches:
                        days_str = next((x for x in m if x), None)
                        if days_str and int(days_str) >= 90:
                            is_restricted = True
                            break
                if is_restricted:
                    flash("🔒 This Premium Service (90+ Days Refill) is locked for non-subscribers.", "warning")
                    return redirect(url_for('subscribe'))

            total_quantity = quantity_per_run
            if is_drip:
                if str(service_info.get('dripfeed')) != '1':
                    flash("Drip-Feed not supported for this service.", "danger")
                    return redirect(url_for('new_order'))
                total_quantity = quantity_per_run * runs
                
            cost_usd = (float(service_info['rate']) * total_quantity / 1000)
            base_cost_pkr = cost_usd * usd_to_pkr
            markup = 1.50 if base_cost_pkr < 300.0 else 1.40
            sub_multiplier = get_subscription_multiplier(current_user, markup)
            price_pkr = round((base_cost_pkr * markup) * sub_multiplier, 2)
            
            user = User.query.filter_by(id=current_user.id).with_for_update().first()
            
            if user.balance < price_pkr:
                db.session.rollback()
                flash(f"Insufficient balance. Need {price_pkr} PKR", "danger")
                return redirect(url_for('wallet'))
                
            user.balance -= price_pkr
            db.session.add(Transaction(user_id=user.id, amount=-price_pkr, type='order_hold'))
            
            # ============================================================
            #                    DIRECT API CALL - NO HUEY
            # ============================================================
            success, api_result, error = submit_order_direct(
                final_service_id, 
                link, 
                total_quantity, 
                is_drip, 
                runs, 
                interval
            )
            
            if success and api_result and 'order' in api_result:
                # Get initial status
                status_success, status_val, status_error = get_order_status_direct(api_result['order'])
                
                new_order_rec = Order(
                    user_id=current_user.id,
                    service_id=final_service_id,
                    link=link,
                    quantity=total_quantity,
                    cost=price_pkr,
                    status=status_val if status_success else 'Processing',
                    api_order_id=str(api_result['order']),
                    api_response=str(api_result)
                )
                db.session.add(new_order_rec)
                db.session.commit()
                
                flash(f"✅ Order #{api_result['order']} placed successfully!", "success")
            else:
                # Refund user
                user.balance += price_pkr
                db.session.rollback()
                flash(f"❌ Order failed: {error}", "danger")
                return redirect(url_for('new_order'))

            return redirect(url_for('status'))

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Critical Order Error: {str(e)}")
            flash("Internal processing error.", "danger")
            return redirect(url_for('new_order'))

    # GET Request Logic
    try:
        raw_services = get_cached_services_safe()

        emoji_map = {'tiktok': '👍', 'facebook': '💙', 'youtube': '🎬', 'instagram': '📸', 'twitter': '🐦', 'x': '🐦', 'telegram': '✈️', 'spotify': '🎵', 'snapchat': '👻', 'whatsapp': '💬', 'threads': '🧵', 'discord': '👾'}
        processed_categories = {}
        platforms = set()
        cheap_keywords = ['cheap', 'budget', 'low', 'economy', 'free', 'bot']
        
        for s in raw_services:
            raw_cat = s.get("category", "Other")
            
            if '[VIP]' in raw_cat:
                if not current_user.is_sub_active or current_user.sub_plan not in ['pro', 'premium']:
                    continue
            
            original_rate_usd = float(s.get("rate", 0))
            base_pkr_reference = original_rate_usd * usd_to_pkr
            markup = 1.50 if base_pkr_reference < 300.0 else 1.40
            
            sub_multiplier = get_subscription_multiplier(current_user, markup)
            display_rate = round((original_rate_usd * user_rate * markup) * sub_multiplier, 2)
            
            s_display = s.copy()
            s_display['rate'] = display_rate
            
            platform_name = raw_cat.split()[0].strip()
            platforms.add(platform_name)
            cat_lower = raw_cat.lower()
            is_cheap = any(word in cat_lower for word in cheap_keywords)
            base_display_name = clean_category_name(raw_cat)
            p_lower = platform_name.lower()
            emoji = emoji_map.get(p_lower, '✨')
            styled_name = f"{emoji} {base_display_name}"
            
            if raw_cat not in processed_categories:
                processed_categories[raw_cat] = {'display_name': styled_name, 'tier': 'Cheap' if is_cheap else 'Standard', 'platform': platform_name, 'min_price': display_rate, 'services': []}
            processed_categories[raw_cat]['services'].append(s_display)
            
            if display_rate < processed_categories[raw_cat]['min_price']:
                processed_categories[raw_cat]['min_price'] = display_rate
                
        base_groups = {}
        for cat_key, data in processed_categories.items():
            base = data['display_name']
            if base not in base_groups:
                base_groups[base] = []
            base_groups[base].append(cat_key)
            
        tier_suffixes = ["", "⭐ Pro", "🚀 Pro Max", "🔥 Ultra", "💎 Ultra Pro", "👑 Ultra Pro Max"]
        for base, cat_keys in base_groups.items():
            if len(cat_keys) > 1:
                sorted_keys = sorted(cat_keys, key=lambda k: processed_categories[k]['min_price'])
                for i, k in enumerate(sorted_keys):
                    suffix = tier_suffixes[i] if i < len(tier_suffixes) else tier_suffixes[-1]
                    processed_categories[k]['display_name'] += f" {suffix}".strip()
                    
        for cat in processed_categories:
            processed_categories[cat]['services'].sort(key=lambda x: x['rate'])
            
        sorted_categories = dict(sorted(processed_categories.items(), key=lambda item: item[1]['min_price']))
        return render_template("new_order.html",
                             grouped_services=sorted_categories,
                             platforms=sorted(list(platforms)),
                             captcha_img=generate_captcha_data(),
                             currency_symbol=user_curr)
    except Exception as e:
        app.logger.error(f"Service Fetch Error: {e}")
        return render_template("errors/api_down.html"), 503

# ==================== ORDER STATUS (DIRECT API) ====================

@app.route('/status', methods=['GET', 'POST'])
@login_required
def status():
    status_data = None
    search_query = None
    
    if request.method == 'POST':
        order_id = request.form.get('order_id')
        search_query = order_id
        order = Order.query.filter_by(id=order_id, user_id=current_user.id).first()
        if order:
            # Fetch latest status from API if api_order_id exists
            if order.api_order_id:
                success, status_val, error = get_order_status_direct(order.api_order_id)
                if success and status_val:
                    order.status = status_val
                    db.session.commit()
            
            status_data = [{
                "id": order.id,
                "service": order.service_id,
                "link": order.link,
                "quantity": order.quantity,
                "status": order.status,
                "date": order.timestamp.strftime('%Y-%m-%d %H:%M'),
                "cost": order.cost
            }]
        else:
            flash("Order not found.", "danger")
    else:
        user_orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.timestamp.desc()).all()
        status_data = []
        for o in user_orders:
            # Refresh status for pending orders
            if o.api_order_id and o.status not in ['Completed', 'Failed', 'Canceled']:
                success, status_val, error = get_order_status_direct(o.api_order_id)
                if success and status_val and status_val != o.status:
                    o.status = status_val
                    db.session.commit()
            
            status_data.append({
                "id": o.id,
                "service": o.service_id,
                "link": o.link,
                "quantity": o.quantity,
                "status": o.status,
                "date": o.timestamp.strftime('%Y-%m-%d %H:%M'),
                "cost": o.cost
            })
    return render_template('status.html', orders=status_data, search_query=search_query)

# ==================== FREE TRIAL (DIRECT API) ====================

@app.route('/freetrial', methods=['GET', 'POST'])
@login_required
def freetrial():
    user_agent = request.headers.get('User-Agent', 'unknown')
    accept_lang = request.headers.get('Accept-Language', 'unknown')
    fingerprint_raw = f"{request.remote_addr}|{user_agent}|{accept_lang}"
    device_hash = hashlib.sha256(fingerprint_raw.encode()).hexdigest()

    if current_user.new_f and not current_user.is_admin:
        return render_template('freetrial.html', used=True)

    if request.method == 'POST':
        is_valid, error_msg = validate_captcha()
        if not is_valid:
            flash(error_msg, 'danger')
            return redirect(url_for('freetrial'))

        if not current_user.is_admin:
            device_exists = FreeTrialLink.query.filter_by(device_fingerprint=device_hash).first()
            if device_exists:
                flash("This device has already claimed a free trial. Limit: 1 per device.", "danger")
                return redirect(url_for('freetrial'))

        tiktok_link = request.form.get('link', '').strip()
        if not tiktok_link or "tiktok.com" not in tiktok_link.lower():
            flash("Please provide a valid TikTok profile link.", "danger")
            return redirect(url_for('freetrial'))

        if not current_user.is_admin:
            existing_link = FreeTrialLink.query.filter_by(link=tiktok_link).first()
            if existing_link:
                flash("This profile link has already been used.", "warning")
                return redirect(url_for('freetrial'))

        try:
            services = get_cached_services_safe()
            usd_to_pkr = get_rate('PKR')
            selected_service = None
            
            for s in services:
                name = s.get('name', '').lower()
                cat = s.get('category', '').lower()
                if 'tiktok' in cat and 'follower' in name and ('hq' in name or 'refill' in name):
                    rate_usd = float(s.get('rate', 0))
                    pkr_price_1k = (rate_usd * usd_to_pkr) * 1.4
                    if 180 <= pkr_price_1k <= 320:
                        selected_service = s
                        break
            
            if not selected_service:
                flash("Free trial service is currently unavailable. Try again later.", "warning")
                return redirect(url_for('freetrial'))

            # ============================================================
            #                    DIRECT API CALL - NO HUEY
            # ============================================================
            success, api_result, error = submit_order_direct(
                selected_service['service'],
                tiktok_link,
                100,
                False
            )
            
            if success and api_result and 'order' in api_result:
                new_trial_order = Order(
                    user_id=current_user.id,
                    service_id=selected_service['service'],
                    link=tiktok_link,
                    quantity=100,
                    cost=0.0,
                    status='Completed',
                    api_order_id=str(api_result['order']),
                    api_response=str(api_result)
                )
                db.session.add(new_trial_order)
                
                if not current_user.is_admin:
                    current_user.new_f = True
                    db.session.add(FreeTrialLink(link=tiktok_link, device_fingerprint=device_hash))
                
                db.session.commit()
                flash("🎉 Free Trial Success! 100 Followers have been added!", "success")
            else:
                flash(f"❌ Free Trial failed: {error}", "danger")
                return redirect(url_for('freetrial'))

            return redirect(url_for('dashboard'))

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Free Trial Route Error: {e}")
            flash("Internal error. Please contact support.", "danger")
            return redirect(url_for('freetrial'))

    return render_template('freetrial.html', used=False, captcha_img=generate_captcha_data())

# ==================== STREAK (DIRECT API) ====================

@app.route('/streak', methods=['GET'])
@login_required
def streak_page():
    now = datetime.utcnow()
    can_claim = True
    if current_user.last_checkin and current_user.last_checkin.date() == now.date():
        can_claim = False
    return render_template('streak.html', can_claim=can_claim, now=now)

@app.route('/streak', methods=['POST'])
@login_required
def claim_daily():
    user = User.query.filter_by(id=current_user.id).with_for_update().first()
    now = datetime.utcnow()
    link = request.form.get('tiktok_link')

    if not link or "tiktok.com" not in link.lower():
        flash("Valid TikTok link required.", "danger")
        return redirect(url_for('dashboard'))

    if user.last_checkin and user.last_checkin.date() == now.date():
        flash("Already claimed today!", "info")
        return redirect(url_for('dashboard'))

    if user.last_checkin and (now - user.last_checkin).days > 1:
        user.streak_count = 0

    user.streak_count = (user.streak_count % 7) + 1
    
    if user.streak_count == 7:
        user.sub_plan = 'pro'
        user.is_sub_active = True
        user.sub_expiry = now + timedelta(days=3)
        flash("👑 KING MOVE: 7-Day Streak reached! You've been upgraded to PRO for 3 days.", "success")
    
    rewards = {1: 1000, 2: 1200, 3: 1500, 4: 1800, 5: 2000, 6: 2500, 7: 5000}
    base_views = rewards[user.streak_count]
    multiplier = 1.012 if user.is_sub_active else 1.0
    final_views = int(base_views * multiplier)

    try:
        services = get_cached_services_safe()
        
        view_services = [
            s for s in services
            if 'tiktok' in s['category'].lower()
            and 'view' in s['name'].lower()
        ]
        
        if not view_services:
            flash("View service temporarily unavailable. Please try again later.", "danger")
            return redirect(url_for('dashboard'))

        cheapest_view = sorted(view_services, key=lambda x: float(x.get('rate', 9999)))[0]

        # ============================================================
        #                    DIRECT API CALL - NO HUEY
        # ============================================================
        success, api_result, error = submit_order_direct(
            cheapest_view['service'],
            link,
            final_views,
            False
        )
        
        if success and api_result and 'order' in api_result:
            new_streak_order = Order(
                user_id=current_user.id,
                service_id=cheapest_view['service'],
                link=link,
                quantity=final_views,
                cost=0.0,
                status='Completed',
                api_order_id=str(api_result['order']),
                api_response=str(api_result)
            )
            db.session.add(new_streak_order)
            flash(f"✅ Day {user.streak_count} Reward: {final_views} Views delivered!", "success")
        else:
            # If API fails, mark as pending for manual review
            new_streak_order = Order(
                user_id=current_user.id,
                service_id=cheapest_view['service'],
                link=link,
                quantity=final_views,
                cost=0.0,
                status='Pending',
                api_response=f"API Error: {error}"
            )
            db.session.add(new_streak_order)
            flash(f"⚠️ Streak reward queued but API had issues. Admin will review.", "warning")

        user.last_checkin = now
        db.session.commit()
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Streak Route Error: {str(e)}")
        flash("Error processing your daily reward.", "danger")

    return redirect(url_for('dashboard'))

# ==================== REFILL (DIRECT API) ====================

@app.route('/order/refill/<int:order_id>', methods=['POST'])
@login_required
@limiter.limit("3 per minute")
def request_refill(order_id):
    order = Order.query.filter_by(id=order_id, user_id=current_user.id).first_or_404()

    if order.status.lower() != 'completed':
        flash("Refill is only available for completed orders.", "warning")
        return redirect(url_for('status'))

    if not order.api_order_id:
        flash("This order does not have a provider ID to refill.", "danger")
        return redirect(url_for('status'))

    if order.timestamp < (datetime.utcnow() - timedelta(days=30)):
        flash("Refill period has expired for this order.", "info")
        return redirect(url_for('status'))

    try:
        payload = {
            'key': API_KEY,
            'action': 'refill',
            'order': order.api_order_id
        }
        
        response = requests.post(API_URL, data=payload, timeout=15)
        api_res = response.json()

        if 'refill' in api_res:
            order.api_response = f"Refill ID: {api_res['refill']} requested on {datetime.now()}"
            db.session.commit()
            flash(f"Refill requested successfully! Refill ID: {api_res['refill']}", "success")
        elif 'error' in api_res:
            app.logger.error(f"SMM API Refill Error: {api_res['error']}")
            flash(f"Provider Error: {api_res['error']}", "danger")
        else:
            flash("Received an unexpected response from the provider.", "danger")

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Critical Refill Route Exception: {str(e)}")
        flash("An internal error occurred while processing the refill.", "danger")

    return redirect(url_for('status'))

# ==================== SUBSCRIPTION ====================

@app.route('/subscribe', methods=['GET', 'POST'])
@login_required
def subscribe():
    plans = {
        'basic': {'price': 300, 'days': 30, 'name': 'Basic'},
        'pro': {'price': 500, 'days': 30, 'name': 'Pro'},
        'premium': {'price': 2000, 'days': 180, 'name': 'Premium'}
    }

    if request.method == 'POST':
        plan_id = request.form.get('plan')
        if plan_id not in plans:
            flash("Invalid plan selected.", "danger")
            return redirect(url_for('subscribe'))
        
        selected = plans[plan_id]
        
        if current_user.balance < selected['price']:
            flash(f"Insufficient balance. You need {selected['price']} PKR to {'upgrade' if current_user.is_sub_active else 'subscribe'}.", "danger")
            return redirect(url_for('wallet'))

        try:
            user = User.query.filter_by(id=current_user.id).with_for_update().first()
            user.balance -= selected['price']
            user.sub_plan = plan_id
            user.is_sub_active = True
            user.sub_expiry = datetime.utcnow() + timedelta(days=selected['days'])
            
            db.session.add(Transaction(user_id=user.id, amount=-selected['price'], type='sub_purchase'))
            
            if plan_id == 'premium':
                user.balance += 200.0
                db.session.add(Transaction(user_id=user.id, amount=200.0, type='sub_cashback'))

            db.session.commit()
            flash(f"Successfully activated {selected['name']} plan!", "success")
            return redirect(url_for('subscribe'))
            
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Subscription Error: {e}")
            flash("An error occurred during purchase.", "danger")
            return redirect(url_for('subscribe'))

    return render_template('subscribe.html',
                           plans=plans,
                           user=current_user,
                           datetime_now=datetime.utcnow())

@app.route('/cancel-subscription', methods=['POST'])
@login_required
def cancel_subscription():
    current_user.is_sub_active = False
    current_user.sub_plan = 'none'
    current_user.sub_expiry = None
    db.session.commit()
    flash("Subscription cancelled successfully.", "info")
    return redirect(url_for('subscribe'))

# ==================== PROFILE ====================

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        new_name = request.form.get('name', '').strip()
        new_email = request.form.get('email', '').strip().lower()
        new_currency = request.form.get('preferred_currency', 'PKR')
        new_password = request.form.get('password', '')
        current_pwd_confirm = request.form.get('current_password', '')
        
        if not check_password_hash(current_user.password, current_pwd_confirm):
            flash('Current password incorrect.', 'danger')
            return redirect(url_for('profile'))
            
        if new_email != current_user.email:
            if User.query.filter_by(email=new_email).first():
                flash('Email already in use.', 'danger')
                return redirect(url_for('profile'))
        try:
            current_user.name = new_name
            current_user.email = new_email
            current_user.preferred_currency = new_currency
            if new_password:
                if len(new_password) < 8:
                    flash('Password too short.', 'warning')
                    return redirect(url_for('profile'))
                current_user.password = generate_password_hash(new_password, method='scrypt')
            db.session.commit()
            flash('Profile updated successfully.', 'success')
        except Exception as e:
            db.session.rollback()
            flash('An error occurred.', 'danger')
        return redirect(url_for('profile'))
    return render_template('profile.html')

@app.route('/refer')
@login_required
def refer():
    referral_link = url_for('register', ref=current_user.referral_code, _external=True)
    return render_template("refer.html", referral_link=referral_link)

# ==================== ADMIN ROUTES ====================

@app.route('/hadi_path', methods=['GET', 'POST'])
@login_required
@admin_required
def hadi_dashboard():
    try:
        total_users = User.query.count()
        total_balance = db.session.query(db.func.sum(User.balance)).scalar() or 0.0
        pending_deposits = DepositRequest.query.filter_by(status='pending').count()
        total_orders = Order.query.count()
        revenue_raw = db.session.query(db.func.sum(DepositRequest.amount)).filter_by(status='approved').scalar() or 0.0
        referral_payouts = db.session.query(db.func.sum(Transaction.amount)).filter_by(type='ref_bonus').scalar() or 0.0
        recent_users = User.query.order_by(User.id.desc()).limit(5).all()
        recent_orders = Order.query.order_by(Order.id.desc()).limit(5).all()
        exchange_rate_val = get_rate('PKR')

        # DIRECT API CALL for provider balance
        provider_balance_usd = get_provider_balance_direct() or 0.0
        api_status = "Connected" if provider_balance_usd > 0 else "API Response Error"

        provider_bal_pkr = provider_balance_usd * exchange_rate_val
        net_worth = provider_bal_pkr - total_balance
        
        required_liquidity = total_balance * 1.05
        is_safe = provider_bal_pkr >= required_liquidity
        
        setting = SystemSetting.query.filter_by(key='is_ordering_enabled').first()
        if setting:
            setting.value = is_safe
            db.session.commit()
        
        if not is_safe:
            nw_color = "red"
        elif net_worth > 0:
            nw_color = "yellow"
        else:
            nw_color = "white"

        total_spend_pkr = abs(db.session.query(db.func.sum(Transaction.amount)).filter(Transaction.type.in_(['order_hold', 'order_refund'])).scalar() or 0.0)
        total_profit = total_spend_pkr * 0.3

        calc_results = None
        if request.method == 'POST' and 'daily_spend' in request.form:
            try:
                daily_val = float(request.form.get('daily_spend', 0))
                calc_results = {
                    'daily': round(daily_val * 0.3, 2),
                    'weekly': round(daily_val * 0.3 * 7, 2),
                    'monthly': round(daily_val * 0.3 * 30, 2)
                }
            except ValueError:
                calc_results = {'error': 'Invalid input'}

        rates_info = ExchangeRate.query.all()

        return render_template(
            'hadi_admin.html',
            stats={
                'users': total_users,
                'balance': round(total_balance, 2),
                'pending': pending_deposits,
                'orders': total_orders,
                'revenue': round(revenue_raw, 2),
                'referrals': round(referral_payouts, 2),
                'api_status': api_status,
                'provider_bal_usd': round(provider_balance_usd, 2),
                'spendable_pkr': round(min(provider_bal_pkr, total_balance * 1.4), 2),
                'pkr_rate': exchange_rate_val,
                'net_worth': round(net_worth, 2),
                'nw_color': nw_color,
                'total_profit': round(total_profit, 2),
                'is_ordering_enabled': is_safe
            },
            calc_results=calc_results,
            recent_users=recent_users,
            recent_orders=recent_orders,
            rates=rates_info
        )
    except Exception as e:
        app.logger.error(f"Super Admin Dashboard Error: {str(e)}")
        db.session.rollback()
        flash("Error loading administrative data.", "danger")
        return redirect(url_for('dashboard'))

# ==================== OTHER ADMIN ROUTES ====================

@app.route('/admin/deposits')
@login_required
@admin_required
def admin_deposits():
    deposits = DepositRequest.query.order_by(DepositRequest.timestamp.desc()).all()
    return render_template('admin_deposits.html', deposits=deposits)

@app.route('/admin/deposits/<int:deposit_id>/<action>')
@login_required
@admin_required
def update_deposit_status(deposit_id, action):
    deposit = DepositRequest.query.filter_by(id=deposit_id).with_for_update().first_or_404()
    if deposit.status != 'pending':
        flash("This deposit has already been processed.", "warning")
        return redirect(url_for('admin_deposits'))
    try:
        if action == "approve":
            user = User.query.filter_by(id=deposit.user_id).with_for_update().first()
            if user:
                deposit.status = "approved"
                user.balance += deposit.amount
                db.session.add(Transaction(user_id=user.id, amount=deposit.amount, type='deposit'))
                if user.referred_by:
                    referrer = User.query.filter_by(id=user.referred_by).with_for_update().first()
                    if referrer:
                        bonus = round(deposit.amount * 0.05, 2)
                        referrer.balance += bonus
                        db.session.add(Transaction(user_id=referrer.id, amount=bonus, type='ref_bonus'))
            flash(f"Deposit {deposit_id} approved.", "success")
        elif action == "reject":
            deposit.status = "rejected"
            flash(f"Deposit {deposit_id} rejected.", "info")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Admin Deposit Error: {str(e)}")
        flash("System error updating deposit.", "danger")
    return redirect(url_for('admin_deposits'))

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = db.session.query(User.id, User.name, User.username, User.email, User.balance, User.is_admin).all()
    return render_template('admin_users.html', users=users)

@app.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        try:
            new_balance = float(request.form.get('balance', 0))
            if user.balance != new_balance:
                diff = new_balance - user.balance
                user.balance = new_balance
                db.session.add(Transaction(user_id=user.id, amount=diff, type='admin_adj'))
            user.name = request.form.get('name')
            user.username = request.form.get('username')
            user.email = request.form.get('email').strip().lower()
            if user.id != current_user.id:
                user.is_admin = 'is_admin' in request.form
            db.session.commit()
            flash(f'User {user.username} updated.', 'success')
            return redirect(url_for('admin_users'))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Admin User Edit Error: {e}")
            flash('Update failed.', 'danger')
    return render_template('edit_user.html', user=user)

@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot delete yourself.', 'danger')
    else:
        db.session.delete(user)
        db.session.commit()
        flash('User deleted.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/exchange', methods=['POST'])
@login_required
@admin_required
def update_exchange_manual():
    target = request.form.get('target_currency')
    new_rate = request.form.get('rate')
    lock = 'is_locked' in request.form
    
    try:
        rate_rec = ExchangeRate.query.filter_by(target_currency=target).first()
        if rate_rec:
            rate_rec.rate = float(new_rate)
            rate_rec.is_locked = lock
            rate_rec.source = 'manual'
            db.session.commit()
            flash(f"Rate for {target} updated manually.", "success")
    except Exception as e:
        db.session.rollback()
        flash("Failed to update rate.", "danger")
    return redirect(url_for('hadi_dashboard'))

@app.route('/reset_pan')
@login_required
@admin_required
def clear_services_cache():
    global SERVICES_CACHE
    SERVICES_CACHE['data'] = None
    SERVICES_CACHE['timestamp'] = 0
    flash("Services cache has been cleared. New data will be fetched on next request.", "success")
    return redirect(url_for('hadi_dashboard'))

# ==================== EXPORT ROUTES ====================

@app.route('/export_logs')
@login_required
@admin_required
def export_logs():
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet("Financial Report")

    header_fmt = workbook.add_format({
        'bold': True, 'bg_color': '#1a1a1a', 'font_color': '#ffc107',
        'border': 1, 'align': 'center', 'valign': 'vcenter'
    })
    data_fmt = workbook.add_format({'border': 1, 'align': 'left'})
    currency_fmt = workbook.add_format({'border': 1, 'num_format': 'PKR #,##0.00'})
    date_fmt = workbook.add_format({'border': 1, 'num_format': 'yyyy-mm-dd hh:mm'})
    summary_label_fmt = workbook.add_format({'bold': True, 'bg_color': '#f8f9fa', 'border': 1})
    summary_val_fmt = workbook.add_format({'bold': True, 'num_format': 'PKR #,##0.00', 'border': 1, 'bg_color': '#f8f9fa'})

    headers = ["User Full Name", "Sender Account Name", "Sender Account Number", "Date", "Deposit Amount", "Current Balance"]
    
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, header_fmt)
    
    worksheet.set_column(0, 2, 22)
    worksheet.set_column(3, 3, 18)
    worksheet.set_column(4, 5, 15)

    users = User.query.all()
    row = 1
    grand_total_deposited = 0.0

    for user in users:
        deposits = DepositRequest.query.filter_by(user_id=user.id, status='approved').all()
        
        if not deposits:
            worksheet.write(row, 0, user.name, data_fmt)
            worksheet.write(row, 1, "N/A", data_fmt)
            worksheet.write(row, 2, "N/A", data_fmt)
            worksheet.write(row, 3, "N/A", data_fmt)
            worksheet.write(row, 4, 0, currency_fmt)
            worksheet.write(row, 5, user.balance, currency_fmt)
            row += 1
        else:
            for dep in deposits:
                worksheet.write(row, 0, user.name, data_fmt)
                worksheet.write(row, 1, dep.sender_name, data_fmt)
                worksheet.write(row, 2, dep.sender_account, data_fmt)
                worksheet.write(row, 3, dep.timestamp.strftime('%Y-%m-%d %H:%M'), date_fmt)
                worksheet.write(row, 4, dep.amount, currency_fmt)
                worksheet.write(row, 5, user.balance, currency_fmt)
                grand_total_deposited += dep.amount
                row += 1

    row += 2
    total_spent_raw = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.type.in_(['order_hold', 'sub_purchase', 'order_refund'])
    ).scalar() or 0.0
    total_available = db.session.query(db.func.sum(User.balance)).scalar() or 0.0

    summary_data = [
        ("Total Deposited Amount", grand_total_deposited),
        ("Total Spending / Used Balance", abs(total_spent_raw)),
        ("Total Available Balance", total_available)
    ]

    for label, value in summary_data:
        worksheet.merge_range(row, 0, row, 1, label, summary_label_fmt)
        worksheet.write(row, 4, value, summary_val_fmt)
        row += 1

    workbook.close()
    output.seek(0)

    filename = f"EngageX_Logs_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(output,
                     download_name=filename,
                     as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/expert_pro')
@login_required
@admin_required
def expert_pro():
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    ws = workbook.add_worksheet("Pro Financial Analysis")

    header_fmt = workbook.add_format({'bold': True, 'bg_color': '#002060', 'font_color': 'white', 'border': 1, 'align': 'center'})
    row_fmt = workbook.add_format({'border': 1, 'align': 'left'})
    money_fmt = workbook.add_format({'num_format': 'PKR #,##0.00', 'border': 1})
    profit_fmt = workbook.add_format({'num_format': 'PKR #,##0.00', 'border': 1, 'font_color': '#006100', 'bg_color': '#C6EFCE'})
    summary_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9EAD3', 'border': 1, 'num_format': 'PKR #,##0.00'})

    headers = ["Username", "Total Deposited", "User Spending (Revenue)", "Earned Profit (Markup)", "Subscription Profit", "Current Balance", "Total Net Earned"]
    
    for col, h in enumerate(headers):
        ws.write(0, col, h, header_fmt)
    
    ws.set_column('A:A', 20)
    ws.set_column('B:G', 18)

    users = User.query.all()
    current_row = 1
    total_platform_deposits = 0.0
    total_platform_profit = 0.0

    for user in users:
        total_dep = db.session.query(func.sum(DepositRequest.amount)).filter(
            DepositRequest.user_id == user.id, DepositRequest.status == 'approved'
        ).scalar() or 0.0

        spent_raw = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == user.id, Transaction.type == 'order_hold'
        ).scalar() or 0.0
        refund_raw = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == user.id, Transaction.type == 'order_refund'
        ).scalar() or 0.0
        net_revenue = abs(spent_raw) - abs(refund_raw)

        order_markup_profit = net_revenue * 0.20
        sub_raw = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == user.id, Transaction.type == 'sub_purchase'
        ).scalar() or 0.0
        sub_profit = abs(sub_raw)

        total_user_net_earned = order_markup_profit + sub_profit

        ws.write(current_row, 0, user.username, row_fmt)
        ws.write(current_row, 1, total_dep, money_fmt)
        ws.write(current_row, 2, net_revenue, money_fmt)
        ws.write(current_row, 3, order_markup_profit, profit_fmt)
        ws.write(current_row, 4, sub_profit, profit_fmt)
        ws.write(current_row, 5, user.balance, money_fmt)
        ws.write(current_row, 6, total_user_net_earned, summary_fmt)

        total_platform_deposits += total_dep
        total_platform_profit += total_user_net_earned
        current_row += 1

    current_row += 2
    ws.merge_range(current_row, 0, current_row, 5, "TOTAL PLATFORM DEPOSITS", header_fmt)
    ws.write(current_row, 6, total_platform_deposits, summary_fmt)
    
    current_row += 1
    ws.merge_range(current_row, 0, current_row, 5, "TOTAL PLATFORM NET PROFIT", header_fmt)
    ws.write(current_row, 6, total_platform_profit, summary_fmt)

    workbook.close()
    output.seek(0)
    
    return send_file(
        output,
        download_name=f"Expert_Pro_Financials_{datetime.now().strftime('%Y%m%d')}.xlsx",
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# ==================== PUSH NOTIFICATIONS ====================

from pywebpush import webpush, WebPushException

VAPID_PRIVATE = os.environ.get('VAPID_PRIVATE_KEY')
VAPID_PUBLIC = os.environ.get('VAPID_PUBLIC_KEY')
VAPID_CLAIMS = {"sub": f"mailto:{os.environ.get('VAPID_SENDER_EMAIL')}"}

@app.route('/subscribe-push', methods=['POST'])
@login_required
def subscribe_push():
    data = request.get_json()
    sub = PushSubscription.query.filter_by(user_id=current_user.id).first()
    if not sub:
        sub = PushSubscription(user_id=current_user.id)
    sub.subscription_json = json.dumps(data)
    db.session.add(sub)
    db.session.commit()
    return jsonify({"status": "success"})

@app.route('/admin/noti', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_noti():
    if request.method == 'POST':
        title = request.form.get('title', 'Hadi88 Premium Alert')
        msg = request.form.get('message')
        
        private_key = os.environ.get('VAPID_PRIVATE_KEY')
        claims = {"sub": f"mailto:{os.environ.get('VAPID_SENDER_EMAIL', 'admin@hadi88.online')}"}
        
        subs = PushSubscription.query.all()
        
        for s in subs:
            try:
                webpush(
                    subscription_info=json.loads(s.subscription_json),
                    data=json.dumps({
                        "title": title,
                        "body": msg,
                        "url": "/dashboard"
                    }),
                    vapid_private_key=private_key,
                    vapid_claims=claims
                )
            except WebPushException:
                db.session.delete(s)
                
        db.session.commit()
        flash("Mass notification successfully sent to all active devices!", "success")
        return redirect(url_for('admin_noti'))
        
    return render_template('admin_noti.html')

# ==================== STATIC FILES ====================

@app.route('/sw.js')
def serve_sw():
    return send_from_directory('.', 'sw.js', mimetype='application/javascript')

# ==================== SEO ====================

@app.route('/robots.txt')
def robots_txt():
    base_url = request.host_url.rstrip('/')
    lines = [
        "User-agent: *",
        f"Sitemap: {base_url}/sitemap.xml",
        "",
        "Disallow: /dashboard",
        "Disallow: /wallet",
        "Disallow: /profile",
        "Disallow: /status",
        "Disallow: /refer",
        "Disallow: /logout",
        "Disallow: /freetrial",
        "Disallow: /streak",
        "",
        "Disallow: /hadi_path",
        "Disallow: /admin/",
        "Disallow: /export_logs",
        "Disallow: /expert_pro",
        "",
        "Disallow: /check_user",
        "Disallow: /get_captcha",
        "Disallow: /reset_pan"
    ]
    response = make_response("\n".join(lines))
    response.headers["Content-Type"] = "text/plain"
    return response

@app.route('/sitemap.xml', methods=['GET'])
def sitemap():
    pages = []
    lastmod = datetime.now().strftime('%Y-%m-%d')
    base_url = request.host_url.rstrip('/')
    
    public_urls = [
        ('/', 'daily', '1.0'),
        ('/login', 'weekly', '0.8'),
        ('/register', 'weekly', '0.9'),
        ('/privacy-policy', 'monthly', '0.3'),
        ('/terms-and-conditions', 'monthly', '0.3')
    ]
    
    for url, changefreq, priority in public_urls:
        pages.append({
            'loc': f"{base_url}{url}",
            'lastmod': lastmod,
            'changefreq': changefreq,
            'priority': priority
        })

    sitemap_xml = render_template_string("""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      {% for page in pages %}
      <url>
        <loc>{{ page.loc }}</loc>
        <lastmod>{{ page.lastmod }}</lastmod>
        <changefreq>{{ page.changefreq }}</changefreq>
        <priority>{{ page.priority }}</priority>
      </url>
      {% endfor %}
    </urlset>
    """, pages=pages)
    
    response = make_response(sitemap_xml)
    response.headers["Content-Type"] = "application/xml"
    return response

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html', title="Privacy Policy - Hadi88")

@app.route('/terms-and-conditions')
def terms_and_conditions():
    return render_template('terms_and_conditions.html', title="Terms & Conditions - Hadi88")

# ==================== CHECK USER ====================

@app.route('/check_user', methods=['POST'])
@limiter.limit("5 per minute")
def check_user():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400
    
    email = data.get('email', '').strip().lower()
    username = data.get('username', '').strip()
    
    email_exists = False
    if email:
        email_exists = User.query.filter_by(email=email).first() is not None
    
    username_exists = False
    if username:
        username_exists = User.query.filter_by(username=username).first() is not None
        
    return jsonify({'email_exists': email_exists, 'username_exists': username_exists})

# ==================== OFFLINE ====================

@app.route('/offline.html')
def offline():
    return render_template('offline.html')

# ==================== ERROR HANDLERS ====================

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify(error="Too many requests. Please try again later."), 429

@app.errorhandler(500)
def internal_error(e):
    app.logger.error(f"Server Error: {e}")
    db.session.rollback()
    return render_template('500.html'), 500

@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(f"Unhandled Exception: {str(e)}")
    if hasattr(e, 'code') and e.code in [400, 401, 403, 404, 405]:
        return render_template(f'{e.code}.html'), e.code
    db.session.rollback()
    return render_template('500.html'), 500

# ==================== DATABASE INITIALIZATION ====================

import os
from sqlalchemy import inspect, text

def initialize_database():
    try:
        db.create_all()
        
        inspector = inspect(db.engine)
        user_cols = [c['name'] for c in inspector.get_columns('user')]
        freetrial_cols = [c['name'] for c in inspector.get_columns('free_trial_link')]
        
        with db.engine.connect() as conn:
            if 'new_f' not in user_cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN new_f BOOLEAN DEFAULT FALSE"))
                app.logger.info("Migration: Added 'new_f' column to User table.")

            if 'referral_code' not in user_cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN referral_code VARCHAR(10)"))
                app.logger.info("Migration: Added 'referral_code' column to User table.")
            
            if 'preferred_currency' not in user_cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN preferred_currency VARCHAR(3) DEFAULT 'PKR'"))
                app.logger.info("Migration: Added 'preferred_currency' column to User table.")

            if 'last_checkin' not in user_cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN last_checkin DATETIME"))
                app.logger.info("Migration: Added 'last_checkin' column to User table.")

            if 'streak_count' not in user_cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN streak_count INTEGER DEFAULT 0"))
                app.logger.info("Migration: Added 'streak_count' column to User table.")
            
            if 'device_fingerprint' not in freetrial_cols:
                conn.execute(text("ALTER TABLE free_trial_link ADD COLUMN device_fingerprint VARCHAR(255)"))
                app.logger.info("Migration: Added 'device_fingerprint' column to FreeTrialLink table.")
            
            conn.commit()

        users_without_codes = User.query.filter(
            (User.referral_code == None) | (User.referral_code == '')
        ).all()
        
        if users_without_codes:
            for user in users_without_codes:
                user.referral_code = generate_referral_code()
            db.session.commit()
            app.logger.info(f"Integrity: Generated codes for {len(users_without_codes)} users.")
        
        if not ExchangeRate.query.first():
            app.logger.info("Initializing exchange rates...")
            sync_exchange_rates()
            
        if not SystemSetting.query.filter_by(key='is_ordering_enabled').first():
            db.session.add(SystemSetting(key='is_ordering_enabled', value=True))
            db.session.commit()

        # Automatic Admin Promotion from Secret Variable
        admin_username = os.getenv('ADMIN_USERNAME', 'bhattixx_vcpk')
        if admin_username:
            admin_user = User.query.filter_by(username=admin_username).first()
            if admin_user:
                if not admin_user.is_admin:
                    admin_user.is_admin = True
                    db.session.commit()
                    app.logger.info(f"Admin status granted to {admin_username}.")
                else:
                    app.logger.info(f"User {admin_username} is already an admin. Skipping promotion.")
            else:
                app.logger.warning(f"Admin user '{admin_username}' not found in database.")
            
        app.logger.info("Database successfully initialized and migrated.")
            
    except Exception as e:
        app.logger.error(f"Initialization failure: {str(e)}")
        db.session.rollback()


# ==================== MAIN ====================

if __name__ == "__main__":
    with app.app_context():
        initialize_database()
    is_debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=is_debug, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))