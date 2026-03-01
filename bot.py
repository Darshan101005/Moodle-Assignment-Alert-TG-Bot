
import telebot
import telebot.apihelper
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ForceReply, ReplyKeyboardRemove,
)
import threading
import time
import json
import os
import sys
import logging
import smtplib
import io
import re
import html as html_mod
import random
import requests
from requests.adapters  import HTTPAdapter
from urllib3.util.retry  import Retry
from bs4 import BeautifulSoup
from email.mime.text      import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime             import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from Moodle import (
    IST, DATA_DIR,
    ensure_data_dir, ensure_user_dir, migrate_user_files,
    load_users, save_users,
    load_user_data, save_user_data,
    load_user_assignments, save_user_assignments,
    unix_to_ist, moodle_login, get_active_session,
    fetch_enrolled_courses, fetch_upcoming_assignments,
    detect_new_items, detect_new_assignments,
    fetch_course_sections, fetch_folder_files,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bot_config.json')


def load_config() -> dict:
    if not os.path.exists(_CONFIG_FILE):
        raise FileNotFoundError(f"bot_config.json not found at {_CONFIG_FILE}")
    with open(_CONFIG_FILE, 'r') as f:
        return json.load(f)


cfg           = load_config()
BOT_TOKEN     = cfg['bot_token']
SYNC_INTERVAL_ACTIVE = int(cfg.get('sync_interval_minutes', 10)) * 60
SYNC_INTERVAL_SLEEP  = int(cfg.get('sync_interval_night_minutes', 60)) * 60


def _get_sync_interval() -> int:
    h = datetime.now(tz=IST).hour
    if 1 <= h < 8:
        return SYNC_INTERVAL_SLEEP
    return SYNC_INTERVAL_ACTIVE

ADMIN_TELEGRAM_USERNAME = cfg.get('admin_telegram_username', 'Darshan_101005')
ADMIN_PASSWORD          = cfg.get('admin_password',          'Darshan.10102005')

_email_cfg    = cfg.get('email', {})
EMAIL_ENABLED = _email_cfg.get('enabled', False)
EMAIL_HOST    = _email_cfg.get('smtp_host', 'smtp.gmail.com')
EMAIL_PORT    = int(_email_cfg.get('smtp_port', 587))
EMAIL_USER    = _email_cfg.get('sender_email', '')
EMAIL_PASS    = _email_cfg.get('sender_password', '')
EMAIL_FROM    = _email_cfg.get('from_name', 'Moodle Monitor Bot')

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

try:
    _tg_retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=['GET', 'POST'],
        raise_on_status=False,
    )
except TypeError:
    _tg_retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        method_whitelist=['GET', 'POST'],
        raise_on_status=False,
    )
_tg_adapter = HTTPAdapter(max_retries=_tg_retry)
_tg_session = requests.Session()
_tg_session.mount('https://', _tg_adapter)
_tg_session.mount('http://',  _tg_adapter)
telebot.apihelper.CUSTOM_REQUEST_SENDER = lambda method, url, **kwargs: _tg_session.request(method, url, **{**{'timeout': 60}, **kwargs})

BOT_USERS_FILE = os.path.join(DATA_DIR, 'bot_users.json')


def _safe_answer(call_id, text: str = None, show_alert: bool = False):
    """answer_callback_query that silently ignores stale/expired queries."""
    try:
        bot.answer_callback_query(call_id, text=text, show_alert=show_alert)
    except Exception:
        pass

REMINDER_THRESHOLDS = [
    (24 * 3600, '24h'),
    (12 * 3600, '12h'),
    ( 6 * 3600,  '6h'),
    ( 3 * 3600,  '3h'),
    ( 1 * 3600,  '1h'),
    (30 *   60, '30m'),
    (10 *   60, '10m'),
]

_bot_users_lock = threading.Lock()
_reminders_lock = threading.Lock()
_users_lock     = threading.Lock()
_todos_lock     = threading.Lock()


def _build_html_email(title: str, content_html: str, footer_note: str = '') -> str:
    """Wrap content in a professional HTML email template."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body {{ margin:0; padding:0; background:#f0f2f5; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
  .wrapper {{ padding:32px 16px; }}
  .card {{ max-width:600px; margin:0 auto; background:#ffffff; border-radius:16px; overflow:hidden; box-shadow:0 8px 32px rgba(0,0,0,0.10); }}
  .header {{ background:linear-gradient(135deg,#ea580c 0%,#f97316 100%); padding:36px 40px 28px; }}
  .header-icon {{ margin-bottom:18px; text-align:center; }}
  .header-icon img {{ width:330px; max-width:80%; height:auto; display:inline-block; }}
  .header h1 {{ margin:0; color:#ffffff; font-size:22px; font-weight:700; letter-spacing:-0.3px; text-align:center; }}
  .header p {{ margin:6px 0 0; color:rgba(255,255,255,0.82); font-size:14px; text-align:center; }}
  .body {{ padding:32px 40px; }}
  .otp-box {{ background:linear-gradient(135deg,#fff7ed 0%,#ffedd5 100%); border:2px solid #fb923c; border-radius:14px; text-align:center; padding:28px 20px; margin:24px 0; }}
  .otp-label {{ font-size:12px; text-transform:uppercase; letter-spacing:1.5px; color:#ea580c; font-weight:700; margin-bottom:12px; }}
  .otp-code {{ font-size:48px; font-weight:800; letter-spacing:14px; color:#c2410c; font-family:'Courier New',monospace; line-height:1; }}
  .otp-expiry {{ margin-top:12px; font-size:13px; color:#888; }}
  .info-table {{ width:100%; border-collapse:collapse; margin:20px 0; }}
  .info-table tr td {{ padding:11px 0; border-bottom:1px solid #f0f0f0; font-size:14px; }}
  .info-table tr:last-child td {{ border-bottom:none; }}
  .info-table .lbl {{ color:#9ca3af; font-weight:600; text-transform:uppercase; font-size:11px; letter-spacing:0.8px; width:110px; }}
  .info-table .val {{ color:#1f2937; font-weight:500; }}
  .badge {{ display:inline-block; padding:3px 11px; border-radius:20px; font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; }}
  .badge-warn {{ background:#fef3c7; color:#92400e; }}
  .badge-danger {{ background:#fee2e2; color:#991b1b; }}
  .badge-success {{ background:#d1fae5; color:#065f46; }}
  .badge-info {{ background:#dbeafe; color:#1e40af; }}
  .badge-purple {{ background:#ffedd5; color:#9a3412; }}
  .highlight {{ background:linear-gradient(120deg,#fed7aa,#fdba74); padding:1px 7px; border-radius:5px; font-weight:700; color:#9a3412; }}
  .urgent {{ color:#dc2626; font-weight:700; }}
  .muted {{ color:#6b7280; font-size:13px; }}
  .divider {{ height:1px; background:#f3f4f6; margin:24px 0; }}
  .cta {{ display:block; background:linear-gradient(135deg,#ea580c,#f97316); color:#fff !important; text-decoration:none; padding:14px 32px; border-radius:10px; text-align:center; font-weight:700; font-size:15px; margin:24px 0; letter-spacing:0.2px; }}
  .note {{ background:#fff7ed; border-left:4px solid #fb923c; padding:12px 16px; border-radius:0 8px 8px 0; font-size:13px; color:#374151; margin:16px 0; }}
  .footer {{ background:#f9fafb; padding:20px 40px; text-align:center; color:#9ca3af; font-size:12px; border-top:1px solid #e5e7eb; }}
  .footer a {{ color:#ea580c; text-decoration:none; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="card">
    <div class="header">
      <div class="header-icon"><img src="https://i.ibb.co/YF62jxY3/moodle-logo.png" alt="Moodle" style="width:330px;max-width:80%;height:auto;display:inline-block;"></div>
      <h1>Moodle Monitor</h1>
      <p>{title}</p>
    </div>
    <div class="body">
      {content_html}
    </div>
    <div class="footer">
      {"<p>" + footer_note + "</p>" if footer_note else ""}
      <p>Sent by <strong>Moodle Monitor Bot</strong> &bull; Auto-generated, do not reply.</p>
    </div>
  </div>
</div>
</body>
</html>"""


def send_email(to_addr: str, subject: str, body: str, html: str = ''):
    """Send an email. Pass html= for rich HTML version (falls back to plain text)."""
    if not EMAIL_USER or not to_addr:
        return
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f'{EMAIL_FROM} <{EMAIL_USER}>'
        msg['To']      = to_addr
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        if html:
            msg.attach(MIMEText(html, 'html', 'utf-8'))
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=15) as s:
            s.starttls()
            s.login(EMAIL_USER, EMAIL_PASS)
            s.sendmail(EMAIL_USER, to_addr, msg.as_string())
        log.info("Email sent to %s — %s", to_addr, subject)
    except Exception as e:
        log.warning("Email failed to %s: %s", to_addr, e)
        raise


def generate_otp() -> str:
    return f"{random.randint(0, 999999):06d}"


def store_otp(chat_id, otp: str, email: str):
    with _otp_lock:
        _otp_store[chat_id] = {
            'otp':     otp,
            'email':   email,
            'expires': int(time.time()) + 600,   # 10-minute validity
        }
        _save_otp_store()


def verify_otp(chat_id, entered: str) -> bool:
    with _otp_lock:
        rec = _otp_store.get(chat_id)
        if not rec:
            return False
        if int(time.time()) > rec['expires']:
            _otp_store.pop(chat_id, None)
            _save_otp_store()
            return False
        if rec['otp'] == entered.strip():
            _otp_store.pop(chat_id, None)
            _save_otp_store()
            return True
        return False


def get_pending_otp_email(chat_id) -> str:
    with _otp_lock:
        return _otp_store.get(chat_id, {}).get('email', '')


