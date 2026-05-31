from flask import Flask, render_template, Response, request, jsonify, send_file, session, redirect, url_for, g  # pyright: ignore[reportMissingImports]
from flask_socketio import SocketIO, emit
import os
import base64
import io
import csv
import threading
import time
import hmac
import hashlib
import uuid
import tempfile
import matplotlib 
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import sqlite3
import bcrypt
import requests
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from model import generate_frames, generate_heatmap, generate_comparison_frames, stop_processing, get_analytics_data, apply_settings
from analysis_state import current_analysis
from chatbot import handle_chat_message

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
load_dotenv()
app.secret_key = os.environ.get('STORE_TRACKER_SECRET', 'store-tracker-dev-key')

DB_PATH = os.environ.get('STORE_TRACKER_DB_PATH', 'store_tracker.sqlite')
ADMIN_SUBSCRIPTION_AMOUNT_USD_CENTS = int(
    os.environ.get('ADMIN_SUBSCRIPTION_AMOUNT_USD_CENTS', '2100')
)
ADMIN_SUBSCRIPTION_CURRENCY = 'USD'
RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', '')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '')
RAZORPAY_WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')

current_source_key = '0'
camera_stats_cache = {}
analytics_last_snapshot = {}
analytics_chart_cache = {}
SNAPSHOT_INTERVAL_SEC = 15
CHART_INTERVAL_SEC = int(os.environ.get('STORE_TRACKER_CHART_INTERVAL_SEC', '12'))
UPLOAD_DB_PATH = os.path.abspath(
    os.environ.get('STORE_TRACKER_UPLOAD_DB_PATH', r'E:\MinorProject\Deployee\upload.sqlite')
)
UPLOAD_TEMP_DIR = os.path.join(tempfile.gettempdir(), 'store_tracker_uploads')
ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv'}
UPLOAD_RETENTION_DAYS = int(os.environ.get('STORE_TRACKER_UPLOAD_RETENTION_DAYS', '7'))
MAX_UPLOAD_SIZE_MB = 500
db_video_cache = {}

MAX_CACHE_ENTRIES = 20
CACHE_TTL_SEC = 300

video_source = 0  # default webcam
current_mode = 'tracking'  # 'tracking' or 'heatmap'


def get_db_path():
    if os.path.isabs(DB_PATH):
        return DB_PATH
    return os.path.abspath(os.path.join(os.path.dirname(__file__), DB_PATH))


