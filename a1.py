import os
import logging
import random
import secrets
import string
import base64
import time
import json
import re
import requests
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, render_template_string, make_response, request, redirect, url_for, flash, session, jsonify, abort, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFProtect
from flask_talisman import Talisman
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from sqlalchemy import inspect, text

# ---------------------- 1. Configuration & Initializations ---------------------- #
load_dotenv()
app = Flask(__name__)

# SECURITY: Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SECURITY: Enforce HTTPS and set security headers
Talisman(app, content_security_policy=None, force_https=os.environ.get('FLASK_ENV') == 'production')

# SECURITY: Global CSRF protection
csrf = CSRFProtect(app)

# APP CONFIG
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'fallback-dev-key-123')
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'site.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# SECURITY: Secure Session Cookies
app.config.update(
    SESSION_COOKIE_SECURE=True, 
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# API CONFIG
EXCHANGE_API_KEY = os.environ.get('EXCHANGE_API_KEY', "36304092d2ffecd62b291ba8")
EXCHANGE_URL = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/latest/USD"
API_KEY = os.environ.get('SMM_API_KEY')
API_URL = "https://godofpanel.com/api/v2"

@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()

# ---------------------- 2. Models ---------------------- #

def generate_referral_code():
    """Generates a secure 6-character alphanumeric referral code."""
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(6))

class User(db.Model, UserMixin):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    email = db.Column(db.String(100), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    is_admin = db.Column(db.Boolean, default=False)
    new_f = db.Column(db.Boolean, default=False) # Free Trial Tracking
    referred_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    referral_code = db.Column(db.String(10), unique=True, nullable=True, index=True)
    preferred_currency = db.Column(db.String(3), default='PKR')
    
    # Relationships
    orders = db.relationship('Order', backref='owner', lazy=True, cascade="all, delete-orphan")
    transactions = db.relationship('Transaction', backref='owner', lazy=True, cascade="all, delete-orphan")
    referrals = db.relationship('User', backref=db.backref('referrer', remote_side=[id]))

class ExchangeRate(db.Model):
    __tablename__ = 'exchange_rate'
    id = db.Column(db.Integer, primary_key=True)
    base_currency = db.Column(db.String(3), default='USD')
    target_currency = db.Column(db.String(3), unique=True, nullable=False)
    rate = db.Column(db.Float, nullable=False)
    source = db.Column(db.String(20), default='api') 
    is_locked = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Transaction(db.Model):
    __tablename__ = 'transaction'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(50), index=True) # 'deposit', 'order_hold', 'ref_bonus', 'order_refund', 'admin_adj'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