def send_otp_email(to_addr: str, otp: str):
    """Send HTML OTP verification email (bypasses EMAIL_ENABLED — used to verify the address)."""
    if not EMAIL_USER or not to_addr:
        raise Exception("Email SMTP is not configured in bot_config.json.")
    content = f"""
    <p style="color:#374151;font-size:15px;margin-bottom:24px;">
      You requested to link <span class="highlight">{to_addr}</span> for
      <strong>Moodle Monitor Bot</strong> notifications.
      Enter the code below in Telegram to confirm your email address.
    </p>
    <div class="otp-box">
      <div class="otp-label">Your Verification Code</div>
      <div class="otp-code">{otp}</div>
      <div class="otp-expiry">Valid for <strong>10 minutes</strong> &bull; Do not share this code.</div>
    </div>
    <div class="note">
      ⚠️ If you did not request this, simply ignore this email.
      Your email will <strong>not</strong> be saved unless you enter the code.
    </div>
    """
    html_body = _build_html_email("Email Verification", content)
    plain = (
        f"Your Moodle Monitor Bot verification code is: {otp}\n\n"
        f"Valid for 10 minutes. Do not share this code.\n"
        f"If you did not request this, ignore this email."
    )
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = "🔐 Verify your email — Moodle Monitor Bot"
        msg['From']    = f'{EMAIL_FROM} <{EMAIL_USER}>'
        msg['To']      = to_addr
        msg.attach(MIMEText(plain, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=15) as s:
            s.starttls()
            s.login(EMAIL_USER, EMAIL_PASS)
            s.sendmail(EMAIL_USER, to_addr, msg.as_string())
        log.info("OTP email sent to %s", to_addr)
    except Exception as e:
        raise Exception(f"Could not send email: {e}")


def load_bot_users() -> dict:
    ensure_data_dir()
    if not os.path.exists(BOT_USERS_FILE):
        return {}
    with open(BOT_USERS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_bot_users(bu: dict):
    ensure_data_dir()
    with open(BOT_USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(bu, f, indent=4)


def get_moodle_username(chat_id) -> str | None:
    with _bot_users_lock:
        bu = load_bot_users()
    return bu.get(str(chat_id), {}).get('username')


def get_bot_user_info(chat_id) -> dict:
    with _bot_users_lock:
        bu = load_bot_users()
    return bu.get(str(chat_id), {})


NOTIF_TELEGRAM = 'telegram'
NOTIF_EMAIL    = 'email'
NOTIF_BOTH     = 'both'
NOTIF_LABELS   = {
    NOTIF_TELEGRAM: '📲 Telegram only',
    NOTIF_EMAIL:    '📧 Email only',
    NOTIF_BOTH:     '🔔 Both (Telegram + Email)',
}


def link_user(chat_id, moodle_username: str, email: str = ''):
    with _bot_users_lock:
        bu      = load_bot_users()
        existing = bu.get(str(chat_id), {})
        bu[str(chat_id)] = {
            'username':      moodle_username,
            'registered_at': existing.get('registered_at',
                             datetime.now(tz=IST).strftime('%d %b %Y, %I:%M %p')),
            'blocked':       existing.get('blocked', False),
            'email':         email or existing.get('email', ''),
            'notif_pref':    existing.get('notif_pref', NOTIF_BOTH),
        }
        save_bot_users(bu)


def get_notif_pref(chat_id) -> str:
    return get_bot_user_info(chat_id).get('notif_pref', NOTIF_BOTH)


def set_notif_pref(chat_id, pref: str):
    with _bot_users_lock:
        bu = load_bot_users()
        if str(chat_id) in bu:
            bu[str(chat_id)]['notif_pref'] = pref
            save_bot_users(bu)


def should_notify_telegram(chat_id) -> bool:
    p = get_notif_pref(chat_id)
    return p in (NOTIF_TELEGRAM, NOTIF_BOTH)


def should_notify_email(chat_id) -> bool:
    p = get_notif_pref(chat_id)
    return p in (NOTIF_EMAIL, NOTIF_BOTH)


def unlink_user(chat_id):
    with _bot_users_lock:
        bu = load_bot_users()
        bu.pop(str(chat_id), None)
        save_bot_users(bu)


def set_user_email(chat_id, email: str):
    with _bot_users_lock:
        bu = load_bot_users()
        if str(chat_id) in bu:
            bu[str(chat_id)]['email'] = email
            save_bot_users(bu)


def get_user_email(chat_id) -> str:
    return get_bot_user_info(chat_id).get('email', '')


def is_blocked(chat_id) -> bool:
    return get_bot_user_info(chat_id).get('blocked', False)


def set_blocked(chat_id, blocked: bool):
    with _bot_users_lock:
        bu = load_bot_users()
        if str(chat_id) in bu:
            bu[str(chat_id)]['blocked'] = blocked
            save_bot_users(bu)


def delete_all_user_data(chat_id, moodle_username: str):
    """Wipe every file for this user and remove from bot."""
    with _users_lock:
        users_db = load_users()
        users_db.pop(moodle_username, None)
        save_users(users_db)
    user_dir = os.path.join(DATA_DIR, moodle_username.upper())
    if os.path.isdir(user_dir):
        import shutil
        shutil.rmtree(user_dir, ignore_errors=True)
    unlink_user(chat_id)


def _reminder_path(username: str) -> str:
    return os.path.join(ensure_user_dir(username), 'reminders.json')


def load_reminders(username: str) -> dict:
    p = _reminder_path(username)
    if not os.path.exists(p):
        return {}
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_reminders(username: str, data: dict):
    with open(_reminder_path(username), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)


def get_reminder_state(username: str, event_id) -> dict:
    rs = load_reminders(username)
    return rs.get(str(event_id), {'sent': [], 'snoozed_until': None, 'muted': False})


def set_reminder_state(username: str, event_id, state: dict):
    with _reminders_lock:
        rs = load_reminders(username)
        rs[str(event_id)] = state
        save_reminders(username, rs)


def mark_reminder_sent(username: str, event_id, label: str):
    state = get_reminder_state(username, event_id)
    if label not in state.get('sent', []):
        state.setdefault('sent', []).append(label)
    set_reminder_state(username, event_id, state)


def snooze_assignment(username: str, event_id, snooze_seconds: int):
    state = get_reminder_state(username, event_id)
    state['snoozed_until'] = int(time.time()) + snooze_seconds
    set_reminder_state(username, event_id, state)


def mute_assignment(username: str, event_id):
    state = get_reminder_state(username, event_id)
    state['muted'] = True
    set_reminder_state(username, event_id, state)


def unmute_assignment(username: str, event_id):
    state = get_reminder_state(username, event_id)
    state['muted']         = False
    state['snoozed_until'] = None
    set_reminder_state(username, event_id, state)


def mark_complete_reminder(username: str, event_id):
    state              = get_reminder_state(username, event_id)
    state['muted']     = True
    state['completed'] = True
    set_reminder_state(username, event_id, state)
    assign_data = load_user_assignments(username)
    for a in assign_data.get('assignments', []):
        if str(a.get('event_id')) == str(event_id):
            a['completed'] = True
            break
    save_user_assignments(username, assign_data)


TODO_REMINDERS = [
    (24 * 3600, '24h'),
    (12 * 3600, '12h'),
    ( 6 * 3600,  '6h'),
    ( 3 * 3600,  '3h'),
    ( 1 * 3600,  '1h'),
    (30 *   60, '30m'),
    (10 *   60, '10m'),
]


def _todo_path(username: str) -> str:
    return os.path.join(ensure_user_dir(username), 'todos.json')


def load_todos(username: str) -> list:
    p = _todo_path(username)
    if not os.path.exists(p):
        return []
    with open(p, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('todos', []) if isinstance(data, dict) else data


def save_todos(username: str, todos: list):
    with open(_todo_path(username), 'w', encoding='utf-8') as f:
        json.dump({'todos': todos}, f, indent=4)


def _next_todo_id(todos: list) -> int:
    if not todos:
        return 1
    return max(t.get('id', 0) for t in todos) + 1


def _format_todo(t: dict, idx: int) -> str:
    check = '✅' if t.get('completed') else '⚪'
    due   = t.get('due_str', 'Today')
    return (
        f"{check} <b>#{idx}</b>  {t['title']}\n"
        f"     📅 Due: <b>{due}</b>"
    )


def _parse_indian_datetime(text: str):
    """Parse DD/MM/YYYY hh:mm AM/PM (Indian 12-hour format) and return IST datetime."""
    text = text.strip()
    for fmt in (
        '%d/%m/%Y %I:%M %p',
        '%d-%m-%Y %I:%M %p',
        '%d/%m/%Y %I:%M%p',
        '%d-%m-%Y %I:%M%p',
        '%d/%m/%Y %I:%M  %p',
        '%d-%m-%Y %I:%M  %p',
    ):
        try:
            dt = datetime.strptime(text, fmt)
            dt = dt.replace(tzinfo=IST)
            return dt
        except ValueError:
            continue
    return None
_conv_states: dict = {}
_conv_lock = threading.Lock()

S_IDLE             = 'idle'
S_SIGNUP_USER      = 'signup_user'
S_SIGNUP_PASS      = 'signup_pass'
S_RESET_PASS       = 'reset_pass'
S_SET_EMAIL        = 'set_email'
S_SET_EMAIL_OTP    = 'set_email_otp'
S_DELETE_PASS      = 'delete_pass'
S_CUSTOM_SNOOZE_DT = 'custom_snooze_dt'
S_ADMIN_PASS       = 'admin_pass'
S_ADMIN_TEXT       = 'admin_text'
S_TODO_TITLE       = 'todo_title'
S_TODO_DUE         = 'todo_due'
S_TODO_DELETE      = 'todo_delete'
S_TODO_EDIT_PICK   = 'todo_edit_pick'
S_TODO_EDIT_TITLE  = 'todo_edit_title'
S_TODO_EDIT_DUE    = 'todo_edit_due'
S_DL_COURSE        = 'dl_course'
S_DL_FILE          = 'dl_file'
S_BROADCAST_CUSTOM = 'broadcast_custom'

_otp_store: dict = {}
_otp_lock = threading.Lock()

_CONV_STATES_FILE = os.path.join(DATA_DIR, 'conv_states.json')
_OTP_STORE_FILE   = os.path.join(DATA_DIR, 'otp_store.json')


def _save_conv_states():
    """Persist conversation states to disk (called inside _conv_lock)."""
    try:
        serializable = {str(k): v for k, v in _conv_states.items()}
        with open(_CONV_STATES_FILE, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, indent=2)
    except Exception:
        pass


def _load_conv_states():
    """Restore conversation states from disk on startup."""
    global _conv_states
    if not os.path.exists(_CONV_STATES_FILE):
        return
    try:
        with open(_CONV_STATES_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        _conv_states = {int(k): v for k, v in raw.items()}
        log.info("Restored %d conversation states from disk.", len(_conv_states))
    except Exception as e:
        log.warning("Could not load conv_states: %s", e)


def _save_otp_store():
    """Persist OTP store to disk (called inside _otp_lock)."""
    try:
        serializable = {str(k): v for k, v in _otp_store.items()}
        with open(_OTP_STORE_FILE, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, indent=2)
    except Exception:
        pass


def _load_otp_store():
    """Restore OTP store from disk on startup (prune expired)."""
    global _otp_store
    if not os.path.exists(_OTP_STORE_FILE):
        return
    try:
        with open(_OTP_STORE_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        now = int(time.time())
        _otp_store = {int(k): v for k, v in raw.items() if v.get('expires', 0) > now}
        log.info("Restored %d pending OTPs from disk.", len(_otp_store))
    except Exception as e:
        log.warning("Could not load otp_store: %s", e)


def get_state(chat_id) -> dict:
    with _conv_lock:
        return _conv_states.get(chat_id, {'state': S_IDLE, 'data': {}})


def set_state(chat_id, state: str, data: dict = None):
    with _conv_lock:
        _conv_states[chat_id] = {'state': state, 'data': data or {}}
        _save_conv_states()


def clear_state(chat_id):
    set_state(chat_id, S_IDLE)


def is_admin_user(message) -> bool:
    uname = (message.from_user.username or '').lstrip('@').lower()
    return uname == ADMIN_TELEGRAM_USERNAME.lstrip('@').lower()


_admin_authed: set = set()
_ADMIN_AUTHED_FILE = os.path.join(DATA_DIR, 'admin_sessions.json')


def _save_admin_sessions():
    try:
        with open(_ADMIN_AUTHED_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(_admin_authed), f)
    except Exception:
        pass


def _load_admin_sessions():
    global _admin_authed
    if os.path.exists(_ADMIN_AUTHED_FILE):
        try:
            with open(_ADMIN_AUTHED_FILE, 'r', encoding='utf-8') as f:
                _admin_authed = set(json.load(f))
        except Exception:
            pass


def admin_is_authed(chat_id) -> bool:
    return int(chat_id) in _admin_authed


def assignment_inline_kb(event_id, is_muted=False, snoozed_until=None,
                         show_snooze=True) -> InlineKeyboardMarkup:
    now = int(time.time())
    kb  = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton('✅ Mark Complete', callback_data=f'done:{event_id}'),
    )
    if show_snooze:
        kb.add(
            InlineKeyboardButton('💤 Snooze',        callback_data=f'snooze_menu:{event_id}'),
            InlineKeyboardButton('⏰ Custom Snooze', callback_data=f'custom_snooze:{event_id}'),
        )
    kb.add(
        InlineKeyboardButton(
            '🔕 Mute All' if not is_muted else '🔔 Unmute',
            callback_data=f'mute:{event_id}' if not is_muted else f'unmute:{event_id}'
        ),
    )
    if snoozed_until and snoozed_until > now:
        kb.add(InlineKeyboardButton(
            f'Cancel Snooze (until {unix_to_ist(snoozed_until)})',
            callback_data=f'unsnooze:{event_id}'
        ))
    return kb


def snooze_options_kb(event_id, seconds_left: int = None) -> InlineKeyboardMarkup:
    """Build snooze keyboard. Only show options that fit within (seconds_left - 300)."""
    kb      = InlineKeyboardMarkup(row_width=3)
    all_options = [
        ('+15 min',  900),   ('+30 min',  1800),  ('+1 hr',   3600),
        ('+3 hrs',  10800),  ('+6 hrs',  21600),  ('+12 hrs', 43200),
        ('+24 hrs', 86400),
    ]
    if seconds_left is not None:
        max_snooze = seconds_left - 300   # must end 5 min before due
        options = [(l, s) for l, s in all_options if s < max_snooze]
    else:
        options = all_options

    if not options:
        kb.add(InlineKeyboardButton('⚠️ No snooze options available', callback_data='noop'))
    else:
        kb.add(*[InlineKeyboardButton(l, callback_data=f'snooze:{event_id}:{s}')
                 for l, s in options])
    kb.add(InlineKeyboardButton('⏰ Custom date/time', callback_data=f'custom_snooze:{event_id}'))
    kb.add(InlineKeyboardButton('« Back',             callback_data=f'snooze_back:{event_id}'))
    return kb


def admin_main_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton('👥 All Users',           callback_data='adm:users'),
        InlineKeyboardButton('📊 Stats',               callback_data='adm:stats'),
    )
    kb.add(
        InlineKeyboardButton('📋 User Assignments',    callback_data='adm:ask_assign'),
        InlineKeyboardButton('🔔 Notif Prefs',         callback_data='adm:notif_prefs'),
    )
    kb.add(
        InlineKeyboardButton('🚫 Block User',          callback_data='adm:ask_block'),
        InlineKeyboardButton('✅ Unblock User',        callback_data='adm:ask_unblock'),
    )
    kb.add(
        InlineKeyboardButton('🗑 Delete User',         callback_data='adm:ask_del'),
        InlineKeyboardButton('📢 Broadcast',           callback_data='adm:broadcast'),
    )
    return kb


_BROADCAST_TEMPLATES = {
    'maintenance': (
        '🛠 <b>Scheduled Maintenance</b>\n\n'
        'The bot is going down for maintenance. '
        'Services may be temporarily unavailable.\n'
        'We\'ll notify you once everything is back online. Thank you for your patience! 🙏'
    ),
    'back_online': (
        '✅ <b>Bot is Back Online!</b>\n\n'
        'Maintenance is complete and all services are running normally.\n'
        'Thank you for your patience! 🚀'
    ),
    'disruption': (
        '⚠️ <b>Service Disruption</b>\n\n'
        'We are experiencing some issues that may affect sync, reminders, or notifications.\n'
        'Our team is working on it. We\'ll update you once resolved. 🔧'
    ),
    'update': (
        '🆕 <b>Bot Update</b>\n\n'
        'A new update has been deployed! Expect improved performance and new features.\n'
        'Type /help to see what\'s new. 🎉'
    ),
}


def _broadcast_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton('🛠 Maintenance Notice',   callback_data='bcast:maintenance'),
        InlineKeyboardButton('✅ Back Online',           callback_data='bcast:back_online'),
        InlineKeyboardButton('⚠️ Service Disruption',    callback_data='bcast:disruption'),
        InlineKeyboardButton('🆕 Bot Update',            callback_data='bcast:update'),
        InlineKeyboardButton('✏️ Custom Message',        callback_data='bcast:custom'),
    )
    kb.add(InlineKeyboardButton('❌ Cancel', callback_data='bcast:cancel'))
    return kb


def _broadcast_confirm_kb(key: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton('📢 Send to All', callback_data=f'bcast_ok:{key}'),
        InlineKeyboardButton('❌ Cancel',      callback_data='bcast_ok:cancel'),
    )
    return kb


def _do_broadcast(chat_id: int, text: str):
    with _bot_users_lock:
        bu = load_bot_users()
    targets = [int(cid) for cid, info in bu.items()
               if not info.get('blocked')]
    sent = 0
    failed = 0
    for cid in targets:
        try:
            bot.send_message(cid, text, disable_web_page_preview=True, parse_mode='HTML')
            sent += 1
        except Exception as e:
            log.warning("Broadcast to %s failed: %s", cid, e)
            failed += 1
    bot.send_message(chat_id,
        f"📢 <b>Broadcast Complete</b>\n\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>\n"
        f"👥 Total active: <b>{len(targets)}</b>")


def notif_pref_kb(current_pref: str) -> InlineKeyboardMarkup:
    """Inline keyboard to pick notification channel."""
    kb = InlineKeyboardMarkup(row_width=1)
    for key, label in NOTIF_LABELS.items():
        tick = ' ✔' if key == current_pref else ''
        kb.add(InlineKeyboardButton(label + tick, callback_data=f'notifpref:{key}'))
    return kb


def _late_by_str(due_unix) -> str:
    if not due_unix:
        return ''
    delta = int(time.time()) - int(due_unix)
    if delta <= 0:
        return ''
    hours, _ = divmod(delta, 3600)
    days, hours = divmod(hours, 24)
    parts = []
    if days:  parts.append(f'{days}d')
    if hours: parts.append(f'{hours}h')
    if not parts:
        parts.append('< 1h')
    return ' '.join(parts)


def format_assignment_msg(a: dict, show_header=True) -> str:
    overdue_tag  = ' ⚠️ <b>OVERDUE</b>'   if a.get('overdue')   else ''
    if a.get('completed') and a.get('late'):
        complete_tag = ' 🟡 <b>Submitted Late</b>'
    elif a.get('completed'):
        complete_tag = ' ✅ <b>Completed</b>'
    else:
        complete_tag = ''
    header       = f"📋 <b>Assignment{overdue_tag}{complete_tag}</b>\n" if show_header else ''
    extra = ''
    if a.get('completed') and a.get('late'):
        extra += f"\n🕐 <b>Detected:</b> {a.get('completed_at', 'N/A')}"
        extra += f"\n⏱ <b>Late by:</b> {a.get('late_by', 'N/A')}"
    return (
        f"{header}"
        f"📚 <b>Course:</b> {a.get('course', 'N/A')}\n"
        f"📝 <b>Task:</b> {a.get('name', 'N/A')}\n"
        f"🟢 <b>Opened:</b> {a.get('opened', 'N/A')}\n"
        f"🔴 <b>Due:</b> {a.get('due', 'N/A')}\n"
        f"🔗 <a href=\"{a.get('submit_url', '#')}\">Submit here</a>"
        + extra
    )


def time_left_str(due_unix) -> str:
    if not due_unix:
        return ''
    delta = int(due_unix) - int(time.time())
    if delta <= 0:
        return '⚠️ Deadline passed'
    hours, rem   = divmod(delta, 3600)
    mins, _      = divmod(rem, 60)
    days, hours  = divmod(hours, 24)
    parts = []
    if days:  parts.append(f'{days}d')
    if hours: parts.append(f'{hours}h')
    if mins:  parts.append(f'{mins}m')
    if not parts:
        parts.append('< 1m')
    return '⏳ ' + ' '.join(parts) + ' left'


def _send_long(chat_id, text: str, edit_msg=None, reply_markup=None):
    MAX = 4000
    chunks = []
    while text:
        if len(text) <= MAX:
            chunks.append(text)
            break
        cut = text.rfind('\n', 0, MAX)
        if cut <= 0:
            cut = MAX
        chunks.append(text[:cut])
        text = text[cut:].lstrip('\n')
    for idx, chunk in enumerate(chunks):
        kw = dict(parse_mode='HTML', disable_web_page_preview=True)
        if idx == len(chunks) - 1 and reply_markup:
            kw['reply_markup'] = reply_markup
        if idx == 0 and edit_msg:
            try:
                bot.edit_message_text(chunk, edit_msg.chat.id, edit_msg.message_id, **kw)
                continue
            except Exception:
                pass
        try:
            bot.send_message(chat_id, chunk, **kw)
        except Exception:
            bot.send_message(chat_id, html_mod.escape(chunk), disable_web_page_preview=True)


def require_signup(func):
    def wrapper(message):
        if is_blocked(message.chat.id):
            bot.reply_to(message, "🚫 Your account has been blocked. Contact the admin.")
            return
        if not get_moodle_username(message.chat.id):
            bot.reply_to(message,
                "⚠️ You're not signed up yet.\nUse /signup to link your Moodle account.")
            return
        return func(message)
    return wrapper


def bot_get_session(username: str):
    with _users_lock:
        users_db = load_users()
    if username not in users_db:
        raise Exception(f"User {username} not found. Please /signup again.")
    password = users_db[username].get('password', '')
    if not password:
        raise Exception("No password stored. Use /reset_password.")
    return get_active_session(username, password, users_db)


def _extract_file_url_from_html(html_text: str) -> str:
    soup = BeautifulSoup(html_text, 'html.parser')
    source = soup.find('source', src=True)
    if source and 'pluginfile.php' in source.get('src', ''):
        return source['src']
    fallback_a = soup.select_one('a.mediafallbacklink[href]')
    if fallback_a and 'pluginfile.php' in fallback_a['href']:
        return fallback_a['href']
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'pluginfile.php' in href and 'forcedownload' in href:
            return href
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'pluginfile.php' in href:
            return href
    for tag in soup.find_all(['object', 'embed']):
        src = tag.get('data') or tag.get('src', '')
        if 'pluginfile.php' in src:
            return src
    iframe = soup.find('iframe', src=True)
    if iframe and 'pluginfile.php' in iframe.get('src', ''):
        return iframe['src']
    return ''


def _extract_actual_url(session, moodle_url: str) -> str:
    try:
        resp = session.get(moodle_url, timeout=30)
        soup = BeautifulSoup(resp.text, 'html.parser')
        wa = soup.select_one('.urlworkaround a[href]')
        if wa:
            return wa['href']
        iframe = soup.select_one('iframe[src]')
        if iframe:
            return iframe['src']
        meta = soup.find('meta', attrs={'http-equiv': 'refresh'})
        if meta:
            m = re.search(r'url=(.+)', meta.get('content', ''), re.I)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return ''


_EXT_MAP = {
    'application/pdf':   '.pdf',
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.ms-excel': '.xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/vnd.ms-powerpoint': '.ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'text/plain':  '.txt',
    'image/jpeg':  '.jpg',
    'image/png':   '.png',
    'image/gif':   '.gif',
    'video/mp4':   '.mp4',
    'video/webm':  '.webm',
    'audio/mpeg':  '.mp3',
    'application/zip': '.zip',
    'application/x-rar-compressed': '.rar',
    'application/x-7z-compressed':  '.7z',
}


def _send_file_from_response(resp, name: str, chat_id: int, course_name: str, fallback_url: str):
    content_type = resp.headers.get('Content-Type', '')
    content_disp = resp.headers.get('Content-Disposition', '')
    filename = name
    m = re.search(r'filename[*]?=["\']?([^"\';\n]+)', content_disp)
    if m:
        filename = m.group(1).strip().strip('"\'')
    for mime, ext in _EXT_MAP.items():
        if mime in content_type and not filename.lower().endswith(ext):
            filename += ext
            break
    MAX_BYTES = 49 * 1024 * 1024
    data = b''
    for chunk in resp.iter_content(chunk_size=65536):
        data += chunk
        if len(data) > MAX_BYTES:
            bot.send_message(chat_id,
                f"📄 <b>{name}</b> — too large to forward (>49 MB)\n"
                f"📚 {course_name}\n<a href=\"{fallback_url}\">Download manually</a>")
            return
    if not data:
        bot.send_message(chat_id,
            f"📄 <b>{name}</b> — empty file (0 bytes)\n"
            f"📚 {course_name}\n<a href=\"{fallback_url}\">Open in Moodle</a>")
        return
    file_obj = io.BytesIO(data)
    file_obj.name = filename
    bot.send_document(chat_id, file_obj,
                      caption=f"📄 <b>{name}</b>\n📚 {course_name}", parse_mode='HTML')
    log.info("Sent file '%s' (%d KB) to chat %s", filename, len(data) // 1024, chat_id)


_VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv', '.m4v'}
_VIDEO_MIMES = {'video/mp4', 'video/webm', 'video/x-matroska', 'video/avi',
                'video/quicktime', 'video/x-flv', 'video/x-ms-wmv'}

_SKIP_DOWNLOAD = {'forum', 'assign', 'assignment', 'quiz', 'feedback',
                  'choice', 'data', 'glossary', 'wiki', 'lesson',
                  'scorm', 'survey', 'workshop', 'chat', 'label',
                  'page', 'book', 'lti', 'h5pactivity', 'bigbluebuttonbn'}


def download_and_send_file(session, item: dict, chat_id: int, course_name: str):
    modname = (item.get('modname') or '').lower()
    url     = item.get('url', '')
    name    = item.get('name', 'file')

    if not url:
        return

    if modname == 'url':
        actual = _extract_actual_url(session, url)
        link = actual if actual else url
        bot.send_message(chat_id,
            f"🔗 <b>{name}</b>\n"
            f"📚 {course_name}\n<a href=\"{link}\">{link}</a>",
            disable_web_page_preview=False,
        )
        return

    if modname in _SKIP_DOWNLOAD:
        icon = '📋' if modname in ('assign', 'assignment') else '📌'
        bot.send_message(chat_id,
            f"{icon} <b>{name}</b>\n"
            f"📚 {course_name}\n<a href=\"{url}\">Open in Moodle</a>",
            disable_web_page_preview=True,
        )
        return

    if modname == 'folder':
        try:
            folder_files = fetch_folder_files(session, url)
            if not folder_files:
                bot.send_message(chat_id,
                    f"📁 <b>{name}</b> — empty folder\n"
                    f"📚 {course_name}\n<a href=\"{url}\">Open in Moodle</a>")
                return
            for ff in folder_files:
                try:
                    r = session.get(ff['url'], allow_redirects=True, timeout=60, stream=True)
                    _send_file_from_response(r, ff['name'], chat_id, course_name, ff['url'])
                except Exception as e2:
                    log.warning("Folder file failed '%s': %s", ff['name'], e2)
                    bot.send_message(chat_id,
                        f"📄 <b>{ff['name']}</b> — download failed\n"
                        f"📚 {course_name}\n<a href=\"{ff['url']}\">Download manually</a>")
        except Exception as e:
            log.warning("Folder download failed for '%s': %s", name, e)
            bot.send_message(chat_id,
                f"📁 <b>{name}</b> — could not open folder\n"
                f"📚 {course_name}\n<a href=\"{url}\">Open in Moodle</a>")
        return

    try:
        resp = session.get(url, allow_redirects=True, timeout=30, stream=True)
        content_type = resp.headers.get('Content-Type', '')

        if any(vm in content_type for vm in _VIDEO_MIMES):
            bot.send_message(chat_id,
                f"🎬 <b>{name}</b>\n"
                f"📚 {course_name}\n\n"
                f"▶️ <a href=\"{resp.url}\">Play / Download video</a>\n"
                f"🔗 <a href=\"{url}\">Open in Moodle</a>",
                disable_web_page_preview=True)
            return

        if 'text/html' in content_type:
            html_body = resp.text
            file_url = _extract_file_url_from_html(html_body)
            if file_url:
                if any(file_url.lower().endswith(ext) or ext + '?' in file_url.lower()
                       for ext in _VIDEO_EXTS):
                    bot.send_message(chat_id,
                        f"🎬 <b>{name}</b>\n"
                        f"📚 {course_name}\n\n"
                        f"▶️ <a href=\"{file_url}\">Play / Download video</a>\n"
                        f"🔗 <a href=\"{url}\">Open in Moodle</a>",
                        disable_web_page_preview=True)
                    return
                resp2 = session.head(file_url, allow_redirects=True, timeout=15)
                ct2 = resp2.headers.get('Content-Type', '')
                if any(vm in ct2 for vm in _VIDEO_MIMES):
                    bot.send_message(chat_id,
                        f"🎬 <b>{name}</b>\n"
                        f"📚 {course_name}\n\n"
                        f"▶️ <a href=\"{file_url}\">Play / Download video</a>\n"
                        f"🔗 <a href=\"{url}\">Open in Moodle</a>",
                        disable_web_page_preview=True)
                    return
                resp2 = session.get(file_url, allow_redirects=True, timeout=60, stream=True)
                _send_file_from_response(resp2, name, chat_id, course_name, url)
            else:
                bot.send_message(chat_id,
                    f"📄 <b>{name}</b> — embedded content, open in browser\n"
                    f"📚 {course_name}\n<a href=\"{url}\">Open in Moodle</a>")
            return

        _send_file_from_response(resp, name, chat_id, course_name, url)

    except Exception as e:
        log.warning("File download failed for '%s': %s", name, e)
        bot.send_message(chat_id,
            f"📄 <b>{name}</b> — could not auto-download.\n"
            f"📚 {course_name}\n<a href=\"{url}\">Open in Moodle</a>")


def _send_signup_summary(username: str, chat_id: int):
    """Send full status + courses + assignments after initial sync completes."""
    try:
        course_data  = load_user_data(username)
        assign_data  = load_user_assignments(username)
        course_cnt   = len(course_data.get('courses', []))
        pending_lst  = [a for a in assign_data.get('assignments', []) if not a.get('completed')]
        complete_cnt = sum(1 for a in assign_data.get('assignments', []) if a.get('completed'))

        bot.send_message(chat_id,
            f"📊 <b>Your Moodle Summary</b>\n\n"
            f"📚 Enrolled courses: <b>{course_cnt}</b>\n"
            f"📋 Pending assignments: <b>{len(pending_lst)}</b>\n"
            f"✅ Completed: <b>{complete_cnt}</b>\n"
            f"🔄 Synced: {course_data.get('last_synced', 'just now')}"
        )

        by_cat: dict = {}
        for c in course_data.get('courses', []):
            by_cat.setdefault(c.get('category') or 'Uncategorised', []).append(c)

        lines = [f"📚 <b>Enrolled Courses ({course_cnt})</b>\n"]
        for cat, cs in by_cat.items():
            lines.append(f"\n<b>🗂 {cat}</b>")
            for c in cs:
                lines.append(f"  • <a href=\"{c['course_url']}\">{c['full_display_name']}</a>")
        _send_long(chat_id, '\n'.join(lines))

        if pending_lst:
            bot.send_message(chat_id, f"📋 <b>{len(pending_lst)} Pending Assignment(s):</b>")
            for a in pending_lst:
                eid          = a.get('event_id')
                state        = get_reminder_state(username, eid)
                tl           = time_left_str(a.get('due_unix'))
                secs_left    = (int(a['due_unix']) - int(time.time())) if a.get('due_unix') else None
                show_snooze  = secs_left is not None and 0 < secs_left <= 24 * 3600
                text         = format_assignment_msg(a) + (f'\n{tl}' if tl else '')
                kb           = assignment_inline_kb(eid,
                                   is_muted=state.get('muted', False),
                                   snoozed_until=state.get('snoozed_until'),
                                   show_snooze=show_snooze)
                bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
        else:
            bot.send_message(chat_id, "🎉 <b>No pending assignments right now!</b>")

    except Exception as e:
        log.error("Post-signup summary error: %s", e)


@bot.message_handler(commands=['start'])
def cmd_start(message):
    if is_blocked(message.chat.id):
        bot.reply_to(message, "🚫 Your account is blocked. Contact the admin.")
        return
    name     = message.from_user.first_name or 'there'
    username = get_moodle_username(message.chat.id)
    if username:
        bot.reply_to(message,
            f"👋 Welcome back, <b>{name}</b>!\n"
            f"🎓 Linked account: <code>{username}</code>\n\n"
            "Use /help to see all commands."
        )
    else:
        bot.reply_to(message,
            f"👋 Hello, <b>{name}</b>! Welcome to the <b>Moodle Monitor Bot</b>.\n\n"
            "📌 Use /signup to link your Moodle account.\n\n"
            "Features:\n"
            "  • 🆕 New file alerts (files sent directly to this chat)\n"
            "  • 📋 New assignment alerts\n"
            "  • ⏰ Smart deadline reminders\n"
            "  • 📧 Email notifications\n\n"
            "Use /help for all commands."
        )


@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.reply_to(message,
        "📖 <b>Commands</b>\n\n"
        "🔐 <b>Account</b>\n"
        "  /signup                     – Link Moodle account\n"
        "  /reset_password             – Update Moodle password\n"
        "  /set_email                  – Set email for notifications\n"
        "  /logout                     – Unlink account (data kept)\n"
        "  /delete_account             – Delete all your data\n\n"
        "📚 <b>Moodle</b>\n"
        "  /enrolled_courses           – View all courses\n"
        "  /pending_assignments        – View pending deadlines\n"
        "  /download_files             – Download files from a course\n"
        "  /sync                       – Force immediate sync\n"
        "  /status                     – Summary + last sync info\n\n"
        "📋 <b>Todo</b>\n"
        "  /add_todo                   – Add one or more todos\n"
        "  /list_todo                  – View all your todos\n"
        "  /edit_todo                  – Edit a todo (title, date, status)\n"
        "  /delete_todo                – Delete a todo\n\n"
        "🔔 <b>Notifications</b>\n"
        "  /notification_preferences   – Choose Telegram / Email / Both\n"
        "  Auto: 24h → 12h → 6h → 3h → 1h → 30m → 10m\n"
        "  Per assignment: ✅ Done  💤 Snooze  ⏰ Custom  🔕 Mute\n\n"
        "🛡 <b>Admin</b>\n"
        "  /admin_panel                – Admin management panel\n"
        "  /broadcast                  – Broadcast message to all users\n"
    )


@bot.message_handler(commands=['signup'])
def cmd_signup(message):
    if is_blocked(message.chat.id):
        bot.reply_to(message, "🚫 Your account is blocked.")
        return
    existing = get_moodle_username(message.chat.id)
    if existing:
        bot.reply_to(message,
            f"ℹ️ Already linked to <code>{existing}</code>.\n"
            "Use /reset_password or /logout first."
        )
        return
    set_state(message.chat.id, S_SIGNUP_USER)
    bot.reply_to(message,
        "🎓 <b>Signup</b>\n\nEnter your Moodle username (e.g. <code>E0323040</code>):",
        reply_markup=ForceReply(selective=True),
    )


@bot.message_handler(commands=['reset_password'])
@require_signup
def cmd_reset_password(message):
    set_state(message.chat.id, S_RESET_PASS)
    bot.reply_to(message, "🔑 Enter your <b>new Moodle password</b>:",
                 reply_markup=ForceReply(selective=True))


@bot.message_handler(commands=['set_email'])
@require_signup
def cmd_set_email(message):
    current = get_user_email(message.chat.id)
    prompt  = f"📧 Current email: <code>{current}</code>\n\n" if current else "📧 No email set yet.\n\n"
    set_state(message.chat.id, S_SET_EMAIL)
    bot.reply_to(message,
        prompt + "Enter your email address — we'll send a 6-digit OTP to verify it:",
        reply_markup=ForceReply(selective=True),
    )


@bot.message_handler(commands=['add_todo'])
@require_signup
def cmd_add_todo(message):
    set_state(message.chat.id, S_TODO_TITLE)
    bot.reply_to(message,
        "📝 <b>Add Todo</b>\n\n"
        "Enter the <b>title</b> of your todo:",
        reply_markup=ForceReply(selective=True),
    )


@bot.message_handler(commands=['list_todo'])
@require_signup
def cmd_list_todo(message):
    username = get_moodle_username(message.chat.id)
    with _todos_lock:
        todos = load_todos(username)
    if not todos:
        bot.reply_to(message, "📭 No todos yet. Use /add_todo to create one.")
        return
    pending   = [t for t in todos if not t.get('completed')]
    completed = [t for t in todos if t.get('completed')]
    lines = ["📋 <b>Your Todos</b>\n"]
    if pending:
        lines.append("<b>Pending:</b>")
        for idx, t in enumerate(pending, 1):
            lines.append(_format_todo(t, idx))
        lines.append("")
    if completed:
        lines.append("<b>✅ Completed:</b>")
        for idx, t in enumerate(completed, len(pending) + 1):
            lines.append(_format_todo(t, idx))
        lines.append("")
    lines.append(f"<i>Total: {len(pending)} pending, {len(completed)} done</i>\n"
                 f"/add_todo  /delete_todo  /edit_todo")
    bot.reply_to(message, "\n".join(lines), disable_web_page_preview=True)


@bot.message_handler(commands=['delete_todo'])
@require_signup
def cmd_delete_todo(message):
    username = get_moodle_username(message.chat.id)
    with _todos_lock:
        todos = load_todos(username)
    if not todos:
        bot.reply_to(message, "📭 No todos to delete.")
        return
    lines = ["🗑 <b>Delete Todo</b>\n\nEnter the <b>serial number</b> to delete:\n"]
    for idx, t in enumerate(todos, 1):
        check = '✅' if t.get('completed') else '⚪'
        lines.append(f"{check} <b>#{idx}</b>  {t['title']}  —  📅 {t.get('due_str', 'Today')}")
    set_state(message.chat.id, S_TODO_DELETE)
    bot.reply_to(message, "\n".join(lines), reply_markup=ForceReply(selective=True))


@bot.message_handler(commands=['edit_todo'])
@require_signup
def cmd_edit_todo(message):
    username = get_moodle_username(message.chat.id)
    with _todos_lock:
        todos = load_todos(username)
    if not todos:
        bot.reply_to(message, "📭 No todos to edit.")
        return
    lines = ["✏️ <b>Edit Todo</b>\n\nEnter the <b>serial number</b> to edit:\n"]
    for idx, t in enumerate(todos, 1):
        check = '✅' if t.get('completed') else '⚪'
        lines.append(f"{check} <b>#{idx}</b>  {t['title']}  —  📅 {t.get('due_str', 'Today')}")
    set_state(message.chat.id, S_TODO_EDIT_PICK)
    bot.reply_to(message, "\n".join(lines), reply_markup=ForceReply(selective=True))


@bot.message_handler(commands=['logout'])
@require_signup
def cmd_logout(message):
    username = get_moodle_username(message.chat.id)
    unlink_user(message.chat.id)
    clear_state(message.chat.id)
    bot.reply_to(message,
        f"👋 Logged out. <code>{username}</code> unlinked.\n"
        "Data is kept on server. Use /signup to link again.",
        reply_markup=ReplyKeyboardRemove(),
    )


@bot.message_handler(commands=['delete_account'])
@require_signup
def cmd_delete_account(message):
    set_state(message.chat.id, S_DELETE_PASS)
    bot.reply_to(message,
        "⚠️ <b>Delete Account</b>\n\n"
        "This will permanently delete:\n"
        "  • Your Moodle credentials\n"
        "  • All course snapshots\n"
        "  • All assignment data\n"
        "  • All reminder states\n\n"
        "Enter your Moodle password to confirm, or /cancel:",
        reply_markup=ForceReply(selective=True),
    )


@bot.message_handler(commands=['enrolled_courses'])
@require_signup
def cmd_enrolled_courses(message):
    username = get_moodle_username(message.chat.id)
    wait_msg = bot.reply_to(message, "⏳ Fetching courses…")
    try:
        session, sesskey = bot_get_session(username)
        courses          = fetch_enrolled_courses(session, sesskey)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", wait_msg.chat.id, wait_msg.message_id)
        return
    if not courses:
        bot.edit_message_text("📭 No courses found.", wait_msg.chat.id, wait_msg.message_id)
        return
    by_cat: dict = {}
    for c in courses:
        by_cat.setdefault(c.get('category') or 'Uncategorised', []).append(c)
    lines = [f"📚 <b>Enrolled Courses ({len(courses)})</b>\n"]
    for cat, cs in by_cat.items():
        lines.append(f"\n<b>🗂 {cat}</b>")
        for c in cs:
            lines.append(f"  • <a href=\"{c['course_url']}\">{c['full_display_name']}</a>")
    _send_long(message.chat.id, '\n'.join(lines), wait_msg)


@bot.message_handler(commands=['pending_assignments'])
@require_signup
def cmd_pending_assignments(message):
    username    = get_moodle_username(message.chat.id)
    assign_data = load_user_assignments(username)
    pending     = [a for a in assign_data.get('assignments', []) if not a.get('completed')]
    if not pending:
        bot.reply_to(message,
            "🎉 <b>No pending assignments!</b>\n"
            f"Last synced: {assign_data.get('last_synced', 'never')}\n"
            "Use /sync to refresh."
        )
        return
    bot.reply_to(message, f"📋 <b>{len(pending)} pending assignment(s):</b>")
    overdue_list = [a for a in pending if a.get('overdue')]
    upcoming_list = [a for a in pending if not a.get('overdue')]
    for a in overdue_list + upcoming_list:
        eid          = a.get('event_id')
        state        = get_reminder_state(username, eid)
        tl           = time_left_str(a.get('due_unix'))
        secs_left    = (int(a['due_unix']) - int(time.time())) if a.get('due_unix') else None
        show_snooze  = secs_left is not None and 0 < secs_left <= 24 * 3600
        text         = format_assignment_msg(a) + (f'\n{tl}' if tl else '')
        kb           = assignment_inline_kb(eid,
                           is_muted=state.get('muted', False),
                           snoozed_until=state.get('snoozed_until'),
                           show_snooze=show_snooze)
        bot.send_message(message.chat.id, text, reply_markup=kb, disable_web_page_preview=True)


@bot.message_handler(commands=['sync'])
@require_signup
def cmd_sync(message):
    username = get_moodle_username(message.chat.id)
    wait_msg = bot.reply_to(message, "🔄 Syncing with Moodle…")
    try:
        _do_sync(username, chat_id=message.chat.id)
        assign_data = load_user_assignments(username)
        pending_cnt = sum(1 for a in assign_data.get('assignments', []) if not a.get('completed'))
        bot.edit_message_text(
            f"✅ Sync complete!\n"
            f"📋 <b>{pending_cnt}</b> pending assignment(s)\n"
            f"⏱ {assign_data.get('last_synced', '')}",
            wait_msg.chat.id, wait_msg.message_id,
        )
    except Exception as e:
        log.exception("Sync error for %s", username)
        bot.edit_message_text(f"❌ Sync failed: {e}", wait_msg.chat.id, wait_msg.message_id)


@bot.message_handler(commands=['status'])
@require_signup
def cmd_status(message):
    username     = get_moodle_username(message.chat.id)
    course_data  = load_user_data(username)
    assign_data  = load_user_assignments(username)
    course_cnt   = len(course_data.get('courses', []))
    pending_cnt  = sum(1 for a in assign_data.get('assignments', []) if not a.get('completed'))
    complete_cnt = sum(1 for a in assign_data.get('assignments', []) if a.get('completed'))
    email        = get_user_email(message.chat.id)
    pref_label   = NOTIF_LABELS.get(get_notif_pref(message.chat.id), 'Both')
    bot.reply_to(message,
        f"📊 <b>Status — <code>{username}</code></b>\n\n"
        f"📚 Enrolled courses: <b>{course_cnt}</b>\n"
        f"📋 Pending assignments: <b>{pending_cnt}</b>\n"
        f"✅ Completed: <b>{complete_cnt}</b>\n"
        f"📧 Email: {email if email else '<i>not set — use /set_email</i>'}\n"
        f"🔔 Notifications: <b>{pref_label}</b>\n\n"
        f"🔄 Courses synced:     {course_data.get('last_synced', 'never')}\n"
        f"🔄 Assignments synced: {assign_data.get('last_synced', 'never')}\n"
        f"⏱ Auto-sync every {_get_sync_interval() // 60} min ({'🌙 night mode' if 1 <= datetime.now(tz=IST).hour < 8 else '☀️ active'})"
    )


@bot.message_handler(commands=['notification_preferences'])
@require_signup
def cmd_notification_preferences(message):
    chat_id = message.chat.id
    pref    = get_notif_pref(chat_id)
    email   = get_user_email(chat_id)
    note    = ''
    if pref in (NOTIF_EMAIL, NOTIF_BOTH) and not email:
        note = ('\n\n⚠️ <i>You have email notifications enabled but no email is set.'
                ' Use /set_email to add one.</i>')
    bot.reply_to(message,
        f"🔔 <b>Notification Preferences</b>\n\n"
        f"Current: <b>{NOTIF_LABELS[pref]}</b>\n\n"
        "Choose how you'd like to receive reminders, new-file alerts,\n"
        "and assignment notifications:" + note,
        reply_markup=notif_pref_kb(pref),
    )


@bot.message_handler(commands=['download_files'])
@require_signup
def cmd_download_files(message):
    username = get_moodle_username(message.chat.id)
    wait_msg = bot.reply_to(message, "⏳ Fetching courses…")
    try:
        session, sesskey = bot_get_session(username)
        courses = fetch_enrolled_courses(session, sesskey)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", wait_msg.chat.id, wait_msg.message_id)
        return
    if not courses:
        bot.edit_message_text("📭 No courses found.", wait_msg.chat.id, wait_msg.message_id)
        return
    lines = ["📚 <b>Select a course</b>\n"]
    course_list = []
    for idx, c in enumerate(courses, 1):
        course_list.append({
            'course_id': c['course_id'],
            'name': c['full_display_name'],
        })
        lines.append(f"  <b>{idx}.</b> {c['full_display_name']}")
    lines.append("\n📝 Reply with the <b>course number</b>:")
    set_state(message.chat.id, S_DL_COURSE, {'courses': course_list})
    _send_long(message.chat.id, '\n'.join(lines), wait_msg)


@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message):
    if not (is_admin_user(message) or admin_is_authed(message.chat.id)):
        bot.reply_to(message, "❌ Admin only.")
        return
    bot.reply_to(message,
        "📢 <b>Broadcast Message</b>\n\n"
        "Choose a template or write a custom message:",
        reply_markup=_broadcast_menu_kb())


@bot.message_handler(commands=['admin_panel'])
def cmd_admin_panel(message):
    if is_admin_user(message) or admin_is_authed(message.chat.id):
        _admin_authed.add(int(message.chat.id))
        _save_admin_sessions()
        clear_state(message.chat.id)
        bot.reply_to(message,
            "🛡 <b>Admin Panel — Welcome, Admin!</b>",
            reply_markup=admin_main_kb(),
        )
    else:
        set_state(message.chat.id, S_ADMIN_PASS)
        bot.reply_to(message, "🔐 Enter admin password:",
                     reply_markup=ForceReply(selective=True))


@bot.message_handler(commands=['cancel'])
def cmd_cancel(message):
    clear_state(message.chat.id)
    bot.reply_to(message, "❌ Cancelled.", reply_markup=ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text(message):
    chat_id = message.chat.id
    if is_blocked(chat_id):
        return

    st   = get_state(chat_id)
    s    = st['state']
    data = st['data']
    text = message.text.strip()

    if s == S_IDLE and text.startswith('/'):
        bot.reply_to(message,
            "❓ Unknown command.\nUse /help to see all available commands.")
        return

    if s == S_BROADCAST_CUSTOM:
        clear_state(chat_id)
        preview = text.strip()
        if not preview:
            bot.reply_to(message, "❌ Empty message. Broadcast cancelled.")
            return
        set_state(chat_id, S_IDLE, {'bcast_text': preview})
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton('📢 Send to All', callback_data='bcast_ok:custom_confirm'),
            InlineKeyboardButton('❌ Cancel',      callback_data='bcast_ok:cancel'),
        )
        bot.reply_to(message,
            f"📢 <b>Preview:</b>\n\n{preview}\n\n"
            f"<i>This will be sent to all active users.</i>",
            reply_markup=kb)
        return

    if s == S_ADMIN_TEXT:
        clear_state(chat_id)
        adm_action = data.get('adm_action', '')
        with _bot_users_lock:
            bu = load_bot_users()
        target_cid  = None
        target_user = None
        for cid, info in bu.items():
            if text == cid or text.upper() == info.get('username', '').upper():
                target_cid  = cid
                target_user = info.get('username', '')
                break

        if not target_cid:
            bot.reply_to(message, f"❌ User '{text}' not found in bot users.")
            return

        if int(target_cid) == chat_id:
            bot.reply_to(message, "❌ You cannot perform admin actions on your own account.")
            return

        if adm_action == 'block':
            set_blocked(int(target_cid), True)
            bot.reply_to(message, f"🚫 <code>{target_user}</code> blocked.")
        elif adm_action == 'unblock':
            set_blocked(int(target_cid), False)
            bot.reply_to(message, f"✅ <code>{target_user}</code> unblocked.")
        elif adm_action == 'del_user':
            delete_all_user_data(int(target_cid), target_user)
            bot.reply_to(message, f"🗑 <code>{target_user}</code> and all data deleted.")
        elif adm_action == 'view_assign':
            assigns = load_user_assignments(target_user)
            pending = [a for a in assigns.get('assignments', []) if not a.get('completed')]
            if not pending:
                bot.reply_to(message, f"No pending assignments for <code>{target_user}</code>.")
            else:
                lines = [f"📋 <b>Assignments for {target_user}</b>\n"]
                for a in pending:
                    ov = '⚠️ OVERDUE' if a.get('overdue') else 'pending'
                    lines.append(f"• {a.get('name')} | Due: {a.get('due')} | {ov}")
                _send_long(chat_id, '\n'.join(lines))
        return

    if s == S_SIGNUP_USER:
        username = text.upper().strip()
        with _users_lock:
            users_db = load_users()
        found    = username in users_db
        set_state(chat_id, S_SIGNUP_PASS, {'username': username})
        prompt   = (f"Found record for <code>{username}</code>.\n" if found else
                    f"Username: <code>{username}</code>\n")
        bot.reply_to(message, prompt + "🔑 Enter your Moodle password:",
                     reply_markup=ForceReply(selective=True))
        return

    if s == S_SIGNUP_PASS:
        username = data.get('username', '')
        password = text
        wait_msg = bot.reply_to(message, "⏳ Verifying credentials with Moodle…")
        try:
            session, sesskey = moodle_login(username, password)
        except Exception as e:
            clear_state(chat_id)
            bot.edit_message_text(f"❌ Login failed: {e}\nTry /signup again.",
                                  wait_msg.chat.id, wait_msg.message_id)
            return
        with _users_lock:
            users_db = load_users()
            users_db[username] = {
                'username':   username,
                'password':   password,
                'cookies':    dict(session.cookies),
                'sesskey':    sesskey,
                'last_login': datetime.now().isoformat(),
            }
            save_users(users_db)
        link_user(chat_id, username)
        clear_state(chat_id)
        bot.edit_message_text(
            f"🎉 <b>Signup successful!</b> Welcome, <code>{username}</code>.\n\n"
            "⏳ Running initial sync — your summary will appear shortly…",
            wait_msg.chat.id, wait_msg.message_id,
        )

        def _initial_sync_thread():
            _do_sync(username, chat_id=chat_id, is_initial=True)
            _send_signup_summary(username, chat_id)

        threading.Thread(target=_initial_sync_thread, daemon=True).start()
        return

    if s == S_RESET_PASS:
        username = get_moodle_username(chat_id)
        password = text
        wait_msg = bot.reply_to(message, "⏳ Verifying…")
        try:
            session, sesskey = moodle_login(username, password)
        except Exception as e:
            clear_state(chat_id)
            bot.edit_message_text(f"❌ Verification failed: {e}",
                                  wait_msg.chat.id, wait_msg.message_id)
            return
        with _users_lock:
            users_db = load_users()
            users_db[username].update({
                'password':   password,
                'cookies':    dict(session.cookies),
                'sesskey':    sesskey,
                'last_login': datetime.now().isoformat(),
            })
            save_users(users_db)
        clear_state(chat_id)
        bot.edit_message_text("✅ Password updated!", wait_msg.chat.id, wait_msg.message_id)
        return

    if s == S_SET_EMAIL:
        if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
            bot.reply_to(message, "❌ Invalid email address. Try again:",
                         reply_markup=ForceReply(selective=True))
            return
        email_addr = text.strip().lower()
        current_email = get_user_email(chat_id)
        if current_email and current_email.lower() == email_addr:
            clear_state(chat_id)
            bot.reply_to(message,
                f"ℹ️ <code>{email_addr}</code> is already your saved email.\n"
                "No changes made.",
                parse_mode='HTML')
            return
        otp = generate_otp()
        store_otp(chat_id, otp, email_addr)
        wait_msg = bot.reply_to(message, "⏳ Sending verification code…")
        try:
            send_otp_email(email_addr, otp)
        except Exception as e:
            clear_state(chat_id)
            bot.edit_message_text(
                f"❌ Could not send OTP to <code>{email_addr}</code>\n"
                f"Error: {e}\n\n"
                "Please check bot_config.json SMTP settings and try /set_email again.",
                wait_msg.chat.id, wait_msg.message_id,
            )
            return
        set_state(chat_id, S_SET_EMAIL_OTP, {'email': email_addr})
        bot.edit_message_text(
            f"📧 OTP sent to <code>{email_addr}</code>!\n\n"
            "Enter the <b>6-digit code</b> from your inbox (valid 10 min):\n"
            "<i>Check spam if you don't see it.</i>",
            wait_msg.chat.id, wait_msg.message_id,
            parse_mode='HTML',
        )
        return

    if s == S_SET_EMAIL_OTP:
        pending_email = data.get('email', '')
        if not re.match(r'^\d{6}$', text.strip()):
            bot.reply_to(message, "❌ Enter the exact 6-digit code:",
                         reply_markup=ForceReply(selective=True))
            return
        if verify_otp(chat_id, text):
            set_user_email(chat_id, pending_email)
            clear_state(chat_id)
            bot.reply_to(message,
                f"✅ Email verified and saved: <code>{pending_email}</code>\n\n"
                "You'll now receive deadline reminders, new-file alerts, and assignment "
                "notifications by email.",
                reply_markup=ReplyKeyboardRemove(),
            )
            if EMAIL_ENABLED:
                content = (
                    f'<p style="color:#374151;font-size:15px;">'
                    f'Your email <span class="highlight">{pending_email}</span> '
                    f'has been successfully verified for <strong>Moodle Monitor Bot</strong>.</p>'
                    f'<div class="note">You will now receive deadline reminders, '
                    f'new file alerts, and assignment notifications here.</div>'
                )
                try:
                    send_email(pending_email,
                               "✅ Email verified — Moodle Monitor Bot",
                               f"Your email {pending_email} has been verified for Moodle Monitor Bot.",
                               html=_build_html_email("Email Verified", content))
                except Exception:
                    pass
        else:
            with _otp_lock:
                rec = _otp_store.get(chat_id, {})
                expired = not rec or int(time.time()) > rec.get('expires', 0)
            if expired:
                clear_state(chat_id)
                bot.reply_to(message,
                    "⏳ OTP has <b>expired</b>. Use /set_email to request a new code.",
                    reply_markup=ReplyKeyboardRemove(),
                )
            else:
                bot.reply_to(message, "❌ Wrong code. Try again:",
                             reply_markup=ForceReply(selective=True))
        return

    if s == S_DELETE_PASS:
        username = get_moodle_username(chat_id)
        wait_msg = bot.reply_to(message, "⏳ Verifying…")
        try:
            moodle_login(username, text)
        except Exception as e:
            clear_state(chat_id)
            bot.edit_message_text(f"❌ Wrong password. Account NOT deleted.\n({e})",
                                  wait_msg.chat.id, wait_msg.message_id)
            return
        delete_all_user_data(chat_id, username)
        clear_state(chat_id)
        bot.edit_message_text(
            f"🗑 Account <code>{username}</code> and all data deleted.\n"
            "Use /signup to register again.",
            wait_msg.chat.id, wait_msg.message_id,
        )
        return

    if s == S_ADMIN_PASS:
        if text == ADMIN_PASSWORD:
            _admin_authed.add(int(chat_id))
            _save_admin_sessions()
            clear_state(chat_id)
            bot.reply_to(message,
                "🛡 <b>Admin Panel</b>\n\nAccess granted.",
                reply_markup=admin_main_kb(),
            )
        else:
            clear_state(chat_id)
            bot.reply_to(message, "❌ Wrong admin password.")
        return

    if s == S_CUSTOM_SNOOZE_DT:
        event_id = data.get('event_id')
        due_unix = data.get('due_unix')      # stored when custom_snooze was tapped
        username = get_moodle_username(chat_id)
        dt       = None
        for fmt in ('%d/%m/%Y %H:%M', '%d-%m-%Y %H:%M', '%d/%m %H:%M'):
            try:
                dt = datetime.strptime(text, fmt)
                if dt.year == 1900:
                    dt = dt.replace(year=datetime.now().year)
                dt = dt.replace(tzinfo=IST)
                break
            except ValueError:
                continue
        if not dt:
            bot.reply_to(message,
                "❌ Unrecognised format.\n\n"
                "Use 24-hour format: <code>DD/MM/YYYY HH:MM</code>\n"
                "Example: <code>24/02/2026 23:30</code>\n"
                "<i>(midnight = 00:00, noon = 12:00, 11 PM = 23:00)</i>",
                reply_markup=ForceReply(selective=True),
            )
            return
        chosen_ts = int(dt.timestamp())
        now_ts    = int(time.time())
        if chosen_ts <= now_ts:
            bot.reply_to(message,
                f"⚠️ <b>{dt.strftime('%d %b %Y, %H:%M')}</b> is already in the past!\n\n"
                "Enter a <b>future</b> date and time (24-hour format):\n"
                "<code>DD/MM/YYYY HH:MM</code>",
                reply_markup=ForceReply(selective=True),
            )
            return
        if due_unix and chosen_ts >= int(due_unix) - 300:
            due_str = unix_to_ist(due_unix)
            bot.reply_to(message,
                f"⚠️ Snooze must end <b>at least 5 minutes before the deadline</b>.\n"
                f"Deadline: <b>{due_str}</b>\n\n"
                "Enter an earlier date/time (24-hour format):\n"
                "<code>DD/MM/YYYY HH:MM</code>",
                reply_markup=ForceReply(selective=True),
            )
            return
        secs = chosen_ts - now_ts
        snooze_assignment(username, event_id, secs)
        clear_state(chat_id)
        bot.reply_to(message,
            f"💤 Snoozed until <b>{dt.strftime('%d %b %Y, %H:%M')} IST</b>.\n"
            f"You'll get a reminder when the snooze ends.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if s == S_DL_COURSE:
        course_list = data.get('courses', [])
        try:
            choice = int(text)
        except ValueError:
            bot.reply_to(message, "❌ Enter a valid course number:",
                         reply_markup=ForceReply(selective=True))
            return
        if choice < 1 or choice > len(course_list):
            bot.reply_to(message,
                f"❌ Enter a number between 1 and {len(course_list)}:",
                reply_markup=ForceReply(selective=True))
            return
        picked = course_list[choice - 1]
        username = get_moodle_username(chat_id)
        wait_msg = bot.reply_to(message, f"⏳ Fetching files for <b>{picked['name']}</b>…")
        try:
            session, sesskey = bot_get_session(username)
            sections = fetch_course_sections(session, sesskey, picked['course_id'])
        except Exception as e:
            clear_state(chat_id)
            bot.edit_message_text(f"❌ Error: {e}", wait_msg.chat.id, wait_msg.message_id)
            return
        _DL_MODNAMES = {'file', 'resource', 'folder', 'url'}
        file_list = []
        for sec in sections:
            for item in sec.get('items', []):
                if (item.get('modname') or '').lower() in _DL_MODNAMES:
                    file_list.append({
                        'id': item['id'],
                        'name': item.get('name', 'Unknown'),
                        'modname': (item.get('modname') or '').lower(),
                        'url': item.get('url', ''),
                        'section': sec.get('section_title', ''),
                    })
        if not file_list:
            clear_state(chat_id)
            bot.edit_message_text(
                f"📭 No downloadable files found in <b>{picked['name']}</b>.",
                wait_msg.chat.id, wait_msg.message_id)
            return
        lines = [f"📁 <b>Files in {picked['name']}</b> ({len(file_list)} items)\n"]
        for idx, f_item in enumerate(file_list, 1):
            mn = f_item['modname']
            icon = '🔗' if mn == 'url' else ('📁' if mn == 'folder' else '📄')
            lines.append(f"  <b>{idx}.</b> {icon} {f_item['name']}")
            if f_item['section']:
                lines[-1] += f"  <i>({f_item['section']})</i>"
        lines.append(
            "\n📝 Reply with file number(s):\n"
            "  • Single: <code>2</code>\n"
            "  • Range: <code>2-5</code>\n"
            "  • Selective: <code>1 4 5</code>\n"
            "  • All: <code>all</code>"
        )
        set_state(chat_id, S_DL_FILE, {
            'files': file_list,
            'course_name': picked['name'],
        })
        _send_long(chat_id, '\n'.join(lines), wait_msg)
        return

    if s == S_DL_FILE:
        file_list   = data.get('files', [])
        course_name = data.get('course_name', '')
        total       = len(file_list)
        indices     = set()
        raw         = text.strip().lower()
        if raw == 'all':
            indices = set(range(1, total + 1))
        else:
            for part in raw.replace(',', ' ').split():
                part = part.strip()
                if not part:
                    continue
                m = re.match(r'^(\d+)\s*-\s*(\d+)$', part)
                if m:
                    a, b = int(m.group(1)), int(m.group(2))
                    if a > b:
                        a, b = b, a
                    for i in range(a, b + 1):
                        indices.add(i)
                elif re.match(r'^\d+$', part):
                    indices.add(int(part))
        invalid = [i for i in indices if i < 1 or i > total]
        if not indices or invalid:
            msg = "❌ Invalid input."
            if invalid:
                msg += f" Numbers {invalid} out of range (1-{total})."
            msg += (
                "\n\nEnter file number(s):\n"
                "  • Single: <code>2</code>\n"
                "  • Range: <code>2-5</code>\n"
                "  • Selective: <code>1 4 5</code>\n"
                "  • All: <code>all</code>"
            )
            bot.reply_to(message, msg, reply_markup=ForceReply(selective=True))
            return
        chosen = sorted(indices)
        username = get_moodle_username(chat_id)
        clear_state(chat_id)
        bot.reply_to(message, f"⏳ Downloading {len(chosen)} file(s)…")
        try:
            session, sesskey = bot_get_session(username)
        except Exception as e:
            bot.send_message(chat_id, f"❌ Session error: {e}")
            return
        for idx in chosen:
            f_item = file_list[idx - 1]
            download_and_send_file(session, f_item, chat_id, course_name)
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton('📚 Download more', callback_data='dl:more'),
            InlineKeyboardButton('✅ Done',          callback_data='dl:done'),
        )
        bot.send_message(chat_id,
            f"✅ Done — sent {len(chosen)} file(s) from <b>{course_name}</b>.",
            reply_markup=kb)
        return

    if s == S_TODO_TITLE:
        username = get_moodle_username(chat_id)
        if not username:
            clear_state(chat_id)
            bot.reply_to(message, "Please /signup first.")
            return
        pending_todos = data.get('pending_todos', [])
        if text.lower() in ('done', 'completed', 'finish', 'stop'):
            clear_state(chat_id)
            if pending_todos:
                lines = "\n".join(f"  ✅ {t['title']}  —  📅 {t['due_str']}" for t in pending_todos)
                bot.reply_to(message,
                    f"🎉 <b>{len(pending_todos)} todo(s) added!</b>\n\n{lines}\n\n"
                    "Use /list_todo to view all todos.",
                    reply_markup=ReplyKeyboardRemove(),
                )
            else:
                bot.reply_to(message, "👍 No todos added. Use /add_todo anytime.",
                             reply_markup=ReplyKeyboardRemove())
            return
        title = text.strip()
        if not title:
            bot.reply_to(message, "❌ Title cannot be empty. Enter a title:",
                         reply_markup=ForceReply(selective=True))
            return
        set_state(chat_id, S_TODO_DUE, {'title': title, 'pending_todos': pending_todos})
        bot.reply_to(message,
            f"✅ Title: <b>{title}</b>\n\n"
            "📅 Enter the <b>due date and time</b> in this format:\n"
            "<code>DD/MM/YYYY hh:mm AM/PM</code>\n"
            "Example: <code>05/03/2026 11:30 PM</code>\n\n"
            "Or type <b>today</b> for today end-of-day (11:59 PM).",
            reply_markup=ForceReply(selective=True),
        )
        return

    if s == S_TODO_DUE:
        username = get_moodle_username(chat_id)
        title    = data.get('title', '')
        pending_todos = data.get('pending_todos', [])

        if text.lower() == 'today':
            dt = datetime.now(tz=IST).replace(hour=23, minute=59, second=0, microsecond=0)
        else:
            dt = _parse_indian_datetime(text)

        if not dt:
            bot.reply_to(message,
                "❌ Invalid format. Please use:\n"
                "<code>DD/MM/YYYY hh:mm AM/PM</code>\n"
                "Example: <code>05/03/2026 11:30 PM</code>\n\n"
                "Or type <b>today</b>.",
                reply_markup=ForceReply(selective=True),
            )
            return

        if int(dt.timestamp()) <= int(time.time()):
            bot.reply_to(message,
                "⚠️ That date/time is in the past! Enter a <b>future</b> date:\n"
                "<code>DD/MM/YYYY hh:mm AM/PM</code>",
                reply_markup=ForceReply(selective=True),
            )
            return

        due_unix = int(dt.timestamp())
        due_str  = dt.strftime('%d/%m/%Y %I:%M %p')

        with _todos_lock:
            todos = load_todos(username)
            new_todo = {
                'id':            _next_todo_id(todos),
                'title':         title,
                'due_unix':      due_unix,
                'due_str':       due_str,
                'created_at':    datetime.now(tz=IST).strftime('%d %b %Y, %I:%M %p'),
                'completed':     False,
                'reminder_sent': {},
            }
            todos.append(new_todo)
            pending_todos.append(new_todo)
            save_todos(username, todos)

        set_state(chat_id, S_TODO_TITLE, {'pending_todos': pending_todos})
        added_list = "\n".join(f"  ✅ {t['title']}  —  📅 {t['due_str']}" for t in pending_todos)
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton('➕ Add More', callback_data='todoadd:more'),
            InlineKeyboardButton('✅ Done',     callback_data='todoadd:done'),
        )
        bot.reply_to(message,
            f"✅ Todo added!\n\n"
            f"<b>Added so far:</b>\n{added_list}",
            reply_markup=kb,
        )
        return


    if s == S_TODO_DELETE:
        username = get_moodle_username(chat_id)
        with _todos_lock:
            todos = load_todos(username)
            if not text.isdigit() or int(text) < 1 or int(text) > len(todos):
                bot.reply_to(message,
                    f"❌ Enter a valid number between 1 and {len(todos)}.",
                    reply_markup=ForceReply(selective=True))
                return
            idx = int(text) - 1
            removed = todos.pop(idx)
            save_todos(username, todos)
        clear_state(chat_id)
        bot.reply_to(message,
            f"🗑 Deleted: <b>{removed['title']}</b>\n\n"
            f"Use /list_todo to see remaining todos.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if s == S_TODO_EDIT_PICK:
        username = get_moodle_username(chat_id)
        with _todos_lock:
            todos = load_todos(username)
        if not text.isdigit() or int(text) < 1 or int(text) > len(todos):
            bot.reply_to(message,
                f"❌ Enter a valid number between 1 and {len(todos)}.",
                reply_markup=ForceReply(selective=True))
            return
        idx  = int(text) - 1
        todo = todos[idx]
        set_state(chat_id, S_IDLE)  # temporarily idle while inline kb is shown
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton('✏️ Edit Title',     callback_data=f'todoedit:title:{todo["id"]}'),
            InlineKeyboardButton('📅 Edit Due Date',   callback_data=f'todoedit:due:{todo["id"]}'),
        )
        kb.add(
            InlineKeyboardButton('✅ Mark Done' if not todo.get('completed') else '↩️ Mark Pending',
                                 callback_data=f'todoedit:toggle:{todo["id"]}'),
        )
        kb.add(InlineKeyboardButton('❌ Cancel', callback_data='todoedit:cancel:0'))
        check = '✅' if todo.get('completed') else '⚪'
        bot.reply_to(message,
            f"✏️ <b>Edit Todo #{text}</b>\n\n"
            f"{check} <b>{todo['title']}</b>\n"
            f"📅 Due: <b>{todo.get('due_str', 'Today')}</b>\n\n"
            "What do you want to edit?",
            reply_markup=kb,
        )
        return

    if s == S_TODO_EDIT_TITLE:
        username = get_moodle_username(chat_id)
        todo_id  = data.get('todo_id')
        new_title = text.strip()
        if not new_title:
            bot.reply_to(message, "❌ Title cannot be empty. Enter a new title:",
                         reply_markup=ForceReply(selective=True))
            return
        with _todos_lock:
            todos = load_todos(username)
            for t in todos:
                if t['id'] == todo_id:
                    t['title'] = new_title
                    break
            save_todos(username, todos)
        clear_state(chat_id)
        bot.reply_to(message,
            f"✅ Title updated to: <b>{new_title}</b>\n\nUse /list_todo to view.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if s == S_TODO_EDIT_DUE:
        username = get_moodle_username(chat_id)
        todo_id  = data.get('todo_id')

        if text.lower() == 'today':
            dt = datetime.now(tz=IST).replace(hour=23, minute=59, second=0, microsecond=0)
        else:
            dt = _parse_indian_datetime(text)

        if not dt:
            bot.reply_to(message,
                "❌ Invalid format. Use:\n"
                "<code>DD/MM/YYYY hh:mm AM/PM</code>\n"
                "Example: <code>05/03/2026 11:30 PM</code>\n\n"
                "Or type <b>today</b>.",
                reply_markup=ForceReply(selective=True),
            )
            return

        if int(dt.timestamp()) <= int(time.time()):
            bot.reply_to(message,
                "⚠️ That's in the past! Enter a <b>future</b> date:\n"
                "<code>DD/MM/YYYY hh:mm AM/PM</code>",
                reply_markup=ForceReply(selective=True),
            )
            return

        due_unix = int(dt.timestamp())
        due_str  = dt.strftime('%d/%m/%Y %I:%M %p')
        with _todos_lock:
            todos = load_todos(username)
            for t in todos:
                if t['id'] == todo_id:
                    t['due_unix']      = due_unix
                    t['due_str']       = due_str
                    t['reminder_sent'] = {}   # reset reminders for new date
                    break
            save_todos(username, todos)
        clear_state(chat_id)
        bot.reply_to(message,
            f"✅ Due date updated to: <b>{due_str}</b>\n\nUse /list_todo to view.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    parts   = call.data.split(':')
    action  = parts[0]
    param   = parts[1] if len(parts) > 1 else None

    if action == 'adm':
        if not (is_admin_user(call) or admin_is_authed(chat_id)):
            _safe_answer(call.id, "❌ Access denied.")
            return
        _safe_answer(call.id)
        _handle_admin_callback(call, param)
        return

    if action == 'bcast':
        if not (is_admin_user(call) or admin_is_authed(chat_id)):
            _safe_answer(call.id, "❌ Admin only.")
            return
        _safe_answer(call.id)
        if param == 'cancel':
            try:
                bot.edit_message_text("❌ Broadcast cancelled.",
                    chat_id, call.message.message_id)
            except Exception:
                pass
            return
        if param == 'custom':
            set_state(chat_id, S_BROADCAST_CUSTOM)
            try:
                bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
            except Exception:
                pass
            bot.send_message(chat_id,
                "✏️ Type your <b>custom broadcast message</b>:\n\n"
                "<i>Supports HTML formatting (bold, italic, links).</i>\n"
                "Send /cancel to abort.",
                reply_markup=ForceReply(selective=True))
            return
        if param in _BROADCAST_TEMPLATES:
            preview = _BROADCAST_TEMPLATES[param]
            try:
                bot.edit_message_text(
                    f"📢 <b>Preview:</b>\n\n{preview}\n\n"
                    f"<i>This will be sent to all active users.</i>",
                    chat_id, call.message.message_id,
                    parse_mode='HTML',
                    reply_markup=_broadcast_confirm_kb(param))
            except Exception:
                pass
            return
        return

    if action == 'bcast_ok':
        if not (is_admin_user(call) or admin_is_authed(chat_id)):
            _safe_answer(call.id, "❌ Admin only.")
            return
        _safe_answer(call.id)
        if param == 'cancel':
            try:
                bot.edit_message_text("❌ Broadcast cancelled.",
                    chat_id, call.message.message_id)
            except Exception:
                pass
            return
        if param == 'custom_confirm':
            st = get_state(chat_id)
            text_to_send = st.get('data', {}).get('bcast_text', '')
            clear_state(chat_id)
            if not text_to_send:
                try:
                    bot.edit_message_text("❌ No message to send.",
                        chat_id, call.message.message_id)
                except Exception:
                    pass
                return
            try:
                bot.edit_message_text("📢 Broadcasting…",
                    chat_id, call.message.message_id)
            except Exception:
                pass
            _do_broadcast(chat_id, text_to_send)
            return
        text_to_send = _BROADCAST_TEMPLATES.get(param, '')
        if not text_to_send:
            return
        try:
            bot.edit_message_text("📢 Broadcasting…",
                chat_id, call.message.message_id)
        except Exception:
            pass
        _do_broadcast(chat_id, text_to_send)
        return

    if action == 'dl':
        if param == 'done':
            _safe_answer(call.id, "👍 Done!")
            try:
                bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
            except Exception:
                pass
            return
        if param == 'more':
            _safe_answer(call.id)
            try:
                bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
            except Exception:
                pass
            username = get_moodle_username(chat_id)
            if not username:
                bot.send_message(chat_id, "Please /signup first.")
                return
            wait_msg = bot.send_message(chat_id, "⏳ Fetching courses…")
            try:
                session, sesskey = bot_get_session(username)
                courses = fetch_enrolled_courses(session, sesskey)
            except Exception as e:
                bot.edit_message_text(f"❌ Error: {e}", wait_msg.chat.id, wait_msg.message_id)
                return
            if not courses:
                bot.edit_message_text("📭 No courses found.", wait_msg.chat.id, wait_msg.message_id)
                return
            lines = ["📚 <b>Select a course</b>\n"]
            course_list = []
            for idx, c in enumerate(courses, 1):
                course_list.append({
                    'course_id': c['course_id'],
                    'name': c['full_display_name'],
                })
                lines.append(f"  <b>{idx}.</b> {c['full_display_name']}")
            lines.append("\n📝 Reply with the <b>course number</b>:")
            set_state(chat_id, S_DL_COURSE, {'courses': course_list})
            _send_long(chat_id, '\n'.join(lines), wait_msg)
            return
        _safe_answer(call.id)
        return

    if action == 'todoadd':
        username = get_moodle_username(chat_id)
        if not username:
            _safe_answer(call.id, "Please /signup first.")
            return

        if param == 'done':
            _safe_answer(call.id, "👍 All done!")
            st = get_state(chat_id)
            pending_todos = st.get('data', {}).get('pending_todos', [])
            clear_state(chat_id)
            try:
                bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
            except Exception:
                pass
            if pending_todos:
                lines = "\n".join(f"  ✅ {t['title']}  —  📅 {t['due_str']}" for t in pending_todos)
                bot.send_message(chat_id,
                    f"🎉 <b>{len(pending_todos)} todo(s) saved!</b>\n\n{lines}\n\n"
                    "Use /list_todo to view all todos.",
                )
            return

        if param == 'more':
            _safe_answer(call.id)
            try:
                bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
            except Exception:
                pass
            st = get_state(chat_id)
            pending_todos = st.get('data', {}).get('pending_todos', [])
            set_state(chat_id, S_TODO_TITLE, {'pending_todos': pending_todos})
            bot.send_message(chat_id,
                "📝 Enter the <b>title</b> of your next todo:",
                reply_markup=ForceReply(selective=True),
            )
            return

        _safe_answer(call.id)
        return

    if action == 'todoedit':
        field   = param                                      # title / due / toggle / cancel
        todo_id = int(parts[2]) if len(parts) > 2 else 0
        username = get_moodle_username(chat_id)

        if field == 'cancel':
            _safe_answer(call.id, "Cancelled.")
            try:
                bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
            except Exception:
                pass
            return

        if not username:
            _safe_answer(call.id, "Please /signup first.")
            return

        if field == 'toggle':
            status = None
            with _todos_lock:
                todos = load_todos(username)
                for t in todos:
                    if t['id'] == todo_id:
                        t['completed'] = not t.get('completed', False)
                        status = '✅ Done' if t['completed'] else '⚪ Pending'
                        break
                save_todos(username, todos)
            if status:
                _safe_answer(call.id, f"{status}!")
            try:
                bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
            except Exception:
                pass
            return

        if field == 'title':
            set_state(chat_id, S_TODO_EDIT_TITLE, {'todo_id': todo_id})
            _safe_answer(call.id)
            bot.send_message(chat_id,
                "✏️ Enter the <b>new title</b>:",
                reply_markup=ForceReply(selective=True),
            )
            return

        if field == 'due':
            set_state(chat_id, S_TODO_EDIT_DUE, {'todo_id': todo_id})
            _safe_answer(call.id)
            bot.send_message(chat_id,
                "📅 Enter the <b>new due date and time</b>:\n"
                "<code>DD/MM/YYYY hh:mm AM/PM</code>\n"
                "Example: <code>05/03/2026 11:30 PM</code>\n\n"
                "Or type <b>today</b> for today 11:59 PM.",
                reply_markup=ForceReply(selective=True),
            )
            return

        _safe_answer(call.id)
        return

    if action == 'notifpref':
        new_pref = param
        if new_pref not in (NOTIF_TELEGRAM, NOTIF_EMAIL, NOTIF_BOTH):
            _safe_answer(call.id, "❌ Unknown option.")
            return
        user_for_pref = get_moodle_username(chat_id)
        if not user_for_pref:
            _safe_answer(call.id, "Please /signup first.")
            return
        set_notif_pref(chat_id, new_pref)
        label = NOTIF_LABELS[new_pref]
        _safe_answer(call.id, f"✅ {label} saved!")
        email = get_user_email(chat_id)
        note = ''
        if new_pref in (NOTIF_EMAIL, NOTIF_BOTH) and not email:
            note = '\n\n⚠️ <i>No email set yet — use /set_email to add one.</i>'
        try:
            bot.edit_message_text(
                f"🔔 <b>Notification Preferences</b>\n\n"
                f"✅ Saved: <b>{label}</b>{note}",
                chat_id, call.message.message_id,
                parse_mode='HTML',
                reply_markup=notif_pref_kb(new_pref),
            )
        except Exception:
            pass
        return


    username = get_moodle_username(chat_id)
    if not username:
        _safe_answer(call.id, "Please /signup first.")
        return

    event_id = param

    if action == 'done':
        mark_complete_reminder(username, event_id)
        _safe_answer(call.id, "✅ Marked as complete!")
        try:
            bot.edit_message_text(
                call.message.html_text + "\n\n✅ <b>Marked as completed</b>",
                chat_id, call.message.message_id,
                parse_mode='HTML', disable_web_page_preview=True,
            )
        except Exception:
            pass
        return

    if action == 'snooze_menu':
        _safe_answer(call.id)
        assign_data  = load_user_assignments(username)
        due_unix_val = next(
            (a.get('due_unix') for a in assign_data.get('assignments', [])
             if str(a.get('event_id')) == str(event_id)), None
        )
        secs_left = (int(due_unix_val) - int(time.time())) if due_unix_val else None
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id,
                                          reply_markup=snooze_options_kb(event_id, secs_left))
        except Exception:
            pass
        return

    if action == 'snooze_back':
        _safe_answer(call.id)
        state = get_reminder_state(username, event_id)
        assign_data  = load_user_assignments(username)
        due_unix_val = next(
            (a.get('due_unix') for a in assign_data.get('assignments', [])
             if str(a.get('event_id')) == str(event_id)), None
        )
        secs_left = (int(due_unix_val) - int(time.time())) if due_unix_val else None
        show_snooze = secs_left is not None and 0 < secs_left <= 24 * 3600
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id,
                reply_markup=assignment_inline_kb(event_id,
                    is_muted=state.get('muted', False),
                    snoozed_until=state.get('snoozed_until'),
                    show_snooze=show_snooze))
        except Exception:
            pass
        return

    if action == 'snooze' and len(parts) == 3:
        secs = int(parts[2])
        snooze_assignment(username, event_id, secs)
        until_str = unix_to_ist(int(time.time()) + secs)
        _safe_answer(call.id, f"💤 Snoozed until {until_str}")
        state        = get_reminder_state(username, event_id)
        assign_data  = load_user_assignments(username)
        due_unix_val = next(
            (a.get('due_unix') for a in assign_data.get('assignments', [])
             if str(a.get('event_id')) == str(event_id)), None
        )
        secs_left   = (int(due_unix_val) - int(time.time())) if due_unix_val else None
        show_snooze = secs_left is not None and 0 < secs_left <= 24 * 3600
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id,
                reply_markup=assignment_inline_kb(event_id,
                    is_muted=state.get('muted', False),
                    snoozed_until=state.get('snoozed_until'),
                    show_snooze=show_snooze))
        except Exception:
            pass
        return

    if action == 'custom_snooze':
        assign_data  = load_user_assignments(username)
        due_unix_val = next(
            (a.get('due_unix') for a in assign_data.get('assignments', [])
             if str(a.get('event_id')) == str(event_id)), None
        )
        set_state(chat_id, S_CUSTOM_SNOOZE_DT, {'event_id': event_id, 'due_unix': due_unix_val})
        _safe_answer(call.id)
        due_hint = f"\n\u26a0️ Deadline: <b>{unix_to_ist(due_unix_val)}</b>" if due_unix_val else ''
        bot.send_message(chat_id,
            "⏰ <b>Custom Snooze</b> — Enter date and time (IST, 24-hour format):\n\n"
            "Format: <code>DD/MM/YYYY HH:MM</code>\n"
            "Example: <code>24/02/2026 23:30</code>\n"
            "<i>Must be at least 5 minutes before the deadline.</i>"
            + due_hint,
            reply_markup=ForceReply(selective=True),
        )
        return

    if action == 'noop':
        _safe_answer(call.id, "⚠️ No snooze options available — deadline is very close!")
        return

    if action == 'unsnooze':
        state = get_reminder_state(username, event_id)
        state['snoozed_until'] = None
        set_reminder_state(username, event_id, state)
        _safe_answer(call.id, "🔔 Snooze cancelled!")
        assign_data  = load_user_assignments(username)
        due_unix_val = next(
            (a.get('due_unix') for a in assign_data.get('assignments', [])
             if str(a.get('event_id')) == str(event_id)), None
        )
        secs_left   = (int(due_unix_val) - int(time.time())) if due_unix_val else None
        show_snooze = secs_left is not None and 0 < secs_left <= 24 * 3600
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id,
                reply_markup=assignment_inline_kb(event_id, is_muted=state.get('muted', False),
                                                  show_snooze=show_snooze))
        except Exception:
            pass
        return

    if action == 'mute':
        mute_assignment(username, event_id)
        _safe_answer(call.id, "🔕 Reminders muted.")
        assign_data  = load_user_assignments(username)
        due_unix_val = next(
            (a.get('due_unix') for a in assign_data.get('assignments', [])
             if str(a.get('event_id')) == str(event_id)), None
        )
        secs_left   = (int(due_unix_val) - int(time.time())) if due_unix_val else None
        show_snooze = secs_left is not None and 0 < secs_left <= 24 * 3600
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id,
                reply_markup=assignment_inline_kb(event_id, is_muted=True,
                                                  show_snooze=show_snooze))
        except Exception:
            pass
        return

    if action == 'unmute':
        unmute_assignment(username, event_id)
        _safe_answer(call.id, "🔔 Reminders re-enabled!")
        assign_data  = load_user_assignments(username)
        due_unix_val = next(
            (a.get('due_unix') for a in assign_data.get('assignments', [])
             if str(a.get('event_id')) == str(event_id)), None
        )
        secs_left   = (int(due_unix_val) - int(time.time())) if due_unix_val else None
        show_snooze = secs_left is not None and 0 < secs_left <= 24 * 3600
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id,
                reply_markup=assignment_inline_kb(event_id, is_muted=False,
                                                  show_snooze=show_snooze))
        except Exception:
            pass
        return

    _safe_answer(call.id)