def get_upload_db_connection():
    conn = sqlite3.connect(UPLOAD_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_upload_db():
    if 'upload_db' not in g:
        g.upload_db = sqlite3.connect(UPLOAD_DB_PATH)
        g.upload_db.row_factory = sqlite3.Row
    return g.upload_db


def ensure_upload_temp_dir():
    os.makedirs(UPLOAD_TEMP_DIR, exist_ok=True)


def is_allowed_video(filename):
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_VIDEO_EXTENSIONS


def get_video_mimetype(filename):
    ext = os.path.splitext(filename.lower())[1]
    if ext == '.mp4':
        return 'video/mp4'
    if ext == '.avi':
        return 'video/x-msvideo'
    if ext == '.mov':
        return 'video/quicktime'
    if ext == '.mkv':
        return 'video/x-matroska'
    if ext == '.wmv':
        return 'video/x-ms-wmv'
    if ext == '.flv':
        return 'video/x-flv'
    return 'application/octet-stream'


def get_db_connection():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(get_db_path())
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def set_pending_admin_session(user_row):
    session['pending_admin_id'] = user_row['id']
    session['pending_admin_email'] = user_row['email']
    session['pending_admin_name'] = user_row['name']
    
def init_db():
    conn = get_db_connection()
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL DEFAULT 'viewer',
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS camera_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source TEXT NOT NULL UNIQUE,
                default_mode TEXT NOT NULL DEFAULT 'tracking',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source_key TEXT,
                mode TEXT,
                frame_count INTEGER,
                total_detections INTEGER,
                unique_customers INTEGER,
                active_detections INTEGER,
                queue_length INTEGER,
                crowd_level INTEGER,
                duration_seconds REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                user_email TEXT,
                action TEXT NOT NULL,
                detail TEXT,
                ip_address TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                provider_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_thresholds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL UNIQUE,
                max_customers INTEGER,
                min_customers INTEGER,
                max_queue_length INTEGER,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                alert_value INTEGER,
                threshold_value INTEGER,
                message TEXT,
                severity TEXT DEFAULT 'medium',
                acknowledged INTEGER DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                hour_of_day INTEGER,
                customers_count INTEGER,
                queue_length INTEGER,
                crowd_level INTEGER,
                dwell_time_avg REAL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT 'default',
                alert_types TEXT DEFAULT 'queue,crowd,restricted',
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                recipients TEXT NOT NULL,
                schedule_type TEXT NOT NULL DEFAULT 'daily',
                enabled INTEGER DEFAULT 1,
                last_sent TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS push_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                token TEXT NOT NULL UNIQUE,
                platform TEXT DEFAULT 'web',
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Create indexes for performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_source_time ON analytics_history(source_key, captured_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_captured_at ON analytics_history(captured_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_source ON alerts(source_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_source_time ON analytics_snapshots(source_key, timestamp)")
    conn.close()
init_db()
ensure_upload_temp_dir()

def init_upload_db():
    conn = get_upload_db_connection()
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS uploaded_videos (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                content BLOB NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    conn.close()

init_upload_db()

def cleanup_expired_uploads():
    conn = None
    try:
        conn = get_upload_db_connection()
        with conn:
            conn.execute(
                "DELETE FROM uploaded_videos WHERE created_at < datetime('now', ?)",
                (f"-{UPLOAD_RETENTION_DAYS} days",)
            )
    finally:
        if conn:
            conn.close()

def store_uploaded_video(filename, content):
    video_id = uuid.uuid4().hex
    conn = None
    try:
        conn = get_upload_db_connection()
        with conn:
            conn.execute(
                "INSERT INTO uploaded_videos (id, filename, content) VALUES (?, ?, ?)",
                (video_id, filename, content)
            )
    finally:
        if conn:
            conn.close()
    return video_id


def fetch_uploaded_video(video_id):
    conn = None
    try:
        conn = get_upload_db_connection()
        row = conn.execute(
            "SELECT id, filename, content FROM uploaded_videos WHERE id = ?",
            (video_id,)
        ).fetchone()
        return row
    finally:
        if conn:
            conn.close()


def delete_uploaded_video(video_id):
    conn = None
    try:
        conn = get_upload_db_connection()
        with conn:
            conn.execute("DELETE FROM uploaded_videos WHERE id = ?", (video_id,))
    finally:
        if conn:
            conn.close()


def get_db_video_path(source_key):
    if source_key in db_video_cache:
        cached_path = db_video_cache[source_key]
        if os.path.exists(cached_path):
            return cached_path

    video_id = source_key.replace('db_', '', 1)
    row = fetch_uploaded_video(video_id)
    if not row:
        return None

    safe_name = secure_filename(row['filename'])
    temp_path = os.path.join(UPLOAD_TEMP_DIR, f"{source_key}_{safe_name}")
    with open(temp_path, 'wb') as handle:
        handle.write(row['content'])

    db_video_cache[source_key] = temp_path
    return temp_path


def cleanup_db_video(source_key):
    video_id = source_key.replace('db_', '', 1)
    temp_path = db_video_cache.pop(source_key, None)
    if temp_path and os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            pass
    delete_uploaded_video(video_id)


def log_audit_event(action, detail=None, user_id=None, user_email=None, ip_address=None):
    conn = None
    try:
        conn = get_db_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO audit_logs (user_id, user_email, action, detail, ip_address)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, user_email, action, detail, ip_address)
            )
    except Exception as exc:
        print(f"Audit log error: {exc}")
    finally:
        if conn:
            conn.close()


def should_snapshot(source_key):
    now = time.time()
    last = analytics_last_snapshot.get(source_key, 0)
    if (now - last) < SNAPSHOT_INTERVAL_SEC:
        return False
    analytics_last_snapshot[source_key] = now
    return True


def should_refresh_chart(source_key):
    now = time.time()
    cached = analytics_chart_cache.get(source_key)
    if not cached:
        return True
    return (now - cached.get('ts', 0)) >= CHART_INTERVAL_SEC


def _evict_cache(cache, max_entries=MAX_CACHE_ENTRIES, ttl=CACHE_TTL_SEC):
    now = time.time()
    expired = [k for k, v in cache.items() if (now - v.get('ts', 0)) > ttl]
    for k in expired:
        del cache[k]
    if len(cache) > max_entries:
        oldest = sorted(cache.keys(), key=lambda k: cache[k].get('ts', 0))
        for k in oldest[:len(cache) - max_entries]:
            del cache[k]


def record_analytics_snapshot(stats, source_key, mode):
    if not stats:
        return
    conn = None
    try:
        conn = get_db_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO analytics_history (
                    source_key,
                    mode,
                    frame_count,
                    total_detections,
                    unique_customers,
                    active_detections,
                    queue_length,
                    crowd_level,
                    duration_seconds
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_key,
                    mode,
                    stats.get('frameCount'),
                    stats.get('totalDetections'),
                    stats.get('uniqueCustomers'),
                    stats.get('activeDetections'),
                    stats.get('queueLength'),
                    stats.get('crowdLevel'),
                    stats.get('duration'),
                )
            )
    except Exception as exc:
        print(f"Analytics snapshot error: {exc}")
    finally:
        if conn:
            conn.close()


def check_and_create_alerts(source_key, stats):
    """Check thresholds and create alerts if exceeded"""
    if not stats:
        return
    conn = None
    try:
        conn = get_db_connection()
        threshold = conn.execute(
            "SELECT * FROM alert_thresholds WHERE source_key = ? AND enabled = 1",
            (source_key,)
        ).fetchone()
        if not threshold:
            return
        customers = stats.get('uniqueCustomers', 0)
        queue = stats.get('queueLength', 0)
        if threshold['max_customers'] and customers > threshold['max_customers']:
            create_alert(source_key, 'max_customers_exceeded', customers, threshold['max_customers'],
                f"Customers ({customers}) exceeded max ({threshold['max_customers']})", 'high')
        if threshold['min_customers'] and customers < threshold['min_customers']:
            create_alert(source_key, 'min_customers_threshold', customers, threshold['min_customers'],
                f"Customers ({customers}) below minimum ({threshold['min_customers']})", 'low')
        if threshold['max_queue_length'] and queue > threshold['max_queue_length']:
            create_alert(source_key, 'queue_length_exceeded', queue, threshold['max_queue_length'],
                f"Queue length ({queue}) exceeded max ({threshold['max_queue_length']})", 'medium')
    finally:
        if conn:
            conn.close()


def create_alert(source_key, alert_type, alert_value, threshold_value, message, severity='medium'):
    """Create a new alert"""
    conn = None
    try:
        conn = get_db_connection()
        with conn:
            conn.execute(
                "INSERT INTO alerts (source_key, alert_type, alert_value, threshold_value, message, severity) VALUES (?, ?, ?, ?, ?, ?)",
                (source_key, alert_type, alert_value, threshold_value, message, severity)
            )
    except Exception as exc:
        print(f"Alert creation error: {exc}")
    finally:
        if conn:
            conn.close()


def get_role():
    return session.get('role')


def get_paginated_results(query, params, page=1, per_page=50):
    """Helper function for pagination"""
    offset = (page - 1) * per_page
    conn = None
    try:
        conn = get_db_connection()
        count_query = f"SELECT COUNT(*) as cnt FROM ({query})"
        total = conn.execute(count_query, params).fetchone()['cnt']
        paginated_query = f"{query} LIMIT ? OFFSET ?"
        param_list = list(params) + [per_page, offset]
        rows = conn.execute(paginated_query, param_list).fetchall()
        return {
            'rows': [dict(row) for row in rows],
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page
        }
    finally:
        if conn:
            conn.close()


def calculate_peak_hours(source_key, days=7):
    """Calculate peak hours for a camera over last N days"""
    conn = None
    try:
        conn = get_db_connection()
        results = conn.execute(
            """
            SELECT 
                CAST(strftime('%H', captured_at) AS INTEGER) as hour,
                AVG(unique_customers) as avg_customers,
                MAX(unique_customers) as max_customers,
                COUNT(*) as sample_count
            FROM analytics_history
            WHERE source_key = ? AND captured_at > datetime('now', ?)
            GROUP BY hour
            ORDER BY avg_customers DESC
            """,
            (source_key, f"-{days} days")
        ).fetchall()
        return [dict(row) for row in results]
    finally:
        if conn:
            conn.close()


def calculate_dwell_time(source_key, days=7):
    """Estimate average dwell time from analytics"""
    conn = None
    try:
        conn = get_db_connection()
        results = conn.execute(
            """
            SELECT 
                AVG(CAST(active_detections AS FLOAT) / NULLIF(unique_customers, 0)) * 5 as avg_dwell_minutes,
                MAX(active_detections) as peak_active,
                AVG(unique_customers) as avg_customers
            FROM analytics_history
            WHERE source_key = ? AND captured_at > datetime('now', ?) AND unique_customers > 0
            """,
            (source_key, f"-{days} days")
        ).fetchone()
        return dict(results) if results else {}
    finally:
        if conn:
            conn.close()


def get_analytics_summary(source_key, range_type='daily'):
    """Get comprehensive analytics summary"""
    conn = None
    try:
        conn = get_db_connection()
        if range_type == 'hourly':
            time_range = "-1 hour"
        elif range_type == 'daily':
            time_range = "-1 day"
        elif range_type == 'weekly':
            time_range = "-7 days"
        else:
            time_range = "-30 days"
        summary = conn.execute(
            """
            SELECT 
                COUNT(*) as total_samples,
                AVG(unique_customers) as avg_customers,
                MAX(unique_customers) as peak_customers,
                MIN(unique_customers) as min_customers,
                AVG(queue_length) as avg_queue,
                MAX(queue_length) as max_queue,
                AVG(crowd_level) as avg_crowd_level,
                SUM(total_detections) as total_detections,
                ROUND(SUM(duration_seconds) / 3600.0, 1) as total_hours
            FROM analytics_history
            WHERE source_key = ? AND captured_at > datetime('now', ?)
            """,
            (source_key, time_range)
        ).fetchone()
        return dict(summary) if summary else {}
    finally:
        if conn:
            conn.close()


def require_login():
    return get_role() is not None


def get_pending_admin_user(pending_admin_id):
    if not pending_admin_id:
        return None
    conn = None
    try:
        conn = get_db_connection()
        return conn.execute(
            "SELECT id, name, email, role FROM users WHERE id = ?",
            (pending_admin_id,)
        ).fetchone()
    finally:
        if conn:
            conn.close()


@app.route('/subscription/manage', methods=['GET'])
def subscription_manage():
    if not require_login():
        return redirect(url_for('login'))
    user_id = session.get('user_id')
    conn = None
    sub = None
    try:
        conn = get_db_connection()
        sub = conn.execute(
            "SELECT id, provider, provider_id, status, created_at FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,)
        ).fetchone()
    finally:
        if conn:
            conn.close()

    return render_template('subscription_manage.html', subscription=sub, user_role=get_role())


@app.route('/subscription/cancel', methods=['POST'])
def subscription_cancel():
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401
    user_id = session.get('user_id')
    conn = None
    try:
        conn = get_db_connection()
        with conn:
            conn.execute(
                "UPDATE subscriptions SET status = ? WHERE user_id = ? AND status = ?",
                ('canceled', user_id, 'active')
            )
            conn.execute(
                "UPDATE users SET role = ? WHERE id = ? AND role = ?",
                ('viewer', user_id, 'admin')
            )
        log_audit_event('subscription_canceled', user_id=user_id, user_email=session.get('user_email'))
    finally:
        if conn:
            conn.close()
    return redirect(url_for('subscription_manage'))


@app.route('/subscription/success', methods=['GET'])
def subscription_success():
    if not require_login():
        return redirect(url_for('login'))
    return render_template('subscription_success.html', user_role=get_role())


def create_razorpay_order(user_id, email, amount_minor_units):
    payload = {
        "amount": amount_minor_units,
        "currency": ADMIN_SUBSCRIPTION_CURRENCY,
        "receipt": f"admin-{user_id}-{uuid.uuid4().hex[:10]}",
        "notes": {
            "email": email,
            "purpose": "admin_subscription"
        }
    }
    response = requests.post(
        "https://api.razorpay.com/v1/orders",
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


@app.route('/webhook/razorpay', methods=['POST'])
def razorpay_webhook():
    # Verify signature header
    signature = request.headers.get('X-Razorpay-Signature') or request.headers.get('x-razorpay-signature')
    raw_body = request.get_data() or b''
    if not RAZORPAY_WEBHOOK_SECRET:
        # Webhook secret not configured; reject
        return jsonify({'status': 'error', 'message': 'Webhook secret not configured'}), 500

    try:
        computed = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode('utf-8'),
            raw_body,
            hashlib.sha256
        ).hexdigest()
    except Exception as exc:
        print(f"Webhook HMAC compute error: {exc}")
        return jsonify({'status': 'error', 'message': 'Invalid webhook payload'}), 400

    if not signature or not hmac.compare_digest(computed, signature):
        # invalid signature
        print('Invalid razorpay webhook signature')
        return jsonify({'status': 'error', 'message': 'Invalid signature'}), 400

    payload = request.get_json(silent=True) or {}
    event = payload.get('event') or ''

    # Handle subscription related events
    try:
        conn = get_db_connection()
        with conn:
            # Extract identifiers safely
            # Many Razorpay webhook payloads put entities under payload.<object>.<entity>
            data = payload.get('payload', {})

            # Helper to find subscription id or payment id
            def _find_provider_ids(dct):
                sub_id = None
                payment_id = None
                try:
                    # subscription event
                    sub_entity = dct.get('subscription') or dct.get('subscription_entity')
                    if isinstance(sub_entity, dict):
                        sub_id = sub_entity.get('entity', {}).get('id') or sub_entity.get('id')
                except Exception:
                    pass
                try:
                    # payment event
                    pay_entity = dct.get('payment') or dct.get('payment_entity')
                    if isinstance(pay_entity, dict):
                        payment_id = pay_entity.get('entity', {}).get('id') or pay_entity.get('id')
                except Exception:
                    pass
                # invoice
                try:
                    inv_entity = dct.get('invoice')
                    if isinstance(inv_entity, dict):
                        if not payment_id:
                            payment_id = inv_entity.get('entity', {}).get('payment_id') or inv_entity.get('entity', {}).get('id')
                except Exception:
                    pass
                return sub_id, payment_id

            sub_id, payment_id = _find_provider_ids(data)

            # Map events to subscription updates
            if event in ('subscription.activated', 'subscription.created'):
                if sub_id:
                    # mark any matching subscription as active
                    conn.execute(
                        "UPDATE subscriptions SET status = ?, provider_id = ? WHERE provider = ? AND provider_id = ?",
                        ('active', sub_id, 'razorpay', sub_id)
                    )
            elif event in ('subscription.cancelled', 'subscription.halted'):
                # mark subscription canceled and demote user if necessary
                if sub_id:
                    row = conn.execute(
                        "SELECT id, user_id FROM subscriptions WHERE provider = ? AND provider_id = ?",
                        ('razorpay', sub_id)
                    ).fetchone()
                    if row:
                        conn.execute(
                            "UPDATE subscriptions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            ('canceled', row['id'])
                        )
                        # demote user to viewer
                        conn.execute(
                            "UPDATE users SET role = ? WHERE id = ? AND role = ?",
                            ('viewer', row['user_id'], 'admin')
                        )
                        log_audit_event('subscription_cancelled_webhook', user_id=row['user_id'], user_email=None, detail=f'provider_id={sub_id}', ip_address=request.remote_addr)
            elif event in ('invoice.paid', 'payment.captured'):
                # record payment against subscriptions if possible
                # If payment_id found, try to map to subscription by provider_id
                if payment_id:
                    # If there is a subscription row with provider_id = payment_id, mark active
                    row = conn.execute(
                        "SELECT id, user_id FROM subscriptions WHERE provider = ? AND provider_id = ?",
                        ('razorpay', payment_id)
                    ).fetchone()
                    if row:
                        conn.execute(
                            "UPDATE subscriptions SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            ('active', row['id'])
                        )
            # other events can be logged for audit
            log_audit_event('razorpay_webhook_received', detail=event, ip_address=request.remote_addr)
    except Exception as exc:
        print(f"Webhook handling error: {exc}")
        return jsonify({'status': 'error', 'message': 'Handler error'}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return jsonify({'status': 'ok'})


@app.route("/")
def home():
    if not require_login():
        return redirect(url_for('login'))
    return render_template("index.html", user_role=get_role())


@app.route('/chatbot/message', methods=['POST'])
def chatbot_message():
    try:
        return handle_chat_message(request)
    except Exception as exc:
        print(f"Chatbot handler error: {exc}")
        return jsonify({'reply': 'Sorry, chatbot error.'}), 500


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not email or not password:
            return render_template('login.html', error='Email and password are required.')

        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, name, email, role, password_hash FROM users WHERE email = ?",
                (email,)
            )
            user = cursor.fetchone()
        except Exception as exc:
            print(f"Login error: {exc}")
            return render_template('login.html', error='Login service unavailable.')
        finally:
            try:
                cursor.close()
                conn.close()
            except Exception:
                pass

        if not user:
            log_audit_event(
                'login_failed',
                detail=f"email={email}",
                user_email=email,
                ip_address=request.remote_addr
            )
            return render_template('login.html', error='Invalid credentials')

        stored_hash = user['password_hash'] if user else ''
        if not stored_hash:
            log_audit_event(
                'login_failed',
                detail=f"email={email}",
                user_email=email,
                ip_address=request.remote_addr
            )
            return render_template('login.html', error='Invalid credentials')

        if not bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8')):
            log_audit_event(
                'login_failed',
                detail=f"email={email}",
                user_email=email,
                ip_address=request.remote_addr
            )
            return render_template('login.html', error='Invalid credentials')

        if user['role'] == 'admin_pending':
            set_pending_admin_session(user)
            log_audit_event(
                'admin_payment_required',
                user_id=user['id'],
                user_email=user['email'],
                ip_address=request.remote_addr
            )
            return redirect(url_for('admin_subscribe'))

        session['role'] = user['role'] or 'viewer'
        session['user_id'] = user['id']
        session['user_name'] = user['name']
        session['user_email'] = user['email']
        log_audit_event(
            'login_success',
            user_id=user['id'],
            user_email=user['email'],
            ip_address=request.remote_addr
        )
        return redirect(url_for('home'))
    return render_template('login.html', error=None)


@app.route('/register', methods=['GET', 'POST'])
def register():
    can_create_admin = True
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        role = request.form.get('role', 'viewer').strip().lower() or 'viewer'
        requested_role = role

        if not name or not email or not password:
            return render_template(
                'register.html',
                error='All fields are required.',
                can_create_admin=can_create_admin,
                selected_role=role,
            )
        if password != confirm:
            return render_template(
                'register.html',
                error='Passwords do not match.',
                can_create_admin=can_create_admin,
                selected_role=role,
            )
        if role not in ['viewer', 'admin']:
            return render_template(
                'register.html',
                error='Invalid role selection.',
                can_create_admin=can_create_admin,
                selected_role='viewer',
            )

        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        conn = None
        cursor = None
        user_id = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            if role == 'admin':
                role = 'admin_pending'
            cursor.execute(
                "INSERT INTO users (name, email, role, password_hash) VALUES (?, ?, ?, ?)",
                (name, email, role, password_hash)
            )
            user_id = cursor.lastrowid
            conn.commit()
        except sqlite3.IntegrityError:
            return render_template(
                'register.html',
                error='Email already registered.',
                can_create_admin=can_create_admin,
                selected_role=requested_role,
            )
        except Exception as exc:
            print(f"Register error: {exc}")
            return render_template(
                'register.html',
                error='Registration failed. Try again.',
                can_create_admin=can_create_admin,
                selected_role=requested_role,
            )
        finally:
            try:
                cursor.close()
                conn.close()
            except Exception:
                pass

        log_audit_event(
            'register',
            user_id=user_id,
            user_email=email,
            ip_address=request.remote_addr
        )

        if role == 'admin_pending':
            set_pending_admin_session({'id': user_id, 'name': name, 'email': email})
            return redirect(url_for('admin_subscribe'))

        return redirect(url_for('login'))

    return render_template('register.html', error=None, can_create_admin=can_create_admin, selected_role='viewer')


@app.route('/admin/subscribe', methods=['GET'])
def admin_subscribe():
    pending_admin_id = session.get('pending_admin_id')
    user = get_pending_admin_user(pending_admin_id)
    if not user or user['role'] != 'admin_pending':
        return redirect(url_for('register'))

    amount_display = ADMIN_SUBSCRIPTION_AMOUNT_USD_CENTS / 100
    amount_minor_units = ADMIN_SUBSCRIPTION_AMOUNT_USD_CENTS

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return render_template(
            'admin_payment.html',
            error='Payment service is not configured. Contact support.',
            key_id='',
            order_id='',
            amount=amount_display,
            amount_minor_units=amount_minor_units,
            currency=ADMIN_SUBSCRIPTION_CURRENCY,
            name=user['name'],
            email=user['email'],
        )

    amount_cents = ADMIN_SUBSCRIPTION_AMOUNT_USD_CENTS
    try:
        order = create_razorpay_order(user['id'], user['email'], amount_cents)
    except Exception as exc:
        print(f"Razorpay order error: {exc}")
        return render_template(
            'admin_payment.html',
            error='Unable to start payment. Please try again later.',
            key_id='',
            order_id='',
            amount=amount_display,
            amount_minor_units=amount_minor_units,
            currency=ADMIN_SUBSCRIPTION_CURRENCY,
            name=user['name'],
            email=user['email'],
        )

    session['pending_admin_order_id'] = order.get('id')
    return render_template(
        'admin_payment.html',
        error=None,
        key_id=RAZORPAY_KEY_ID,
        order_id=order.get('id', ''),
        amount=amount_display,
        amount_minor_units=amount_minor_units,
        currency=ADMIN_SUBSCRIPTION_CURRENCY,
        name=user['name'],
        email=user['email'],
    )


@app.route('/admin/payment/verify', methods=['POST'])
def admin_payment_verify():
    pending_admin_id = session.get('pending_admin_id')
    expected_order_id = session.get('pending_admin_order_id')
    if not pending_admin_id or not expected_order_id:
        return jsonify({'status': 'error', 'message': 'No pending admin registration.'}), 400

    payload = request.get_json(silent=True) or {}
    payment_id = payload.get('razorpay_payment_id')
    order_id = payload.get('razorpay_order_id')
    signature = payload.get('razorpay_signature')

    if not payment_id or not order_id or not signature:
        return jsonify({'status': 'error', 'message': 'Incomplete payment response.'}), 400

    if order_id != expected_order_id:
        return jsonify({'status': 'error', 'message': 'Order mismatch.'}), 400

    if not RAZORPAY_KEY_SECRET:
        return jsonify({'status': 'error', 'message': 'Payment verification unavailable.'}), 500

    message = f"{order_id}|{payment_id}".encode('utf-8')
    expected_signature = hmac.new(
        RAZORPAY_KEY_SECRET.encode('utf-8'),
        message,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        return jsonify({'status': 'error', 'message': 'Payment verification failed.'}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn:
            conn.execute(
                "UPDATE users SET role = ? WHERE id = ? AND role = ?",
                ('admin', pending_admin_id, 'admin_pending')
            )
            user = conn.execute(
                "SELECT id, name, email, role FROM users WHERE id = ?",
                (pending_admin_id,)
            ).fetchone()

            # record subscription
            try:
                conn.execute(
                    "INSERT INTO subscriptions (user_id, provider, provider_id, status) VALUES (?, ?, ?, ?)",
                    (pending_admin_id, 'razorpay', payment_id, 'active')
                )
            except Exception:
                pass

        session['role'] = user['role'] if user else 'admin'
        session['user_id'] = user['id'] if user else pending_admin_id
        session['user_name'] = user['name'] if user else None
        session['user_email'] = user['email'] if user else session.get('pending_admin_email')
        session.pop('pending_admin_id', None)
        session.pop('pending_admin_email', None)
        session.pop('pending_admin_order_id', None)

        log_audit_event(
            'admin_subscription_paid',
            user_id=session.get('user_id'),
            user_email=session.get('user_email'),
            ip_address=request.remote_addr
        )
    finally:
        if conn:
            conn.close()

    return jsonify({'status': 'success', 'redirect': url_for('subscription_success')})


@app.route('/logout')
def logout():
    log_audit_event(
        'logout',
        user_id=session.get('user_id'),
        user_email=session.get('user_email'),
        ip_address=request.remote_addr
    )
    session.clear()
    return redirect(url_for('login'))


@app.route('/upload_video', methods=['POST'])
def upload_video():
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401

    if 'video' not in request.files:
        return jsonify({'status': 'error', 'message': 'No video provided'}), 400

    file = request.files['video']
    if not file or not file.filename:
        return jsonify({'status': 'error', 'message': 'Invalid file'}), 400

    filename = secure_filename(file.filename)
    if not is_allowed_video(filename):
        return jsonify({'status': 'error', 'message': 'Unsupported video format'}), 400

    content = file.read()
    if len(content) > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        return jsonify({'status': 'error', 'message': f'File too large. Max {MAX_UPLOAD_SIZE_MB}MB.'}), 400

    video_id = store_uploaded_video(filename, content)
    source_key = f"db_{video_id}"
    temp_path = os.path.join(UPLOAD_TEMP_DIR, f"{source_key}_{filename}")
    try:
        with open(temp_path, 'wb') as temp_file:
            temp_file.write(content)
        db_video_cache[source_key] = temp_path
    except OSError as exc:
        print(f"Upload cache write failed: {exc}")
    del content

    cleanup_expired_uploads()

    log_audit_event(
        'video_uploaded',
        detail=source_key,
        user_id=session.get('user_id'),
        user_email=session.get('user_email'),
        ip_address=request.remote_addr
    )

    return jsonify({'status': 'success', 'source': source_key})


@app.route('/api/uploads', methods=['GET'])
def list_uploads():
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401
    if get_role() != 'admin':
        return jsonify({'status': 'error', 'message': 'Admin access required'}), 403

    conn = None
    try:
        conn = get_upload_db_connection()
        rows = conn.execute(
            "SELECT id, filename, created_at FROM uploaded_videos ORDER BY created_at DESC"
        ).fetchall()
        payload = [
            {
                'id': row['id'],
                'filename': row['filename'],
                'created_at': row['created_at']
            }
            for row in rows
        ]
        return jsonify({'status': 'success', 'uploads': payload})
    finally:
        if conn:
            conn.close()


@app.route('/uploads/<source_key>', methods=['GET'])
def download_upload(source_key):
    if not require_login():
        return Response(status=401)
    if get_role() != 'admin':
        return Response(status=403)

    if not source_key.startswith('db_'):
        source_key = f"db_{source_key}"

    video_path = get_db_video_path(source_key)
    if not video_path or not os.path.exists(video_path):
        return Response(status=404)

    filename = os.path.basename(video_path)
    return send_file(video_path, mimetype=get_video_mimetype(filename), as_attachment=False)


@app.route('/video_feed')
def video_feed():
    if not require_login():
        return Response(status=401)
    global current_source_key
    source_param = request.args.get('source', '0')
    mode_param = request.args.get('mode', 'tracking')

    current_source = 0
    if source_param.isdigit():
        current_source = int(source_param)
        current_source_key = source_param
    else:
        video_path = None
        if source_param.startswith('db_'):
            video_path = get_db_video_path(source_param)
        else:
            video_path = os.path.join(UPLOAD_TEMP_DIR, source_param)
            if not os.path.exists(video_path):
                video_path = None

        if video_path and os.path.exists(video_path):
            current_source = video_path
            current_source_key = source_param
        else:
            print(f"Error: Video file not found at {video_path}")
            # Return an empty response or an error image
            return Response(status=404)

    if mode_param == 'heatmap':
        return Response(
            generate_heatmap(current_source),
            mimetype='multipart/x-mixed-replace; boundary=frame'
        )
    else:
        return Response(
            generate_frames(current_source),
            mimetype='multipart/x-mixed-replace; boundary=frame'
        )

@app.route('/comparison_feed')
def comparison_feed():
    if not require_login():
        return Response(status=401)
    source1 = request.args.get('source1', '0')
    source2 = request.args.get('source2', '1')

    def _resolve_source(param):
        if param.isdigit():
            return int(param)
        if param.startswith('db_'):
            path = get_db_video_path(param)
            if path and os.path.exists(path):
                return path
        return param

    s1 = _resolve_source(source1)
    s2 = _resolve_source(source2)
    return Response(
        generate_comparison_frames(s1, s2),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@app.route('/switch_mode/<mode>', methods=['POST'])
def switch_mode(mode):
    global current_mode
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401
    
    if mode in ['tracking', 'heatmap']:
        stop_processing()
        current_mode = mode
        return jsonify({'status': 'success', 'mode': current_mode})
    
    return jsonify({'status': 'error', 'message': 'Invalid mode'})


@app.route('/api/cameras', methods=['GET', 'POST'])
def cameras():
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401

    if request.method == 'GET':
        conn = None
        try:
            conn = get_db_connection()
            rows = conn.execute(
                "SELECT id, name, source, default_mode, enabled, created_at, updated_at FROM camera_sources ORDER BY id"
            ).fetchall()
            return jsonify({'status': 'success', 'cameras': [dict(row) for row in rows]})
        finally:
            if conn:
                conn.close()

    if get_role() != 'admin':
        return jsonify({'status': 'error', 'message': 'Admin access required'}), 403

    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name', '')).strip()
    source = str(payload.get('source', '')).strip()
    default_mode = payload.get('default_mode', 'tracking')
    enabled = 1 if payload.get('enabled', True) else 0

    if not name or not source:
        return jsonify({'status': 'error', 'message': 'Name and source are required'}), 400
    if default_mode not in ['tracking', 'heatmap']:
        return jsonify({'status': 'error', 'message': 'Invalid default mode'}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO camera_sources (name, source, default_mode, enabled)
                VALUES (?, ?, ?, ?)
                """,
                (name, source, default_mode, enabled)
            )
            camera_id = cursor.lastrowid
            row = conn.execute(
                "SELECT id, name, source, default_mode, enabled, created_at, updated_at FROM camera_sources WHERE id = ?",
                (camera_id,)
            ).fetchone()
        log_audit_event(
            'camera_created',
            detail=f"camera_id={camera_id}",
            user_id=session.get('user_id'),
            user_email=session.get('user_email'),
            ip_address=request.remote_addr
        )
        return jsonify({'status': 'success', 'camera': dict(row)})
    except sqlite3.IntegrityError:
        return jsonify({'status': 'error', 'message': 'Source already exists'}), 400
    finally:
        if conn:
            conn.close()


@app.route('/api/cameras/<int:camera_id>', methods=['PATCH', 'DELETE'])
def camera_detail(camera_id):
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401
    if get_role() != 'admin':
        return jsonify({'status': 'error', 'message': 'Admin access required'}), 403

    conn = None
    try:
        conn = get_db_connection()

        if request.method == 'DELETE':
            with conn:
                conn.execute("DELETE FROM camera_sources WHERE id = ?", (camera_id,))
            log_audit_event(
                'camera_deleted',
                detail=f"camera_id={camera_id}",
                user_id=session.get('user_id'),
                user_email=session.get('user_email'),
                ip_address=request.remote_addr
            )
            return jsonify({'status': 'success'})

        payload = request.get_json(silent=True) or {}
        fields = []
        values = []

        if 'name' in payload:
            name = str(payload.get('name', '')).strip()
            if not name:
                return jsonify({'status': 'error', 'message': 'Name cannot be empty'}), 400
            fields.append('name = ?')
            values.append(name)
        if 'source' in payload:
            source = str(payload.get('source', '')).strip()
            if not source:
                return jsonify({'status': 'error', 'message': 'Source cannot be empty'}), 400
            fields.append('source = ?')
            values.append(source)
        if 'default_mode' in payload:
            default_mode = payload.get('default_mode')
            if default_mode not in ['tracking', 'heatmap']:
                return jsonify({'status': 'error', 'message': 'Invalid default mode'}), 400
            fields.append('default_mode = ?')
            values.append(default_mode)
        if 'enabled' in payload:
            enabled = 1 if payload.get('enabled') else 0
            fields.append('enabled = ?')
            values.append(enabled)

        if not fields:
            return jsonify({'status': 'error', 'message': 'No fields to update'}), 400

        fields.append('updated_at = CURRENT_TIMESTAMP')
        values.append(camera_id)

        with conn:
            conn.execute(
                f"UPDATE camera_sources SET {', '.join(fields)} WHERE id = ?",
                values
            )
            row = conn.execute(
                "SELECT id, name, source, default_mode, enabled, created_at, updated_at FROM camera_sources WHERE id = ?",
                (camera_id,)
            ).fetchone()

        log_audit_event(
            'camera_updated',
            detail=f"camera_id={camera_id}",
            user_id=session.get('user_id'),
            user_email=session.get('user_email'),
            ip_address=request.remote_addr
        )
        return jsonify({'status': 'success', 'camera': dict(row) if row else None})
    except sqlite3.IntegrityError:
        return jsonify({'status': 'error', 'message': 'Source already exists'}), 400
    finally:
        if conn:
            conn.close()


@app.route('/stop', methods=['POST'])
def stop():
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401
    log_audit_event(
        'stop_processing',
        user_id=session.get('user_id'),
        user_email=session.get('user_email'),
        ip_address=request.remote_addr
    )
    stop_processing()
    return jsonify({'status': 'success'})


@app.route('/update_settings', methods=['POST'])
def update_settings():
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401
    if get_role() != 'admin':
        return jsonify({'status': 'error', 'message': 'Admin access required'}), 403
    payload = request.get_json(silent=True) or {}
    apply_settings(payload)
    log_audit_event(
        'update_settings',
        user_id=session.get('user_id'),
        user_email=session.get('user_email'),
        ip_address=request.remote_addr
    )
    return jsonify({'status': 'success'})


@app.route('/get_stats', methods=['GET'])
def get_stats():
    """Endpoint to get analytics data from the last run."""
    try:
        if not require_login():
            return jsonify({'status': 'error', 'message': 'Login required'}), 401
        source_key = request.args.get('source')
        if source_key and source_key != current_source_key:
            cached = camera_stats_cache.get(source_key)
            if cached:
                if should_snapshot(source_key):
                    record_analytics_snapshot(cached.get('stats'), source_key, current_mode)
                    check_and_create_alerts(source_key, cached.get('stats'))
                return jsonify(cached)

        stats = current_analysis.get_stats()
        source_for_chart = source_key or current_source_key
        img_base64 = None
        if stats.get('frameCount', 0) > 0 and should_refresh_chart(source_for_chart):
            all_track_ids = current_analysis.all_track_ids
            all_positions = [pos for history in current_analysis.track_history.values() for pos in history]
            unique_positions = set(all_positions)
            frame_count = stats['frameCount']
            total_detections = stats['totalDetections']
            duration = stats['duration']

            def _gen_chart(fc, td, ati, up, dur, src):
                try:
                    chart = get_analytics_data(fc, td, ati, up, dur)
                    _evict_cache(analytics_chart_cache)
                    analytics_chart_cache[src] = {'ts': time.time(), 'image': chart}
                except Exception as e:
                    print(f"Background chart error: {e}")

            threading.Thread(target=_gen_chart, args=(frame_count, total_detections, all_track_ids, unique_positions, duration, source_for_chart), daemon=True).start()
            cached_chart = analytics_chart_cache.get(source_for_chart)
            if cached_chart:
                img_base64 = cached_chart.get('image')
        else:
            cached_chart = analytics_chart_cache.get(source_for_chart)
            if cached_chart:
                img_base64 = cached_chart.get('image')

        payload = {
            'status': 'success',
            'image': img_base64,
            'stats': stats,
            'alerts': list(current_analysis.alerts)[-10:],
            'lastAlertId': current_analysis.alerts[-1]['id'] if current_analysis.alerts else 0
        }

        if source_key:
            _evict_cache(camera_stats_cache)
            camera_stats_cache[source_key] = payload

        source_for_snapshot = source_key or current_source_key
        if should_snapshot(source_for_snapshot):
            record_analytics_snapshot(stats, source_for_snapshot, current_mode)

        if img_base64:
            return jsonify(payload)

        payload['image'] = None
        payload['message'] = 'Not enough data for chart yet.'
        return jsonify(payload)
    except Exception as e:
        print(f"Error getting stats: {e}")
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/api/analytics_history', methods=['GET'])
def analytics_history():
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401
    limit = request.args.get('limit', '100')
    source_key = request.args.get('source')
    try:
        limit = max(1, min(int(limit), 1000))
    except ValueError:
        limit = 100

    conn = None
    try:
        conn = get_db_connection()
        if source_key:
            rows = conn.execute(
                """
                SELECT id, captured_at, source_key, mode, frame_count, total_detections,
                       unique_customers, active_detections, queue_length, crowd_level, duration_seconds
                FROM analytics_history
                WHERE source_key = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (source_key, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, captured_at, source_key, mode, frame_count, total_detections,
                       unique_customers, active_detections, queue_length, crowd_level, duration_seconds
                FROM analytics_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()
        return jsonify({'status': 'success', 'history': [dict(row) for row in rows]})
    finally:
        if conn:
            conn.close()


@app.route('/api/audit_logs', methods=['GET'])
def audit_logs():
    if not require_login():
        return jsonify({'status': 'error', 'message': 'Login required'}), 401
    if get_role() != 'admin':
        return jsonify({'status': 'error', 'message': 'Admin access required'}), 403
    limit = request.args.get('limit', '200')
    try:
        limit = max(1, min(int(limit), 1000))
    except ValueError:
        limit = 200

    conn = None
    try:
        conn = get_db_connection()
        rows = conn.execute(
            """
            SELECT id, created_at, user_id, user_email, action, detail, ip_address
            FROM audit_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return jsonify({'status': 'success', 'logs': [dict(row) for row in rows]})
    finally:
        if conn:
            conn.close()


@app.route('/get_last_heatmap', methods=['GET'])
def get_last_heatmap():
    try:
        if not require_login():
            return jsonify({'status': 'error', 'message': 'Login required'}), 401
        overlay_path = current_analysis.last_heatmap_overlay_path
        heatmap_path = current_analysis.last_heatmap_path

        image_path = overlay_path or heatmap_path
        if not image_path or not os.path.exists(image_path):
            return jsonify({'status': 'error', 'message': 'No saved heatmap found.'})

        with open(image_path, 'rb') as handle:
            img_base64 = base64.b64encode(handle.read()).decode('utf-8')

        return jsonify({
            'status': 'success',
            'image': img_base64,
            'filename': os.path.basename(image_path),
            'type': 'overlay' if overlay_path else 'heatmap'
        })
    except Exception as e:
        print(f"Error getting heatmap: {e}")
        return jsonify({'status': 'error', 'message': str(e)})


def build_report_summary():
    history = current_analysis.metric_history
    if not history:
        return {
            'duration': current_analysis.duration,
            'peak_active': 0,
            'peak_queue': 0,
            'peak_time': None,
            'avg_active': 0,
            'avg_queue': 0
        }

    peak_point = max(history, key=lambda entry: entry['active'])
    peak_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(peak_point['ts']))
    avg_active = sum(item['active'] for item in history) / len(history)
    avg_queue = sum(item['queue'] for item in history) / len(history)
    return {
        'duration': current_analysis.duration,
        'peak_active': peak_point['active'],
        'peak_queue': max(item['queue'] for item in history),
        'peak_time': peak_time,
        'avg_active': avg_active,
        'avg_queue': avg_queue
    }


def parse_sqlite_timestamp(ts_value):
    if not ts_value:
        return None
    if isinstance(ts_value, (int, float)):
        return float(ts_value)
    try:
        return time.mktime(time.strptime(ts_value, '%Y-%m-%d %H:%M:%S'))
    except Exception:
        return None


def fetch_history_rows(report_range, source_key=None, start_date=None, end_date=None):
    range_map = {
        'daily': '-1 day',
        'weekly': '-7 day'
    }
    if start_date and end_date:
        time_filter = None
    else:
        range_key = report_range if report_range in range_map else 'daily'
        time_filter = range_map[range_key]
    conn = None
    try:
        conn = get_db_connection()
        base_query = """
            SELECT id, captured_at, source_key, mode, frame_count, total_detections,
                   unique_customers, active_detections, queue_length, crowd_level, duration_seconds
            FROM analytics_history
            WHERE 1=1
        """
        params = []
        if source_key:
            base_query += " AND source_key = ?"
            params.append(source_key)
        if time_filter:
            base_query += " AND captured_at >= datetime('now', ?)"
            params.append(time_filter)
        elif start_date and end_date:
            base_query += " AND captured_at BETWEEN ? AND ?"
            params.extend([start_date, end_date])
        base_query += " ORDER BY captured_at ASC"
        rows = conn.execute(base_query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        if conn:
            conn.close()


def build_report_summary_from_history(rows):
    if not rows:
        return {
            'duration': 0,
            'peak_active': 0,
            'peak_queue': 0,
            'peak_time': None,
            'avg_active': 0,
            'avg_queue': 0,
            'unique_customers': 0,
            'total_detections': 0,
            'frame_count': 0,
            'latest_crowd': 0,
            'latest_queue': 0
        }

    active_values = [row.get('active_detections') or 0 for row in rows]
    queue_values = [row.get('queue_length') or 0 for row in rows]
    peak_index = max(range(len(rows)), key=lambda i: active_values[i])
    peak_time = rows[peak_index].get('captured_at')
    first_ts = parse_sqlite_timestamp(rows[0].get('captured_at'))
    last_ts = parse_sqlite_timestamp(rows[-1].get('captured_at'))
    duration = (last_ts - first_ts) if first_ts and last_ts and last_ts >= first_ts else 0

    return {
        'duration': duration,
        'peak_active': max(active_values),
        'peak_queue': max(queue_values),
        'peak_time': peak_time,
        'avg_active': (sum(active_values) / len(active_values)) if active_values else 0,
        'avg_queue': (sum(queue_values) / len(queue_values)) if queue_values else 0,
        'unique_customers': max(row.get('unique_customers') or 0 for row in rows),
        'total_detections': max(row.get('total_detections') or 0 for row in rows),
        'frame_count': max(row.get('frame_count') or 0 for row in rows),
        'latest_crowd': rows[-1].get('crowd_level') or 0,
        'latest_queue': rows[-1].get('queue_length') or 0
    }


def build_alert_summary(max_alerts=10):
    alerts = list(current_analysis.alerts) if current_analysis.alerts else []
    level_counts = {'warning': 0, 'error': 0}
    for alert in alerts:
        level = alert.get('level')
        if level in level_counts:
            level_counts[level] += 1
    return {
        'total': len(alerts),
        'warning': level_counts['warning'],
        'error': level_counts['error'],
        'recent': alerts[-max_alerts:]
    }


def format_alert_time(ts):
    try:
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
    except Exception:
        return 'N/A'


@app.route('/export_report', methods=['GET'])
def export_report():
    if not require_login():
        return redirect(url_for('login'))
    report_range = request.args.get('range', 'daily')
    fmt = request.args.get('format', 'csv')
    source_key = request.args.get('source')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    history_rows = fetch_history_rows(report_range, source_key, start_date, end_date)
    use_history = len(history_rows) > 0

    if use_history:
        summary = build_report_summary_from_history(history_rows)
        stats = {
            'uniqueCustomers': summary['unique_customers'],
            'totalDetections': summary['total_detections'],
            'frameCount': summary['frame_count'],
            'crowdLevel': summary['latest_crowd'],
            'queueLength': summary['latest_queue']
        }
        alert_summary = {'total': 0, 'warning': 0, 'error': 0, 'recent': []}
    else:
        stats = current_analysis.get_stats()
        summary = build_report_summary()
        alert_summary = build_alert_summary()

    if fmt == 'pdf':
        fig = plt.figure(figsize=(11, 6.5))
        gs = fig.add_gridspec(2, 2, height_ratios=[3.2, 1.3], width_ratios=[1.35, 1])
        ax = fig.add_subplot(gs[0, :])
        summary_ax = fig.add_subplot(gs[1, 0])
        alerts_ax = fig.add_subplot(gs[1, 1])
        summary_ax.axis('off')
        alerts_ax.axis('off')
        if use_history:
            timestamps = [parse_sqlite_timestamp(row.get('captured_at')) for row in history_rows]
            timestamps = [ts for ts in timestamps if ts is not None]
            if timestamps:
                start_ts = timestamps[0]
                rel_time = [(ts - start_ts) / 60 for ts in timestamps]
                active_series = [row.get('active_detections') or 0 for row in history_rows]
                queue_series = [row.get('queue_length') or 0 for row in history_rows]
                ax.plot(rel_time, active_series, label='Active', color='#2563eb')
                ax.plot(rel_time, queue_series, label='Queue', color='#f97316')
        else:
            history = current_analysis.metric_history
            if history:
                timestamps = [entry['ts'] for entry in history]
                start_ts = timestamps[0]
                rel_time = [(ts - start_ts) / 60 for ts in timestamps]
                active_series = [entry['active'] for entry in history]
                queue_series = [entry['queue'] for entry in history]
                ax.plot(rel_time, active_series, label='Active', color='#2563eb')
                ax.plot(rel_time, queue_series, label='Queue', color='#f97316')

        ax.set_xlabel('Minutes')
        ax.set_ylabel('People')
        ax.legend()
        ax.grid(alpha=0.2)

        ax.set_title('Live Activity Over Time', fontsize=12, fontweight='bold')

        fig.suptitle(f"Store Analytics Report ({report_range.title()})", fontsize=15, fontweight='bold')
        data_source_label = 'History snapshots' if use_history else 'Live session'
        avg_det_per_customer = stats['totalDetections'] / max(stats['uniqueCustomers'], 1)
        summary_lines = [
            f"Data Source: {data_source_label}",
            f"Duration: {summary['duration']:.1f}s",
            f"Unique Customers: {stats['uniqueCustomers']}",
            f"Total Detections: {stats['totalDetections']}",
            f"Crowd Level (latest): {stats['crowdLevel']}",
            f"Queue Length (latest): {stats['queueLength']}",
            f"Peak Active: {summary['peak_active']} @ {summary['peak_time']}",
            f"Avg Active: {summary['avg_active']:.1f}",
            f"Peak Queue: {summary['peak_queue']}",
            f"Avg Queue: {summary['avg_queue']:.1f}",
            f"Avg Detections per Customer: {avg_det_per_customer:.2f}"
        ]
        summary_ax.text(0.0, 1.0, "Summary", fontsize=11, fontweight='bold', va='top')
        summary_ax.text(0.0, 0.9, "\n".join(summary_lines), fontsize=9.5, va='top')
        if use_history:
            alert_lines = ["Alerts: not available for history reports"]
        else:
            alert_lines = [
                f"Alerts Total: {alert_summary['total']} (warning: {alert_summary['warning']}, error: {alert_summary['error']})"
            ]
            for alert in alert_summary['recent'][-5:]:
                alert_lines.append(
                    f"{format_alert_time(alert.get('ts'))} | {alert.get('level')} | {alert.get('message')}"
                )
        alerts_ax.text(0.0, 1.0, "Alerts", fontsize=11, fontweight='bold', va='top')
        alerts_ax.text(0.0, 0.9, "\n".join(alert_lines), fontsize=9.2, va='top')
        buf = io.BytesIO()
        fig.tight_layout(rect=[0.02, 0.02, 0.98, 0.94])
        fig.savefig(buf, format='pdf')
        plt.close(fig)
        buf.seek(0)
        filename = f"report_{report_range}_{int(time.time())}.pdf"
        return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=filename)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f"Store Analytics Report ({report_range.title()})"])
    writer.writerow([])
    writer.writerow(["Data Source", "History snapshots" if use_history else "Live session"])
    writer.writerow(["Duration (s)", f"{summary['duration']:.1f}"])
    writer.writerow(["Unique Customers", stats['uniqueCustomers']])
    writer.writerow(["Total Detections", stats['totalDetections']])
    writer.writerow(["Crowd Level (latest)", stats['crowdLevel']])
    writer.writerow(["Queue Length (latest)", stats['queueLength']])
    writer.writerow(["Peak Active", summary['peak_active']])
    writer.writerow(["Peak Queue", summary['peak_queue']])
    writer.writerow(["Peak Time", summary['peak_time'] or 'N/A'])
    writer.writerow(["Avg Active", f"{summary['avg_active']:.2f}"])
    writer.writerow(["Avg Queue", f"{summary['avg_queue']:.2f}"])
    writer.writerow(["Avg Detections per Customer", f"{stats['totalDetections'] / max(stats['uniqueCustomers'], 1):.2f}"])
    writer.writerow([])

    if use_history:
        writer.writerow([
            "Captured At",
            "Active",
            "Queue",
            "Crowd Level",
            "Queue Length",
            "Unique Customers",
            "Total Detections",
            "Frame Count",
            "Duration (s)",
            "Source",
            "Mode"
        ])
        for row in history_rows:
            writer.writerow([
                row.get('captured_at'),
                row.get('active_detections'),
                row.get('queue_length'),
                row.get('crowd_level'),
                row.get('queue_length'),
                row.get('unique_customers'),
                row.get('total_detections'),
                row.get('frame_count'),
                row.get('duration_seconds'),
                row.get('source_key'),
                row.get('mode')
            ])
        writer.writerow([])
        writer.writerow(["Alerts", "Not available for history reports"])
    else:
        writer.writerow(["Alerts Total", alert_summary['total']])
        writer.writerow(["Alerts Warning", alert_summary['warning']])
        writer.writerow(["Alerts Error", alert_summary['error']])
        writer.writerow([])
        writer.writerow(["Time (s)", "Active", "Queue", "Crowd Level", "Queue Length"])

        if current_analysis.metric_history:
            start_ts = current_analysis.metric_history[0]['ts']
            for entry in current_analysis.metric_history:
                writer.writerow([
                    f"{entry['ts'] - start_ts:.1f}",
                    entry['active'],
                    entry['queue'],
                    entry['active'],
                    entry['queue']
                ])

        writer.writerow([])
        writer.writerow(["Alert Time", "Level", "Type", "Message"])
        for alert in alert_summary['recent']:
            writer.writerow([
                format_alert_time(alert.get('ts')),
                alert.get('level'),
                alert.get('type'),
                alert.get('message')
            ])

    csv_bytes = io.BytesIO(output.getvalue().encode('utf-8'))
    csv_bytes.seek(0)
    filename = f"report_{report_range}_{int(time.time())}.csv"
    return send_file(csv_bytes, mimetype='text/csv', as_attachment=True, download_name=filename)
@app.route('/api/analytics/summary', methods=['GET'])
def api_analytics_summary():
    """Get comprehensive analytics summary for a camera"""
    if not require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    source_key = request.args.get('source', '0')
    range_type = request.args.get('range', 'daily')
    summary = get_analytics_summary(source_key, range_type)
    peak_hours = calculate_peak_hours(source_key)
    dwell_time = calculate_dwell_time(source_key)
    return jsonify({'summary': summary, 'peak_hours': peak_hours, 'dwell_time': dwell_time, 'source_key': source_key, 'range': range_type})

@app.route('/api/analytics/peak_hours', methods=['GET'])
def api_peak_hours():
    """Get peak hours analysis"""
    if not require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    source_key = request.args.get('source', '0')
    days = int(request.args.get('days', '7'))
    peak_hours = calculate_peak_hours(source_key, days)
    return jsonify({'peak_hours': peak_hours, 'source_key': source_key, 'days': days})

@app.route('/api/analytics/dwell_time', methods=['GET'])
def api_dwell_time():
    """Get dwell time estimates"""
    if not require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    source_key = request.args.get('source', '0')
    days = int(request.args.get('days', '7'))
    dwell_data = calculate_dwell_time(source_key, days)
    return jsonify({'dwell_time': dwell_data, 'source_key': source_key, 'days': days})

@app.route('/api/alerts', methods=['GET'])
def api_get_alerts():
    """Get recent alerts with pagination"""
    if not require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    source_key = request.args.get('source')
    page = int(request.args.get('page', '1'))
    per_page = int(request.args.get('per_page', '50'))
    conn = None
    try:
        conn = get_db_connection()
        query = "SELECT * FROM alerts WHERE 1=1"
        params = []
        if source_key:
            query += " AND source_key = ?"
            params.append(source_key)
        query += " ORDER BY created_at DESC"
        result = get_paginated_results(query, params, page, per_page)
        return jsonify(result)
    finally:
        if conn:
            conn.close()

@app.route('/api/alerts/<int:alert_id>/acknowledge', methods=['POST'])
def api_acknowledge_alert(alert_id):
    """Mark alert as acknowledged"""
    if not require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    conn = None
    try:
        conn = get_db_connection()
        with conn:
            conn.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
        return jsonify({'success': True, 'message': 'Alert acknowledged'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        if conn:
            conn.close()

@app.route('/api/alert_thresholds', methods=['GET', 'POST'])
def api_alert_thresholds():
    """Get or update alert thresholds"""
    if not require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    source_key = request.args.get('source', '0')
    conn = None
    try:
        conn = get_db_connection()
        if request.method == 'GET':
            threshold = conn.execute("SELECT * FROM alert_thresholds WHERE source_key = ?", (source_key,)).fetchone()
            if not threshold:
                return jsonify({'source_key': source_key, 'enabled': False})
            return jsonify(dict(threshold))
        data = request.get_json()
        with conn:
            existing = conn.execute("SELECT id FROM alert_thresholds WHERE source_key = ?", (source_key,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE alert_thresholds SET max_customers = ?, min_customers = ?, max_queue_length = ?, enabled = ? WHERE source_key = ?",
                    (data.get('max_customers'), data.get('min_customers'), data.get('max_queue_length'), data.get('enabled', 1), source_key)
                )
            else:
                conn.execute(
                    "INSERT INTO alert_thresholds (source_key, max_customers, min_customers, max_queue_length, enabled) VALUES (?, ?, ?, ?, ?)",
                    (source_key, data.get('max_customers'), data.get('min_customers'), data.get('max_queue_length'), data.get('enabled', 1))
                )
        return jsonify({'success': True, 'message': 'Thresholds updated', 'source_key': source_key})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        if conn:
            conn.close()

@app.route('/api/webhooks', methods=['GET', 'POST'])
def api_webhooks():
    if not require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    if get_role() != 'admin':
        return jsonify({'error': 'Admin access required'}), 403
    conn = None
    try:
        conn = get_db_connection()
        if request.method == 'GET':
            rows = conn.execute("SELECT * FROM webhook_configs ORDER BY id").fetchall()
            return jsonify({'webhooks': [dict(r) for r in rows]})
        data = request.get_json(silent=True) or {}
        url = data.get('url', '').strip()
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        name = data.get('name', 'default')
        alert_types = data.get('alert_types', 'queue,crowd,restricted')
        enabled = 1 if data.get('enabled', True) else 0
        with conn:
            conn.execute(
                "INSERT INTO webhook_configs (url, name, alert_types, enabled) VALUES (?, ?, ?, ?)",
                (url, name, alert_types, enabled)
            )
        _refresh_webhook_urls()
        log_audit_event('webhook_created', detail=url, user_id=session.get('user_id'), user_email=session.get('user_email'), ip_address=request.remote_addr)
        return jsonify({'success': True, 'message': 'Webhook added'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        if conn:
            conn.close()

@app.route('/api/webhooks/<int:webhook_id>', methods=['DELETE', 'PATCH'])
def api_webhook_detail(webhook_id):
    if not require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    if get_role() != 'admin':
        return jsonify({'error': 'Admin access required'}), 403
    conn = None
    try:
        conn = get_db_connection()
        if request.method == 'DELETE':
            with conn:
                conn.execute("DELETE FROM webhook_configs WHERE id = ?", (webhook_id,))
            _refresh_webhook_urls()
            return jsonify({'success': True})
        data = request.get_json(silent=True) or {}
        fields = []
        values = []
        if 'url' in data:
            fields.append('url = ?')
            values.append(data['url'])
        if 'name' in data:
            fields.append('name = ?')
            values.append(data['name'])
        if 'alert_types' in data:
            fields.append('alert_types = ?')
            values.append(data['alert_types'])
        if 'enabled' in data:
            fields.append('enabled = ?')
            values.append(1 if data['enabled'] else 0)
        if fields:
            fields.append('id = ?')
            values.append(webhook_id)
            with conn:
                conn.execute(f"UPDATE webhook_configs SET {', '.join(fields)} WHERE id = ?", values)
            _refresh_webhook_urls()
        return jsonify({'success': True})
    finally:
        if conn:
            conn.close()

def _refresh_webhook_urls():
    conn = None
    try:
        conn = get_db_connection()
        rows = conn.execute("SELECT url FROM webhook_configs WHERE enabled = 1").fetchall()
        current_analysis.webhook_urls = [r['url'] for r in rows]
    except Exception:
        current_analysis.webhook_urls = []
    finally:
        if conn:
            conn.close()

_refresh_webhook_urls()

@app.route('/api/email-schedules', methods=['GET', 'POST'])
def api_email_schedules():
    if not require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    if get_role() != 'admin':
        return jsonify({'error': 'Admin access required'}), 403
    conn = None
    try:
        conn = get_db_connection()
        if request.method == 'GET':
            rows = conn.execute("SELECT * FROM email_schedules WHERE user_id = ?", (session.get('user_id'),)).fetchall()
            return jsonify({'schedules': [dict(r) for r in rows]})
        data = request.get_json(silent=True) or {}
        recipients = data.get('recipients', '').strip()
        if not recipients:
            return jsonify({'error': 'Recipients required'}), 400
        schedule_type = data.get('schedule_type', 'daily')
        enabled = 1 if data.get('enabled', True) else 0
        with conn:
            conn.execute(
                "INSERT INTO email_schedules (user_id, recipients, schedule_type, enabled) VALUES (?, ?, ?, ?)",
                (session.get('user_id'), recipients, schedule_type, enabled)
            )
        return jsonify({'success': True, 'message': 'Schedule created'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        if conn:
            conn.close()

@app.route('/api/email-schedules/<int:sid>', methods=['DELETE'])
def api_email_schedule_delete(sid):
    if not require_login() or get_role() != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    conn = None
    try:
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM email_schedules WHERE id = ? AND user_id = ?", (sid, session.get('user_id')))
        return jsonify({'success': True})
    finally:
        if conn:
            conn.close()

@app.route('/api/push/register', methods=['POST'])
def api_push_register():
    if not require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    token = data.get('token', '').strip()
    if not token:
        return jsonify({'error': 'Token required'}), 400
    platform = data.get('platform', 'web')
    conn = None
    try:
        conn = get_db_connection()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO push_tokens (user_id, token, platform) VALUES (?, ?, ?)",
                (session.get('user_id'), token, platform)
            )
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        if conn:
            conn.close()

@app.route('/api/push/unregister', methods=['POST'])
def api_push_unregister():
    if not require_login():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    token = data.get('token', '')
    conn = None
    try:
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM push_tokens WHERE token = ? AND user_id = ?", (token, session.get('user_id')))
        return jsonify({'success': True})
    finally:
        if conn:
            conn.close()

@socketio.on('connect')
def handle_connect():
    emit('connected', {'status': 'ok'})

@socketio.on('subscribe_source')
def handle_subscribe(data):
    source = data.get('source', '0')
    emit('subscribed', {'source': source})

def emit_stats_update():
    try:
        stats = current_analysis.get_stats()
        stats['alerts'] = list(current_analysis.alerts)[-5:]
        socketio.emit('stats_update', stats)
    except Exception:
        pass

if __name__ == "__main__":
    debug_mode = os.environ.get('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes', 'on')
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', os.environ.get('FLASK_PORT', '5000')))
    app.run(debug=debug_mode, host=host, port=port)