class DepositRequest(db.Model):
    __tablename__ = 'deposit_request'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    sender_account = db.Column(db.String(100), nullable=False)
    sender_name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    transaction_id = db.Column(db.String(100), nullable=False, unique=True)
    status = db.Column(db.String(20), default='pending', index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_rel = db.relationship('User', backref='deposit_requests')

class Order(db.Model):
    __tablename__ = 'order'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    service_id = db.Column(db.String(50), nullable=False)
    link = db.Column(db.String(500), nullable=False)      
    quantity = db.Column(db.Integer, nullable=False)
    cost = db.Column(db.Float, nullable=False)
    api_response = db.Column(db.Text)
    status = db.Column(db.String(50), default='Processing', index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

class FreeTrialLink(db.Model):
    __tablename__ = 'free_trial_link'
    id = db.Column(db.Integer, primary_key=True)
    link = db.Column(db.String(500), unique=True, nullable=False, index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# ---------------------- 3. Helpers & Auth ---------------------- #

@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except:
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

def generate_captcha_data():
    chars = string.ascii_letters + string.digits
    chars = chars.replace('0', '').replace('O', '').replace('I', '').replace('l', '') 
    captcha_text = ''.join(secrets.choice(chars) for _ in range(4))
    svg = f"""<svg width="120" height="45" xmlns="http://www.w3.org/2000/svg">
        <rect width="100%" height="100%" fill="#1a1a1a"/>
        <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" font-family="Arial" font-weight="bold" font-size="24" fill="#ffc107" letter-spacing="5">{captcha_text}</text>
        <line x1="0" y1="20" x2="120" y2="30" stroke="#ffc107" stroke-width="1" opacity="0.3"/>
        <line x1="10" y1="40" x2="110" y2="5" stroke="#ffc107" stroke-width="1" opacity="0.3"/>
    </svg>"""
    b64_svg = base64.b64encode(svg.encode()).decode()
    session['captcha_text'] = captcha_text
    return b64_svg

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

def clean_category_name(name):
    name = re.sub(r'\[.*?\]|\(.*?\)', '', name)
    jargon = ['fast', 'instant', 'non drop', 'nondrop', 's1', 's2', 's3', 'hq', 'real', 'best', 'working']
    words = name.lower().split()
    cleaned_words = [w for w in words if w not in jargon]
    result = " ".join(cleaned_words).title()
    return " ".join(result.split()[:4])

def update_services_json():
    try:
        response = requests.post(API_URL, data={'key': API_KEY, 'action': 'services'}, timeout=15)
        response.raise_for_status()
        raw_services = response.json()
        usd_to_pkr = get_rate('PKR')
        structured_data = {}
        for service in raw_services:
            rate_usd = float(service.get('rate', 0))
            base_pkr = rate_usd * usd_to_pkr
            markup = 1.50 if base_pkr < 4.0 else 1.30
            price_pkr = round(base_pkr * markup, 2)
            category = service.get('category', 'Uncategorized')
            service_item = {
                "service_id": service.get('service'),
                "name": service.get('name'),
                "rate_pkr": price_pkr,
                "min": service.get('min'),
                "max": service.get('max'),
                "description": service.get('description', '')
            }
            if category not in structured_data:
                structured_data[category] = []
            structured_data[category].append(service_item)
        file_path = os.path.join(basedir, 'services.json')
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(structured_data, f, indent=4, ensure_ascii=False)
        return True, "services.json updated successfully."
    except Exception as e:
        app.logger.error(f"Fetchall Error: {str(e)}")
        return False, str(e)

# ---------------------- 4. Core Routes ---------------------- #

@app.route('/')
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
            login_user(user, remember=True)
            return redirect(url_for('dashboard'))
        else:
            time.sleep(0.1) 
            flash('Invalid credentials', 'danger')
    return render_template('login.html', captcha_img=generate_captcha_data())

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
            name=name, username=username, email=email, 
            password=generate_password_hash(password_raw, method='scrypt'),
            referred_by=referred_by, referral_code=generate_referral_code()
        )
        try:
            db.session.add(user)
            db.session.commit()
            session.pop('ref', None)
            login_user(user, remember=True)  
            return redirect(url_for('dashboard'))
        except Exception as e:
            db.session.rollback()
            flash('Internal error during registration.', 'danger')
    return render_template('register.html', captcha_img=generate_captcha_data())

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

@app.route('/get_captcha')
def get_captcha():
    return jsonify({'img': generate_captcha_data()})

@app.route('/validate_order_captcha', methods=['POST'])
@login_required
def validate_order_captcha():
    is_valid, message = validate_captcha()
    if is_valid: return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': message}), 400

# ---------------------- 5. Service & Order Routes ---------------------- #

@app.route('/das', methods=['GET', 'POST'])
@login_required
def new_order():
    usd_to_pkr = get_rate('PKR')
    user_curr = current_user.preferred_currency
    user_rate = get_rate(user_curr)
    
    if request.method == 'POST':
        now = time.time()
        order_attempts = [t for t in session.get('order_attempts', []) if now - t < 300]
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
            service_id = request.form.get('service')
            link = request.form.get('link')
            quantity_per_run = int(request.form.get('quantity', 0))
            is_drip = request.form.get('is_drip_feed') == 'on'
            runs = int(request.form.get('runs', 0)) if is_drip else 0
            interval = int(request.form.get('interval', 0)) if is_drip else 0
            
            response = requests.post(API_URL, data={'key': API_KEY, 'action': 'services'}, timeout=10)
            services = response.json()
            service_info = next((s for s in services if str(s['service']) == service_id), None)
            
            if not service_info:
                flash("Invalid service.", "danger")
                return redirect(url_for('new_order'))
                
            total_quantity = quantity_per_run * runs if is_drip else quantity_per_run
            cost_usd = (float(service_info['rate']) * total_quantity / 1000)
            base_cost_pkr = cost_usd * usd_to_pkr
            markup = 1.50 if base_cost_pkr < 4.0 else 1.30
            price_pkr = round(base_cost_pkr * markup, 2)
            
            # ATOMIC TRANSACTION START
            user = User.query.filter_by(id=current_user.id).with_for_update().first()
            if user.balance < price_pkr:
                flash(f"Insufficient balance. Need {price_pkr} PKR", "danger")
                return redirect(url_for('wallet'))
                
            user.balance -= price_pkr 
            db.session.add(Transaction(user_id=user.id, amount=-price_pkr, type='order_hold'))
            db.session.commit() 
            
            try:
                payload = {'key': API_KEY, 'action': 'add', 'service': service_id, 'link': link, 'quantity': quantity_per_run}
                if is_drip: payload.update({'runs': runs, 'interval': interval})
                
                api_req = requests.post(API_URL, data=payload, timeout=15)
                api_response = api_req.json()
                
                if 'error' in api_response: raise Exception(f"API Error: {api_response['error']}")
                
                new_order_rec = Order(user_id=current_user.id, service_id=service_id, link=link, quantity=total_quantity, cost=price_pkr, api_response=str(api_response))
                db.session.add(new_order_rec)
                db.session.commit()
                return render_template('order_result.html', result=api_response)
            except Exception as e:
                db.session.rollback()
                # Atomic Refund
                user_ref = User.query.filter_by(id=current_user.id).with_for_update().first()
                user_ref.balance += price_pkr
                db.session.add(Transaction(user_id=user_ref.id, amount=price_pkr, type='order_refund'))
                db.session.commit()
                flash(f"Order failed: {str(e)}", "danger")
                return redirect(url_for('new_order'))
        except Exception as e:
            app.logger.error(f"Critical Order Error: {e}")
            flash("Internal processing error.", "danger")
            return redirect(url_for('new_order'))

    # GET Logic for Category Display
    try:
        response = requests.post(API_URL, data={'key': API_KEY, 'action': 'services'}, timeout=10)
        raw_services = response.json()
        emoji_map = {'tiktok': '👍', 'facebook': '💙', 'youtube': '🎬', 'instagram': '📸', 'twitter': '🐦', 'x': '', 'telegram': '✈️', 'spotify': '🎵', 'snapchat': '👻', 'whatsapp': '💬', 'threads': '🧵', 'discord': '👾'}
        processed_categories = {}
        platforms = set()
        
        for s in raw_services:
            original_rate_usd = float(s.get("rate", 0))
            base_pkr_ref = original_rate_usd * usd_to_pkr
            markup = 1.50 if base_pkr_ref < 4.0 else 1.30
            display_rate = round(original_rate_usd * user_rate * markup, 2)
            s['rate'] = display_rate 
            
            raw_cat = s.get("category", "Other")
            platform_name = raw_cat.split()[0].strip()
            platforms.add(platform_name)
            base_display_name = clean_category_name(raw_cat)
            emoji = emoji_map.get(platform_name.lower(), '✨')
            styled_name = f"{emoji} {base_display_name}"
            
            if raw_cat not in processed_categories:
                processed_categories[raw_cat] = {'display_name': styled_name, 'platform': platform_name, 'min_price': display_rate, 'services': []}
            processed_categories[raw_cat]['services'].append(s)
            if display_rate < processed_categories[raw_cat]['min_price']:
                processed_categories[raw_cat]['min_price'] = display_rate
                
        sorted_categories = dict(sorted(processed_categories.items(), key=lambda item: item[1]['min_price']))
        return render_template("new_order.html", grouped_services=sorted_categories, platforms=sorted(list(platforms)), captcha_img=generate_captcha_data(), currency_symbol=user_curr)
    except Exception as e:
        return render_template("errors/api_down.html"), 503

@app.route('/freetrial', methods=['GET', 'POST'])
@login_required
def freetrial():
    if current_user.new_f:
        return render_template('freetrial.html', used=True)
    if request.method == 'POST':
        tiktok_link = request.form.get('link', '').strip()
        if not tiktok_link or "tiktok.com" not in tiktok_link.lower():
            flash("Please provide a valid TikTok profile link.", "danger")
            return redirect(url_for('freetrial'))
        if FreeTrialLink.query.filter_by(link=tiktok_link).first():
            flash("Link already used.", "warning")
            return redirect(url_for('freetrial'))
        try:
            usd_to_pkr = get_rate('PKR')
            response = requests.post(API_URL, data={'key': API_KEY, 'action': 'services'}, timeout=10)
            services = response.json()
            selected = None
            for s in services:
                name_l, cat_l = s.get('name', '').lower(), s.get('category', '').lower()
                if 'tiktok' in cat_l and 'follower' in name_l:
                    pkr_1k = (float(s.get('rate', 0)) * usd_to_pkr) * 1.3
                    if 150 <= pkr_1k <= 250:
                        selected = s
                        break
            if not selected:
                flash("Trial unavailable.", "danger")
                return redirect(url_for('freetrial'))
            api_res = requests.post(API_URL, data={'key': API_KEY, 'action': 'add', 'service': selected['service'], 'link': tiktok_link, 'quantity': 100}, timeout=15).json()
            if 'error' in api_res: raise Exception(api_res['error'])
            
            user = User.query.filter_by(id=current_user.id).with_for_update().first()
            user.new_f = True
            db.session.add(FreeTrialLink(link=tiktok_link))
            db.session.add(Order(user_id=current_user.id, service_id=selected['service'], link=tiktok_link, quantity=100, cost=0.0, api_response=str(api_res)))
            db.session.commit()
            return render_template('order_result.html', result={'order': api_res.get('order'), 'trial': True})
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template('freetrial.html', used=False)

@app.route('/tiktok')
@login_required
def tiktok_services():
    json_path = os.path.join(basedir, 'services.json')
    if not os.path.exists(json_path):
        flash("Services data file is missing.", "danger")
        return redirect(url_for('dashboard'))
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            categorized_data = json.load(f)
    except Exception as e:
        flash("Error loading services.", "danger")
        return redirect(url_for('dashboard'))
    return render_template('tiktok.html', grouped_services=categorized_data, user_balance=current_user.balance, captcha_img=generate_captcha_data())

# ---------------------- 6. Wallet & Profile ---------------------- #

@app.route('/wallet', methods=['GET', 'POST'])
@login_required
def wallet():
    if request.method == 'POST':
        try:
            s_acc, s_name, t_id, amt_s = request.form.get('sender_account'), request.form.get('sender_name'), request.form.get('transaction_id'), request.form.get('amount', '0')
            if not all([s_acc, s_name, t_id, amt_s]):
                flash("All fields required.", "danger")
            else:
                amt = float(amt_s)
                if amt <= 0: raise ValueError()
                db.session.add(DepositRequest(user_id=current_user.id, sender_account=s_acc, sender_name=s_name, amount=amt, transaction_id=t_id))
                db.session.commit()
                session['trigger_whatsapp'] = True
                flash("Request submitted.", "success")
        except:
            db.session.rollback()
            flash("Invalid input.", "danger")
    return render_template('wallet.html', balance=current_user.balance)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        cur_pwd = request.form.get('current_password', '')
        if not check_password_hash(current_user.password, cur_pwd):
            flash('Current password incorrect.', 'danger')
        else:
            try:
                current_user.name = request.form.get('name')
                current_user.email = request.form.get('email').strip().lower()
                current_user.preferred_currency = request.form.get('preferred_currency', 'PKR')
                new_pwd = request.form.get('password')
                if new_pwd and len(new_pwd) >= 8:
                    current_user.password = generate_password_hash(new_pwd, method='scrypt')
                db.session.commit()
                flash('Profile updated.', 'success')
            except:
                db.session.rollback()
                flash('Error updating profile.', 'danger')
    return render_template('profile.html')

@app.route('/status', methods=['GET', 'POST'])
@login_required
def status():
    search_query = request.form.get('order_id') if request.method == 'POST' else None
    if search_query:
        orders_q = Order.query.filter_by(id=search_query, user_id=current_user.id).all()
    else:
        orders_q = Order.query.filter_by(user_id=current_user.id).order_by(Order.timestamp.desc()).all()
    
    status_data = [{"id": o.id, "service": o.service_id, "link": o.link, "quantity": o.quantity, "status": o.status, "date": o.timestamp.strftime('%Y-%m-%d %H:%M'), "cost": o.cost} for o in orders_q]
    return render_template('status.html', orders=status_data, search_query=search_query)

@app.route('/refer')
@login_required
def refer():
    referral_link = url_for('register', ref=current_user.referral_code, _external=True)
    return render_template("refer.html", referral_link=referral_link)

# ---------------------- 7. Admin Routes ---------------------- #

@app.route('/hadi_path', methods=['GET', 'POST'])
@login_required
@admin_required
def hadi_dashboard():
    try:
        t_users = User.query.count()
        t_balance = db.session.query(db.func.sum(User.balance)).scalar() or 0.0
        p_deposits = DepositRequest.query.filter_by(status='pending').count()
        t_orders = Order.query.count()
        p_bal_usd, api_status = 0.0, "Disconnected"
        if API_KEY:
            try:
                r = requests.post(API_URL, data={'key': API_KEY, 'action': 'balance'}, timeout=5).json()
                p_bal_usd = float(r.get('balance', 0))
                api_status = "Connected"
            except: api_status = "Connection Failed"

        rate_val = get_rate('PKR')
        net_worth = (p_bal_usd * rate_val) - t_balance
        nw_color = "red" if net_worth < 0 else "yellow" if net_worth > 0 else "white"
        
        t_spend = abs(db.session.query(db.func.sum(Transaction.amount)).filter(Transaction.type.in_(['order_hold', 'order_refund'])).scalar() or 0.0)
        
        calc_results = None
        if request.method == 'POST' and 'daily_spend' in request.form:
            try:
                dv = float(request.form.get('daily_spend', 0))
                calc_results = {'daily': round(dv * 0.3, 2), 'weekly': round(dv * 0.3 * 7, 2), 'monthly': round(dv * 0.3 * 30, 2)}
            except: calc_results = {'error': 'Invalid input'}

        return render_template('hadi_admin.html', stats={'users': t_users, 'balance': round(t_balance, 2), 'pending': p_deposits, 'orders': t_orders, 'api_status': api_status, 'provider_bal_usd': round(p_bal_usd, 2), 'pkr_rate': rate_val, 'net_worth': round(net_worth, 2), 'nw_color': nw_color, 'total_profit': round(t_spend * 0.3, 2)}, calc_results=calc_results, recent_users=User.query.order_by(User.id.desc()).limit(5).all(), recent_orders=Order.query.order_by(Order.id.desc()).limit(5).all(), rates=ExchangeRate.query.all())
    except:
        return redirect(url_for('dashboard'))

@app.route('/admin/exchange', methods=['POST'])
@login_required
@admin_required
def update_exchange_manual():
    try:
        rec = ExchangeRate.query.filter_by(target_currency=request.form.get('target_currency')).first()
        if rec:
            rec.rate = float(request.form.get('rate'))
            rec.is_locked = 'is_locked' in request.form
            rec.source = 'manual'
            db.session.commit()
            flash("Rate updated.", "success")
    except: db.session.rollback()
    return redirect(url_for('hadi_dashboard'))

@app.route('/admin/deposits/<int:deposit_id>/<action>')
@login_required
@admin_required
def update_deposit_status(deposit_id, action):
    dep = DepositRequest.query.filter_by(id=deposit_id).with_for_update().first_or_404()
    if dep.status == 'pending':
        try:
            if action == "approve":
                u = User.query.filter_by(id=dep.user_id).with_for_update().first()
                dep.status = "approved"
                u.balance += dep.amount
                db.session.add(Transaction(user_id=u.id, amount=dep.amount, type='deposit'))
                if u.referred_by:
                    ref = User.query.filter_by(id=u.referred_by).with_for_update().first()
                    bonus = round(dep.amount * 0.05, 2)
                    ref.balance += bonus
                    db.session.add(Transaction(user_id=ref.id, amount=bonus, type='ref_bonus'))
            elif action == "reject":
                dep.status = "rejected"
            db.session.commit()
        except: db.session.rollback()
    return redirect(url_for('admin_deposits'))

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    return render_template('admin_users.html', users=User.query.all())

@app.route('/fetchall')
@login_required
@admin_required
def fetchall_services():
    success, msg = update_services_json()
    flash(msg, "success" if success else "danger")
    return redirect(url_for('hadi_dashboard'))

# ---------------------- 8. System Routes ---------------------- #

@app.route('/robots.txt')
def robots_txt():
    lines = ["User-agent: *", f"Sitemap: {request.host_url.rstrip('/')}/sitemap.xml", "", "Disallow: /dashboard", "Disallow: /wallet", "Disallow: /hadi_path", "Disallow: /admin/"]
    r = make_response("\n".join(lines))
    r.headers["Content-Type"] = "text/plain"
    return r

@app.route('/sitemap.xml')
def sitemap():
    pages = [{'loc': f"{request.host_url.rstrip('/')}{u}", 'lastmod': datetime.now().strftime('%Y-%m-%d'), 'changefreq': 'weekly', 'priority': p} for u, p in [('/login', '1.0'), ('/register', '0.9')]]
    xml = render_template_string("""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{% for p in pages %}<url><loc>{{p.loc}}</loc><lastmod>{{p.lastmod}}</lastmod><changefreq>{{p.changefreq}}</changefreq><priority>{{p.priority}}</priority></url>{% endfor %}</urlset>""", pages=pages)
    r = make_response(xml); r.headers["Content-Type"] = "application/xml"
    return r

@app.errorhandler(Exception)
def handle_exception(e):
    if hasattr(e, 'code') and e.code in [400, 401, 403, 404, 500]:
        return render_template(f'{e.code}.html'), e.code
    db.session.rollback()
    return render_template('500.html'), 500

def initialize_database():
    with app.app_context():
        db.create_all()
        inspector = inspect(db.engine)
        cols = [c['name'] for c in inspector.get_columns('user')]
        with db.engine.connect() as conn:
            if 'new_f' not in cols: conn.execute(text("ALTER TABLE user ADD COLUMN new_f BOOLEAN DEFAULT FALSE"))
            if 'referral_code' not in cols: conn.execute(text("ALTER TABLE user ADD COLUMN referral_code VARCHAR(10)"))
            if 'preferred_currency' not in cols: conn.execute(text("ALTER TABLE user ADD COLUMN preferred_currency VARCHAR(3) DEFAULT 'PKR'"))
            conn.commit()
        for u in User.query.filter((User.referral_code == None) | (User.referral_code == '')).all():
            u.referral_code = generate_referral_code()
        db.session.commit()
        if not ExchangeRate.query.first(): sync_exchange_rates()

if __name__ == "__main__":
    initialize_database()
    app.run(debug=os.environ.get('FLASK_DEBUG', 'False').lower() == 'true', host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