def _handle_admin_callback(call, action: str):
    chat_id = call.message.chat.id

    if action == 'users':
        with _bot_users_lock:
            bu = load_bot_users()
        if not bu:
            bot.send_message(chat_id, "No registered users.")
            return
        lines = ["👥 <b>All Users</b>\n"]
        for cid, info in bu.items():
            status     = '🚫 BLOCKED' if info.get('blocked') else '✅ Active'
            email      = info.get('email') or '<i>no email</i>'
            pref_key   = info.get('notif_pref', NOTIF_BOTH)
            pref_label = NOTIF_LABELS.get(pref_key, pref_key)
            lines.append(
                f"{status} <code>{info.get('username','?')}</code>\n"
                f"   Chat ID: <code>{cid}</code>\n"
                f"   Email: {email}\n"
                f"   Notifications: {pref_label}\n"
                f"   Joined: {info.get('registered_at','?')}\n"
            )
        _send_long(chat_id, '\n'.join(lines))
        return

    if action == 'notif_prefs':
        with _bot_users_lock:
            bu = load_bot_users()
        if not bu:
            bot.send_message(chat_id, "No registered users.")
            return
        lines = ["🔔 <b>Notification Preferences (all users)</b>\n"]
        for cid, info in bu.items():
            pref_key   = info.get('notif_pref', NOTIF_BOTH)
            pref_label = NOTIF_LABELS.get(pref_key, pref_key)
            status     = '🚫' if info.get('blocked') else ''
            lines.append(
                f"{status} <code>{info.get('username','?')}</code> — {pref_label}"
            )
        _send_long(chat_id, '\n'.join(lines))
        return

    if action == 'stats':
        with _bot_users_lock:
            bu = load_bot_users()
        total       = len(bu)
        blocked     = sum(1 for u in bu.values() if u.get('blocked'))
        tg_only     = sum(1 for u in bu.values() if u.get('notif_pref') == NOTIF_TELEGRAM)
        email_only  = sum(1 for u in bu.values() if u.get('notif_pref') == NOTIF_EMAIL)
        both        = sum(1 for u in bu.values() if u.get('notif_pref', NOTIF_BOTH) == NOTIF_BOTH)
        bot.send_message(chat_id,
            f"📊 <b>Bot Stats</b>\n\n"
            f"Total users:         <b>{total}</b>\n"
            f"Active:              <b>{total - blocked}</b>\n"
            f"Blocked:             <b>{blocked}</b>\n\n"
            f"🔔 <b>Notification Channels</b>\n"
            f"📲 Telegram only:    <b>{tg_only}</b>\n"
            f"📧 Email only:       <b>{email_only}</b>\n"
            f"🔀 Both:             <b>{both}</b>"
        )
        return

    if action == 'broadcast':
        bot.send_message(chat_id,
            "📢 <b>Broadcast Message</b>\n\n"
            "Choose a template or write a custom message:",
            reply_markup=_broadcast_menu_kb())
        return

    prompt_map = {
        'ask_assign':   ('📋 Enter Moodle username to view assignments:',   'view_assign'),
        'ask_block':    ('🚫 Enter Chat ID or Moodle username to block:',    'block'),
        'ask_unblock':  ('✅ Enter Chat ID or Moodle username to unblock:',  'unblock'),
        'ask_del':      ('🗑 Enter Chat ID or Moodle username to DELETE:',   'del_user'),
    }
    if action in prompt_map:
        prompt_text, adm_action = prompt_map[action]
        set_state(chat_id, S_ADMIN_TEXT, {'adm_action': adm_action})
        bot.send_message(chat_id, prompt_text, reply_markup=ForceReply(selective=True))
        return


def _do_sync(username: str, chat_id: int = None, is_initial: bool = False):
    """Full sync: courses + files + assignments. Notifies via Telegram + email."""
    log.info("Sync start: %s (initial=%s)", username, is_initial)
    try:
        session, sesskey = bot_get_session(username)
    except Exception as e:
        log.error("Session error for %s: %s", username, e)
        if chat_id:
            bot.send_message(chat_id, f"❌ Session error: {e}\nTry /reset_password.")
        return

    user_email       = get_user_email(chat_id) if chat_id else ''
    notif_tg         = should_notify_telegram(chat_id) if chat_id else True
    notif_email_flag = should_notify_email(chat_id) if chat_id else True

    try:
        courses  = fetch_enrolled_courses(session, sesskey)
        snapshot = {
            'last_synced': datetime.now(tz=IST).strftime('%d %b %Y, %I:%M %p') + ' IST',
            'courses':     [],
        }
        for course in courses:
            try:
                course['sections'] = fetch_course_sections(session, sesskey, course['course_id'])
            except Exception:
                course['sections'] = []
            snapshot['courses'].append(course)

        old_course_data = load_user_data(username)
        if old_course_data and not is_initial:
            new_items = detect_new_items(old_course_data, snapshot)
            if new_items and chat_id:
                _ITEM_ICONS = {
                    'file': '📄', 'resource': '📄', 'folder': '📁',
                    'url': '🔗', 'assign': '📋', 'assignment': '📋',
                    'forum': '💬', 'quiz': '❓', 'page': '📃',
                }
                lines = [f"🆕 <b>{len(new_items)} new item(s) added on Moodle!</b>\n"]
                for item in new_items:
                    if item.get('type') == 'new_course':
                        lines.append(f"📚 New course: <b>{item.get('course_name')}</b>")
                    else:
                        mn = (item.get('modname') or '').lower()
                        icon = _ITEM_ICONS.get(mn, '📌')
                        lines.append(
                            f"{icon} <b>{item.get('course_name')}</b> › {item.get('section_title')}\n"
                            f"  {item.get('item_name')} ({item.get('modname','')})"
                        )
                if notif_tg:
                    _send_long(chat_id, '\n'.join(lines))

                for item in new_items:
                    if item.get('type') == 'new_course':
                        continue
                    fake = {
                        'name':    item.get('item_name', ''),
                        'modname': item.get('modname', ''),
                        'url':     item.get('url', ''),
                    }
                    if notif_tg:
                        download_and_send_file(session, fake, chat_id, item.get('course_name', ''))

                if user_email and EMAIL_ENABLED and notif_email_flag:
                    rows = ''.join(
                        f'<tr><td class="val"><strong>'
                        f'{i.get("item_name","New Course")}</strong></td>'
                        f'<td class="val">{i.get("course_name","")} '
                        f'&rsaquo; {i.get("section_title","")}</td></tr>'
                        for i in new_items
                    )
                    content = (
                        f'<p style="color:#374151;font-size:15px;">'
                        f'<span class="badge badge-info">{len(new_items)} new item(s)</span> '
                        f'New course materials have been added to your Moodle courses.</p>'
                        f'<table class="info-table"><tr><td class="lbl">Item</td><td class="lbl">Location</td></tr>'
                        + rows + '</table>'
                    )
                    plain = (
                        f"Moodle Monitor: {len(new_items)} new item(s)\n\n"
                        + '\n'.join(
                            f"• {i.get('item_name','Course')} — {i.get('course_name','')} "
                            f"({i.get('section_title','')})"
                            for i in new_items
                        )
                    )
                    try:
                        send_email(user_email,
                            f"📥 {len(new_items)} new material(s) on Moodle",
                            plain,
                            html=_build_html_email("📥 New Course Materials Added", content))
                    except Exception:
                        pass

        save_user_data(username, snapshot)

    except Exception as e:
        log.error("Course sync failed for %s: %s", username, e)

    try:
        assignments  = fetch_upcoming_assignments(session, sesskey, weeks_ahead=4)
        current_ids  = {str(a['event_id']) for a in assignments}
        old_assigns  = load_user_assignments(username)

        prev_completed = [a for a in old_assigns.get('assignments', []) if a.get('completed')]

        now_ts = int(time.time())
        now_ist = datetime.now(tz=IST).strftime('%d %b %Y, %I:%M %p') + ' IST'
        newly_completed = []
        for a in old_assigns.get('assignments', []):
            if a.get('completed'):
                continue
            if str(a['event_id']) in current_ids:
                continue
            entry = dict(a, completed=True)
            due_unix = a.get('due_unix')
            if due_unix and int(due_unix) < now_ts:
                entry['late'] = True
                entry['late_by'] = _late_by_str(due_unix)
                entry['completed_at'] = now_ist
            else:
                entry['late'] = False
                entry['completed_at'] = now_ist
            newly_completed.append(entry)

        on_time = [a for a in newly_completed if not a.get('late')]
        late    = [a for a in newly_completed if a.get('late')]

        if newly_completed and chat_id and not is_initial:
            for a in newly_completed:
                mark_complete_reminder(username, a['event_id'])

            if on_time:
                lines = [f"✅ <b>{len(on_time)} assignment(s) detected as submitted!</b>\n"]
                for a in on_time:
                    lines.append(f"  • {a['name']}")
                if notif_tg:
                    bot.send_message(chat_id, '\n'.join(lines))

            if late:
                lines = [f"🟡 <b>{len(late)} assignment(s) submitted late!</b>\n"]
                for a in late:
                    lines.append(f"  • {a['name']} — <i>late by {a.get('late_by', '?')}</i>")
                if notif_tg:
                    bot.send_message(chat_id, '\n'.join(lines))

            if user_email and EMAIL_ENABLED and notif_email_flag:
                rows = ''
                for a in newly_completed:
                    if a.get('late'):
                        badge = '<span class="badge" style="background:#f59e0b;color:#fff;">🟡 Late</span>'
                        extra = (f'<tr><td class="lbl">Late by</td><td class="val urgent">{a.get("late_by","")}</td></tr>'
                                 f'<tr><td class="lbl">Detected</td><td class="val">{a.get("completed_at","")}</td></tr>')
                    else:
                        badge = '<span class="badge badge-success">✅ On Time</span>'
                        extra = ''
                    rows += (f'<tr><td class="lbl">Course</td><td class="val">{a.get("course","")} </td></tr>'
                             f'<tr><td class="lbl">Task</td><td class="val">{badge} {a.get("name","")} </td></tr>'
                             + extra)
                content = (
                    f'<p style="color:#374151;font-size:15px;">'
                    f'The following assignment(s) were <strong>detected as submitted</strong>.</p>'
                    f'<table class="info-table">' + rows + '</table>'
                )
                try:
                    send_email(user_email,
                        f"✅ Assignments submitted — Moodle Monitor",
                        '\n'.join(f"• {a['name']}" for a in newly_completed),
                        html=_build_html_email("✅ Assignments Submitted", content))
                except Exception:
                    pass

        if old_assigns and not is_initial:
            new_ones = detect_new_assignments(old_assigns, assignments)
            if new_ones and chat_id:
                for a in new_ones:
                    state        = get_reminder_state(username, a['event_id'])
                    tl           = time_left_str(a.get('due_unix'))
                    secs_left    = (int(a['due_unix']) - int(time.time())) if a.get('due_unix') else None
                    show_snooze  = secs_left is not None and 0 < secs_left <= 24 * 3600
                    text  = (
                        "📋 <b>New Assignment Posted!</b>\n\n"
                        + format_assignment_msg(a, show_header=False)
                        + (f'\n{tl}' if tl else '')
                    )
                    kb = assignment_inline_kb(a['event_id'],
                             is_muted=state.get('muted', False),
                             show_snooze=show_snooze)
                    if notif_tg:
                        bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
                    if user_email and EMAIL_ENABLED and notif_email_flag:
                        content = (
                            f'<p style="color:#374151;font-size:15px;">A new assignment has been posted on Moodle!</p>'
                            f'<table class="info-table">'
                            f'<tr><td class="lbl">Assignment</td><td class="val"><strong>{a.get("name","")}</strong></td></tr>'
                            f'<tr><td class="lbl">Course</td><td class="val">{a.get("course","")}</td></tr>'
                            f'<tr><td class="lbl">Opened</td><td class="val">{a.get("opened","N/A")}</td></tr>'
                            f'<tr><td class="lbl">Due</td><td class="val urgent">{a.get("due","N/A")}</td></tr>'
                            f'</table>'
                            f'<a class="cta" href="{a.get("submit_url","#")}">View Assignment →</a>'
                        )
                        try:
                            send_email(user_email,
                                f"📋 New assignment: {a['name']}",
                                f"New assignment:\n\nCourse: {a.get('course','')}\nTask: {a.get('name','')}\nOpened: {a.get('opened','N/A')}\nDue: {a.get('due','N/A')}\nSubmit: {a.get('submit_url','')}",
                                html=_build_html_email("📋 New Assignment Posted", content))
                        except Exception:
                            pass

        seen = {}
        for a in newly_completed + prev_completed:
            seen[str(a.get('event_id'))] = a
        for a in assignments:
            eid = str(a.get('event_id'))
            if eid not in seen:
                seen[eid] = a
        all_assignments = list(seen.values())
        save_user_assignments(username, {
            'last_synced': datetime.now(tz=IST).strftime('%d %b %Y, %I:%M %p') + ' IST',
            'assignments': all_assignments,
        })
        log.info("Sync done: %s, %d live assignments", username, len(assignments))

    except Exception as e:
        log.error("Assignment sync failed for %s: %s", username, e)


def _background_sync_loop():
    time.sleep(30)            # give bot time to start
    while True:
        with _bot_users_lock:
            bu = load_bot_users()
        for cid_str, info in bu.items():
            username = info.get('username')
            if not username or info.get('blocked'):
                continue
            try:
                _do_sync(username, chat_id=int(cid_str))
            except Exception as e:
                log.error("BG sync error for %s: %s", username, e)
        time.sleep(_get_sync_interval())


def _reminder_loop():
    time.sleep(60)
    while True:
        with _bot_users_lock:
            bu = load_bot_users()

        for cid_str, info in bu.items():
            username          = info.get('username')
            if not username or info.get('blocked'):
                continue
            chat_id           = int(cid_str)
            user_email        = info.get('email', '')
            _pref             = info.get('notif_pref', NOTIF_BOTH)
            notif_tg          = _pref in (NOTIF_TELEGRAM, NOTIF_BOTH)
            notif_email_pref  = _pref in (NOTIF_EMAIL, NOTIF_BOTH)

            assign_data = load_user_assignments(username)
            pending     = [a for a in assign_data.get('assignments', []) if not a.get('completed')]
            now         = int(time.time())
            now_ist     = datetime.fromtimestamp(now, tz=IST)

            for a in pending:
                due_unix = a.get('due_unix')
                if not due_unix:
                    continue
                eid   = a.get('event_id')
                state = get_reminder_state(username, eid)

                if state.get('muted') or state.get('completed'):
                    continue

                seconds_left = int(due_unix) - now

                snoozed_until = state.get('snoozed_until')
                if snoozed_until:
                    if now < snoozed_until:
                        continue
                    state['snoozed_until'] = None
                    set_reminder_state(username, eid, state)
                    try:
                        tl        = time_left_str(due_unix)
                        show_snooze = seconds_left <= 24 * 3600
                        exp_text  = (
                            f"⏰ <b>Snooze ended!</b> Assignment is due soon.\n\n"
                            + format_assignment_msg(a, show_header=False)
                            + (f'\n{tl}' if tl else '')
                        )
                        kb = assignment_inline_kb(eid, is_muted=False, snoozed_until=None,
                                                  show_snooze=show_snooze)
                        if notif_tg:
                            bot.send_message(chat_id, exp_text, reply_markup=kb,
                                             disable_web_page_preview=True)
                        log.info("Snooze-expiry reminder sent to %s for event %s", username, eid)
                        if user_email and EMAIL_ENABLED and notif_email_pref:
                            content = (
                                f'<p style="color:#374151;font-size:15px;">Your snooze has ended!'
                                f' The assignment is due soon.</p>'
                                f'<table class="info-table">'
                                f'<tr><td class="lbl">Assignment</td><td class="val"><strong>{a.get("name","")}</strong></td></tr>'
                                f'<tr><td class="lbl">Course</td><td class="val">{a.get("course","")}</td></tr>'
                                f'<tr><td class="lbl">Due</td><td class="val urgent">{a.get("due","")}</td></tr>'
                                f'</table>'
                                f'<a class="cta" href="{a.get("submit_url","#")}">Submit Assignment \u2192</a>'
                            )
                            try:
                                send_email(user_email,
                                    f"\u23f0 Snooze ended \u2014 {a.get('name','')}",
                                    f"Your snooze has ended.\n\nAssignment: {a.get('name','')}\nCourse: {a.get('course','')}\nDue: {a.get('due','')}\nSubmit: {a.get('submit_url','')}",
                                    html=_build_html_email(f"\u23f0 Snooze Ended \u2014 Act Now!", content))
                            except Exception:
                                pass
                    except Exception as e:
                        log.error("Snooze-expiry send error for %s event %s: %s", username, eid, e)
                    continue

                if seconds_left > 24 * 3600:
                    days_left = seconds_left // 86400
                    date_str  = now_ist.strftime('%Y-%m-%d')

                    for slot_hour, slot_key in [(9, 'daily_am'), (21, 'daily_pm')]:
                        slot_label = '🌅 Morning' if slot_hour == 9 else '🌙 Evening'
                        if now_ist.hour == slot_hour and now_ist.minute < 5:
                            sent_dates = state.get(slot_key, [])
                            if date_str not in sent_dates:
                                try:
                                    tl   = time_left_str(due_unix)
                                    text = (
                                        f"{slot_label} <b>Daily Reminder</b> — "
                                        f"<b>{days_left} day{'s' if days_left != 1 else ''}</b> left!\n\n"
                                        + format_assignment_msg(a, show_header=False)
                                        + (f'\n{tl}' if tl else '')
                                    )
                                    kb = assignment_inline_kb(eid,
                                             is_muted=state.get('muted', False),
                                             show_snooze=False)
                                    if notif_tg:
                                        bot.send_message(chat_id, text, reply_markup=kb,
                                                         disable_web_page_preview=True)
                                    state.setdefault(slot_key, []).append(date_str)
                                    set_reminder_state(username, eid, state)
                                    log.info("Daily %s reminder sent to %s for event %s",
                                             slot_key, username, eid)
                                    if user_email and EMAIL_ENABLED and notif_email_pref:
                                        icon = '\U0001f305' if slot_hour == 9 else '\U0001f319'
                                        content = (
                                            f'<p style="color:#374151;font-size:15px;">'
                                            f'{icon} {slot_label} check-in: you have '
                                            f'<span class="highlight">{days_left} day{"s" if days_left != 1 else ""}</span>'
                                            f' left to complete this assignment.</p>'
                                            f'<table class="info-table">'
                                            f'<tr><td class="lbl">Assignment</td><td class="val"><strong>{a.get("name","")}</strong></td></tr>'
                                            f'<tr><td class="lbl">Course</td><td class="val">{a.get("course","")}</td></tr>'
                                            f'<tr><td class="lbl">Opened</td><td class="val">{a.get("opened","N/A")}</td></tr>'
                                            f'<tr><td class="lbl">Due</td><td class="val urgent">{a.get("due","")}</td></tr>'
                                            f'</table>'
                                            f'<a class="cta" href="{a.get("submit_url","#")}">Open Assignment \u2192</a>'
                                        )
                                        try:
                                            send_email(user_email,
                                                f"{icon} {days_left}d left \u2014 {a.get('name','')}",
                                                f"Daily reminder: {days_left} day(s) left.\n\nAssignment: {a.get('name','')}\nCourse: {a.get('course','')}\nDue: {a.get('due','')}\nSubmit: {a.get('submit_url','')}",
                                                html=_build_html_email(f"Daily Reminder \u2014 {days_left} Day{'s' if days_left != 1 else ''} Left", content))
                                        except Exception:
                                            pass
                                except Exception as e:
                                    log.error("Daily reminder error for %s event %s: %s",
                                              username, eid, e)
                    continue  # don't run threshold logic when > 24hrs left

                if not state.get('sent'):
                    already_past = [
                        lbl for thr, lbl in REMINDER_THRESHOLDS
                        if thr > seconds_left + 120
                    ]
                    if already_past:
                        state['sent'] = already_past
                        set_reminder_state(username, eid, state)

                for threshold_secs, label in REMINDER_THRESHOLDS:
                    if seconds_left <= threshold_secs and label not in state.get('sent', []):
                        try:
                            tl   = time_left_str(due_unix)
                            text = (
                                f"⏰ <b>Deadline Reminder — {label} left!</b>\n\n"
                                + format_assignment_msg(a, show_header=False)
                                + (f'\n{tl}' if tl else '')
                            )
                            kb = assignment_inline_kb(eid, is_muted=False,
                                                      snoozed_until=None, show_snooze=True)
                            if notif_tg:
                                bot.send_message(chat_id, text, reply_markup=kb,
                                                 disable_web_page_preview=True)
                            mark_reminder_sent(username, eid, label)
                            log.info("Threshold reminder %s sent to %s for event %s",
                                     label, username, eid)
                            if user_email and EMAIL_ENABLED and notif_email_pref:
                                urgency_class = 'danger' if threshold_secs <= 3600 else 'warn'
                                content = (
                                    f'<p style="color:#374151;font-size:15px;">'
                                    f'<span class="badge badge-{urgency_class}">{label} left</span> '
                                    f'Your assignment deadline is approaching!</p>'
                                    f'<table class="info-table">'
                                    f'<tr><td class="lbl">Assignment</td><td class="val"><strong>{a.get("name","")}</strong></td></tr>'
                                    f'<tr><td class="lbl">Course</td><td class="val">{a.get("course","")}</td></tr>'
                                    f'<tr><td class="lbl">Due</td><td class="val urgent">{a.get("due","")}</td></tr>'
                                    f'<tr><td class="lbl">Time Left</td><td class="val urgent">{tl}</td></tr>'
                                    f'</table>'
                                    f'<a class="cta" href="{a.get("submit_url","#")}">Submit Now \u2192</a>'
                                )
                                try:
                                    send_email(user_email,
                                        f"\u23f0 {label} left \u2014 {a.get('name','')}",
                                        f"Only {label} left!\n\nAssignment: {a.get('name','')}\nCourse: {a.get('course','')}\nDue: {a.get('due','')}\nSubmit: {a.get('submit_url','')}",
                                        html=_build_html_email(f"\u23f0 {label} Left \u2014 Act Now!", content))
                                except Exception:
                                    pass
                        except Exception as e:
                            log.error("Reminder send error for %s event %s: %s", username, eid, e)
                        break   # one threshold reminder per check-cycle per assignment

            pending_sends = []
            with _todos_lock:
                todos = load_todos(username)
                changed = False
                for t in todos:
                    if t.get('completed'):
                        continue
                    due_unix = t.get('due_unix')
                    if not due_unix:
                        continue
                    seconds_left = int(due_unix) - now
                    if seconds_left <= 0:
                        continue
                    sent = t.get('reminder_sent', {})
                    for threshold, label in TODO_REMINDERS:
                        if seconds_left <= threshold and label not in sent:
                            sent[label] = True
                            t['reminder_sent'] = sent
                            changed = True
                            pending_sends.append((t, label, due_unix))
                            break
                if changed:
                    save_todos(username, todos)

            for t, label, due_unix in pending_sends:
                try:
                    tl = time_left_str(due_unix)
                    text_msg = (
                        f"📝 <b>Todo Reminder</b> — <b>{label}</b> left!\n\n"
                        f"<b>{t['title']}</b>\n"
                        f"📅 Due: <b>{t.get('due_str', '')}</b>\n"
                        f"{tl}"
                    )
                    if notif_tg:
                        bot.send_message(chat_id, text_msg,
                                         disable_web_page_preview=True)
                    log.info("Todo reminder %s sent to %s: %s", label, username, t['title'])
                    if user_email and EMAIL_ENABLED and notif_email_pref:
                        content = (
                            f'<p style="color:#374151;font-size:15px;">'
                            f'⏰ Only <span class="highlight">{label}</span> left for your todo!</p>'
                            f'<table class="info-table">'
                            f'<tr><td class="lbl">Todo</td><td class="val"><strong>{t["title"]}</strong></td></tr>'
                            f'<tr><td class="lbl">Due</td><td class="val urgent">{t.get("due_str","")}</td></tr>'
                            f'</table>'
                        )
                        try:
                            send_email(user_email,
                                f"⏰ {label} left — {t['title']}",
                                f"Only {label} left!\n\nTodo: {t['title']}\nDue: {t.get('due_str','')}",
                                html=_build_html_email(f"⏰ {label} Left — Todo Reminder", content))
                        except Exception:
                            pass
                except Exception as e:
                    log.error("Todo reminder error for %s: %s", username, e)

        time.sleep(60)


if __name__ == '__main__':
    ensure_data_dir()
    migrate_user_files()          # one-time: move flat files into per-user folders
    _load_conv_states()           # restore conversation states from disk
    _load_otp_store()             # restore pending OTPs from disk
    _load_admin_sessions()        # restore admin auth sessions from disk
    log.info("Moodle Monitor Bot starting…")
    threading.Thread(target=_background_sync_loop, name='SyncThread',     daemon=True).start()
    threading.Thread(target=_reminder_loop,        name='ReminderThread', daemon=True).start()
    log.info("Polling started. Token: %s…", BOT_TOKEN[:12])

    while True:
        try:
            bot.infinity_polling(
                timeout=60,
                long_polling_timeout=30,
                logger_level=logging.WARNING,
                restart_on_change=False,
            )
        except KeyboardInterrupt:
            log.info("Bot stopped by user (Ctrl+C).")
            break
        except Exception as e:
            log.error("Polling crashed: %s — retrying in 10 seconds…", e)
            time.sleep(10)
