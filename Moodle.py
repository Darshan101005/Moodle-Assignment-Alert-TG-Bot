
import requests
from bs4 import BeautifulSoup
import re
import json
import os
import sys
import getpass
import tempfile
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

BASE_URL    = 'https://hselearning.sriher.com'
LOGIN_URL   = f'{BASE_URL}/login/index.php'
DASHBOARD_URL = f'{BASE_URL}/my/'
DATA_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'moodle_data')
USERS_FILE  = os.path.join(DATA_DIR, 'users.json')
USER_AGENT  = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/145.0.0.0 Safari/537.36'
)
IST = timezone(timedelta(hours=5, minutes=30))
_COOKIE_DOMAIN = urlparse(BASE_URL).hostname


def unix_to_ist(ts) -> str:
    """Convert a Unix timestamp to IST, formatted as '24 Feb 2026, 12:00 AM'."""
    if not ts:
        return 'N/A'
    dt = datetime.fromtimestamp(int(ts), tz=IST)
    return dt.strftime('%d %b %Y, %I:%M %p')


def parse_moodle_date(raw: str) -> str:
    """
    Parse Moodle's display date string like 'Tuesday, 17 February 2026, 12:00 AM'
    and return it as '17 Feb 2026, 12:00 AM'.
    """
    raw = raw.strip()
    try:
        dt = datetime.strptime(raw, '%A, %d %B %Y, %I:%M %p')
        return dt.strftime('%d %b %Y, %I:%M %p')
    except ValueError:
        return raw  # return as-is if format is unexpected


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def ensure_user_dir(username: str) -> str:
    """Return and create per-user sub-folder: moodle_data/<USERNAME>/"""
    d = os.path.join(DATA_DIR, username.upper())
    os.makedirs(d, exist_ok=True)
    return d


def load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}


def save_users(users: dict):
    ensure_data_dir()
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(users, f, indent=4)
        os.replace(tmp, USERS_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_user_data_path(username: str) -> str:
    return os.path.join(ensure_user_dir(username), 'data.json')


def load_user_data(username: str) -> dict:
    path = get_user_data_path(username)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}


def save_user_data(username: str, data: dict):
    path = get_user_data_path(username)
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_user_assignments_path(username: str) -> str:
    return os.path.join(ensure_user_dir(username), 'assignments.json')


def load_user_assignments(username: str) -> dict:
    path = get_user_assignments_path(username)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}


def save_user_assignments(username: str, data: dict):
    path = get_user_assignments_path(username)
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def migrate_user_files():
    """One-time migration: move <user>_data.json / _assignments.json / _reminders.json
    from flat moodle_data/ into moodle_data/<USER>/ subfolders."""
    import shutil
    if not os.path.isdir(DATA_DIR):
        return
    for fname in os.listdir(DATA_DIR):
        fpath = os.path.join(DATA_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        for suffix, new_name in [
            ('_data.json',        'data.json'),
            ('_assignments.json', 'assignments.json'),
            ('_reminders.json',   'reminders.json'),
        ]:
            if fname.endswith(suffix):
                uname = fname[: -len(suffix)]
                if not uname:
                    continue
                dest_dir  = ensure_user_dir(uname)
                dest_path = os.path.join(dest_dir, new_name)
                if not os.path.exists(dest_path):
                    shutil.move(fpath, dest_path)
                else:
                    os.remove(fpath)   # already migrated; remove leftover
                break


def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'User-Agent': USER_AGENT,
        'Accept': (
            'text/html,application/xhtml+xml,application/xml;'
            'q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
        ),
    })
    return s


def moodle_login(username: str, password: str):
    """
    Perform a fresh login to Moodle.
    Returns (session, sesskey) on success, raises Exception on failure.
    """
    session = create_session()

    login_page = session.get(LOGIN_URL, timeout=30)
    soup = BeautifulSoup(login_page.text, 'html.parser')
    token_input = soup.find('input', {'name': 'logintoken'})
    logintoken = token_input['value'] if token_input else ''

    resp = session.post(LOGIN_URL, data={
        'anchor':     '',
        'logintoken': logintoken,
        'username':   username,
        'password':   password,
    }, timeout=30)

    if 'loginerrormessage' in resp.text or resp.url.startswith(LOGIN_URL):
        raise Exception("Invalid username or password.")

    dashboard = session.get(DASHBOARD_URL, timeout=30)
    cfg_match = re.search(r'M\.cfg\s*=\s*(\{.*?\});', dashboard.text, re.DOTALL)
    if not cfg_match:
        raise Exception("Could not find Moodle config (M.cfg) after login.")

    moodle_cfg = json.loads(cfg_match.group(1))
    sesskey = moodle_cfg.get('sesskey')
    if not sesskey:
        raise Exception("sesskey missing from Moodle config.")

    return session, sesskey


def restore_session(saved_cookies: dict) -> requests.Session:
    """Rebuild a requests.Session from previously saved cookies."""
    session = create_session()
    for name, value in saved_cookies.items():
        session.cookies.set(name, value, domain=_COOKIE_DOMAIN)
    return session


def is_session_valid(session: requests.Session, sesskey: str) -> bool:
    """Quick probe – returns True if the session / sesskey is still alive."""
    try:
        url = (
            f'{BASE_URL}/lib/ajax/service.php'
            f'?sesskey={sesskey}'
            f'&info=core_course_get_enrolled_courses_by_timeline_classification'
        )
        payload = [{
            "index": 0,
            "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
            "args": {
                "offset": 0, "limit": 1,
                "classification": "all", "sort": "fullname",
                "customfieldname": "", "customfieldvalue": "",
            }
        }]
        resp = session.post(url, json=payload, headers={
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        }, timeout=20)
        data = resp.json()
        return isinstance(data, list) and not data[0].get('error')
    except Exception:
        return False


def get_active_session(username: str, password: str, users_db: dict):
    """
    Try to reuse a saved session.  If it has expired, do a fresh login
    and persist the new cookies + sesskey.
    Returns (session, sesskey).
    """
    record = users_db.get(username, {})
    saved_cookies = record.get('cookies', {})
    saved_sesskey = record.get('sesskey', '')

    if saved_cookies and saved_sesskey:
        print("[*] Restoring previous session …")
        session = restore_session(saved_cookies)
        if is_session_valid(session, saved_sesskey):
            print("[+] Session still valid.")
            return session, saved_sesskey
        print("[-] Session expired – re-logging in …")

    print("[*] Logging in to Moodle …")
    session, sesskey = moodle_login(username, password)
    print("[+] Login successful.")

    users_db.setdefault(username, {}).update({
        'cookies':    dict(session.cookies),
        'sesskey':    sesskey,
        'last_login': datetime.now(tz=IST).isoformat(),
    })
    save_users(users_db)
    return session, sesskey


def _ajax_headers(referer: str) -> dict:
    return {
        'Content-Type':      'application/json',
        'X-Requested-With':  'XMLHttpRequest',
        'Accept':            'application/json, text/javascript, */*; q=0.01',
        'Origin':            BASE_URL,
        'Referer':           referer,
    }


def fetch_enrolled_courses(session: requests.Session, sesskey: str) -> list:
    """
    Returns a list of dicts:
        course_id, course_name (shortname), full_display_name,
        course_url, category
    """
    url = (
        f'{BASE_URL}/lib/ajax/service.php'
        f'?sesskey={sesskey}'
        f'&info=core_course_get_enrolled_courses_by_timeline_classification'
    )
    payload = [{
        "index": 0,
        "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
        "args": {
            "offset": 0, "limit": 0,
            "classification": "all", "sort": "fullname",
            "customfieldname": "", "customfieldvalue": "",
            "requiredfields": [
                "id", "fullname", "shortname",
                "showcoursecategory", "showshortname", "visible", "enddate"
            ],
        }
    }]

    resp = session.post(url, json=payload,
                        headers=_ajax_headers(f'{BASE_URL}/my/courses.php'),
                        timeout=30)
    data = resp.json()
    if not (data and isinstance(data, list) and not data[0].get('error')):
        raise Exception(f"Enrolled courses API error: {data}")

    courses = []
    raw = data[0].get('data') or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    for c in raw.get('courses', []):
        courses.append({
            'course_id':        c.get('id'),
            'course_name':      c.get('shortname'),
            'full_display_name': c.get('fullnamedisplay') or c.get('fullname'),
            'course_url':       c.get('viewurl'),
            'category':         c.get('coursecategory'),
        })
    return courses


def fetch_assignment_dates(session: requests.Session, view_url: str) -> dict:
    """
    Fetches assignment view.php and scrapes the opened/due dates from
    <div data-region="activity-dates">.
    Returns dict with 'opened' and 'due' as formatted IST strings.
    """
    result = {'opened': 'N/A', 'due': 'N/A'}
    try:
        resp = session.get(view_url, timeout=20)
        soup = BeautifulSoup(resp.text, 'html.parser')
        region = soup.find('div', {'data-region': 'activity-dates'})
        if not region:
            return result
        for div in region.find_all('div'):
            strong = div.find('strong')
            if not strong:
                continue
            label = strong.get_text(strip=True).rstrip(':')
            raw = div.get_text(separator=' ', strip=True)
            date_part = raw[len(label):].lstrip(':').strip()
            formatted = parse_moodle_date(date_part)
            if label.lower() == 'opened':
                result['opened'] = formatted
            elif label.lower() == 'due':
                result['due'] = formatted
    except Exception:
        pass
    return result


def fetch_upcoming_assignments(session: requests.Session, sesskey: str,
                               weeks_ahead: int = 4) -> list:
    """
    Calls core_calendar_get_action_events_by_timesort.
    Returns a lean list of upcoming assignment deadlines, with opened date
    scraped from each assignment's view.php page.
    """
    now    = int(datetime.now(tz=timezone.utc).timestamp())
    future = now + weeks_ahead * 7 * 24 * 3600

    url = (
        f'{BASE_URL}/lib/ajax/service.php'
        f'?sesskey={sesskey}'
        f'&info=core_calendar_get_action_events_by_timesort'
    )
    payload = [{
        'index': 0,
        'methodname': 'core_calendar_get_action_events_by_timesort',
        'args': {
            'limitnum':                   50,
            'timesortfrom':               now,
            'timesortto':                 future,
            'limittononsuspendedevents':  True,
        },
    }]

    resp = session.post(url, json=payload,
                        headers=_ajax_headers(f'{BASE_URL}/my/'),
                        timeout=30)
    data = resp.json()

    if not data or data[0].get('error'):
        raise Exception(f'Calendar API error: {data}')

    raw = data[0].get('data') or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    events = raw.get('events', [])
    assignments = []
    for ev in events:
        if ev.get('modulename') != 'assign':
            continue
        due_unix  = ev.get('timesort') or ev.get('timestart')
        view_url  = ev.get('url', '')
        submit_url = (ev.get('action') or {}).get('url') or view_url

        dates = fetch_assignment_dates(session, view_url)

        assignments.append({
            'event_id':   ev['id'],
            'name':       ev.get('activityname') or ev.get('name', ''),
            'course':     ev.get('course', {}).get('fullnamedisplay', ''),
            'course_id':  ev.get('course', {}).get('id'),
            'opened':     dates['opened'],
            'due':        dates['due'] if dates['due'] != 'N/A' else unix_to_ist(due_unix),
            'due_unix':   due_unix,
            'overdue':    ev.get('overdue', False),
            'completed':  False,
            'submit_url': submit_url,
            'view_url':   view_url,
        })
    return assignments


def fetch_course_sections(session: requests.Session, sesskey: str,
                           course_id) -> list:
    """
    Calls core_courseformat_get_state for one course.
    Returns a list of section dicts, each containing an 'items' list.
    """
    url = (
        f'{BASE_URL}/lib/ajax/service.php'
        f'?sesskey={sesskey}'
        f'&info=core_courseformat_get_state'
    )
    payload = [{
        "index": 0,
        "methodname": "core_courseformat_get_state",
        "args": {"courseid": int(course_id)},
    }]

    resp = session.post(
        url, json=payload,
        headers=_ajax_headers(f'{BASE_URL}/course/view.php?id={course_id}'),
        timeout=30,
    )
    data = resp.json()

    if not data or data[0].get('error'):
        raise Exception(f"core_courseformat_get_state error: {data}")

    raw = data[0].get('data') or {}
    if isinstance(raw, str):
        raw = json.loads(raw)

    cm_map = {}
    for cm in raw.get('cm', []):
        cm_map[str(cm['id'])] = cm

    sections = []
    for sec in raw.get('section', []):
        items = []
        for cm_id in sec.get('cmlist', []):
            cm = cm_map.get(str(cm_id))
            if cm:
                items.append({
                    'id':      str(cm.get('id')),
                    'name':    cm.get('name'),
                    'modname': cm.get('modname'),
                    'url':     cm.get('url'),
                })
        sections.append({
            'section_id':     str(sec.get('id')),
            'section_number': sec.get('number'),
            'section_title':  sec.get('title'),
            'section_url':    sec.get('sectionurl'),
            'items':          items,
        })
    return sections


def fetch_folder_files(session: requests.Session, folder_url: str) -> list:
    resp = session.get(folder_url, timeout=30)
    soup = BeautifulSoup(resp.text, 'html.parser')
    files = []
    seen = set()
    for a in soup.select('a[href*="pluginfile.php"]'):
        href = a.get('href', '')
        fname = a.get_text(strip=True)
        if not fname:
            img = a.find('img', title=True)
            if img:
                fname = img['title']
        if href and fname and href not in seen:
            seen.add(href)
            files.append({'name': fname, 'url': href})
    return files


def detect_new_assignments(old_snapshot: dict, new_assignments: list) -> list:
    """
    Returns assignments that are new since the last snapshot.
    Compares by event_id, ignoring already-completed ones.
    """
    old_ids = {str(a['event_id']) for a in old_snapshot.get('assignments', [])}
    return [a for a in new_assignments if str(a['event_id']) not in old_ids]


def detect_new_items(old_data: dict, new_data: dict) -> list:
    """
    Compare two course-data snapshots.
    Returns a list of newly added courses / sections / files.
    """
    new_items = []

    old_lookup: dict = {}
    for course in old_data.get('courses', []):
        cid = str(course['course_id'])
        old_lookup[cid] = {}
        for sec in course.get('sections', []):
            sid = str(sec['section_id'])
            old_lookup[cid][sid] = {
                str(item['id']): True for item in sec.get('items', [])
            }

    for course in new_data.get('courses', []):
        cid  = str(course['course_id'])
        cname = course['full_display_name']

        if cid not in old_lookup:
            new_items.append({
                'type':        'new_course',
                'course_id':   cid,
                'course_name': cname,
                'course_url':  course.get('course_url'),
            })
            continue

        for sec in course.get('sections', []):
            sid   = str(sec['section_id'])
            stitle = sec['section_title']

            if sid not in old_lookup[cid]:
                for item in sec.get('items', []):
                    new_items.append({
                        'type':          'new_file',
                        'course_name':   cname,
                        'section_title': stitle,
                        'item_id':       str(item['id']),
                        'item_name':     item['name'],
                        'modname':       item.get('modname'),
                        'url':           item.get('url'),
                    })
                continue

            for item in sec.get('items', []):
                iid = str(item['id'])
                if iid not in old_lookup[cid][sid]:
                    new_items.append({
                        'type':          'new_file',
                        'course_name':   cname,
                        'section_title': stitle,
                        'item_id':       iid,
                        'item_name':     item['name'],
                        'modname':       item.get('modname'),
                        'url':           item.get('url'),
                    })

    return new_items


def run_sync(session: requests.Session, sesskey: str, username: str):
    print("\n[*] Fetching enrolled courses …")
    courses = fetch_enrolled_courses(session, sesskey)
    print(f"[+] {len(courses)} course(s) found.\n")

    snapshot = {
        'last_synced': datetime.now(tz=IST).strftime('%d %b %Y, %I:%M %p') + ' IST',
        'courses': [],
    }

    for i, course in enumerate(courses, 1):
        cid   = course['course_id']
        cname = course['full_display_name']
        print(f"  [{i:>2}/{len(courses)}] {cname}  (id={cid})")
        try:
            course['sections'] = fetch_course_sections(session, sesskey, cid)
        except Exception as e:
            print(f"         [!] Could not fetch sections: {e}")
            course['sections'] = []
        snapshot['courses'].append(course)

    old_data = load_user_data(username)

    if not old_data:
        print("\n[*] First sync — saving baseline snapshot.")
        save_user_data(username, snapshot)
        print(f"[+] Saved → {get_user_data_path(username)}")
        print("\n--- Baseline Summary ---")
        for c in snapshot['courses']:
            sec_cnt  = len(c.get('sections', []))
            file_cnt = sum(len(s.get('items', [])) for s in c.get('sections', []))
            print(f"  {c['full_display_name']:60s}  "
                  f"{sec_cnt} section(s), {file_cnt} item(s)")
    else:
        new_items = detect_new_items(old_data, snapshot)
        if new_items:
            print(f"\n{'='*55}")
            print(f"  🆕  {len(new_items)} NEW item(s) detected!")
            print(f"{'='*55}\n")
            for item in new_items:
                if item['type'] == 'new_course':
                    print(f"  [NEW COURSE]  {item['course_name']}")
                    print(f"    URL : {item['course_url']}")
                else:
                    print(f"  [NEW FILE]  {item['course_name']}  »  {item['section_title']}")
                    print(f"    Name : {item['item_name']}")
                    print(f"    Type : {item['modname']}")
                    print(f"    URL  : {item['url']}")
                print()
        else:
            print("\n[+] No new content since last sync.")

        save_user_data(username, snapshot)
        print(f"[+] Snapshot updated. Last synced: {snapshot['last_synced']}")

    print("\n[*] Fetching upcoming assignment deadlines …")
    try:
        assignments = fetch_upcoming_assignments(session, sesskey, weeks_ahead=4)
    except Exception as e:
        print(f"[!] Could not fetch assignments: {e}")
        assignments = []

    old_assigns = load_user_assignments(username)

    current_ids = {str(a['event_id']) for a in assignments}

    prev_completed = [
        a for a in old_assigns.get('assignments', [])
        if a.get('completed', False)
    ]

    newly_completed = []
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    now_ist_str = datetime.now(tz=IST).strftime('%d %b %Y, %I:%M %p') + ' IST'
    for a in old_assigns.get('assignments', []):
        if a.get('completed', False):
            continue
        if str(a['event_id']) not in current_ids:
            entry = dict(a, completed=True, completed_at=now_ist_str)
            due_unix = a.get('due_unix')
            if due_unix and int(due_unix) < now_ts:
                delta = now_ts - int(due_unix)
                h, _ = divmod(delta, 3600)
                d, h = divmod(h, 24)
                parts = []
                if d: parts.append(f'{d}d')
                if h: parts.append(f'{h}h')
                if not parts: parts.append('< 1h')
                entry['late'] = True
                entry['late_by'] = ' '.join(parts)
            else:
                entry['late'] = False
            newly_completed.append(entry)

    seen = {}
    for a in newly_completed + prev_completed:
        seen[str(a.get('event_id'))] = a
    for a in assignments:
        eid = str(a.get('event_id'))
        if eid not in seen:
            seen[eid] = a
    all_assignments = list(seen.values())

    new_assign_snapshot = {
        'last_synced': snapshot['last_synced'],
        'assignments': all_assignments,
    }

    pending = [a for a in all_assignments if not a.get('completed', False)]
    overdue_count = sum(1 for a in pending if a.get('overdue'))
    if pending:
        print(f"[+] {len(pending)} pending assignment(s)", end='')
        if overdue_count:
            print(f" ({overdue_count} overdue)", end='')
        print(f".\n")
        print(f"  {'Course':<45}  {'Assignment':<45}  {'Opened':<22}  {'Due (IST)'}")
        print(f"  {'-'*45}  {'-'*45}  {'-'*22}  {'-'*22}")
        for a in pending:
            overdue_tag = ' [OVERDUE]' if a.get('overdue') else ''
            print(f"  {a['course'][:45]:<45}  {a['name'][:45]:<45}  "
                  f"{a.get('opened', 'N/A'):<22}  {a['due']}{overdue_tag}")
    else:
        print("[+] No pending assignments in the next 4 weeks.")

    if newly_completed:
        on_time = [a for a in newly_completed if not a.get('late')]
        late_sub = [a for a in newly_completed if a.get('late')]
        if on_time:
            print(f"\n{'='*55}")
            print(f"  ✅  {len(on_time)} assignment(s) submitted on time!")
            print(f"{'='*55}\n")
            for a in on_time:
                print(f"  [COMPLETED]  {a['course']}")
                print(f"    Task : {a['name']}")
                print(f"    Due  : {a['due']}")
                print()
        if late_sub:
            print(f"\n{'='*55}")
            print(f"  🟡  {len(late_sub)} assignment(s) submitted late!")
            print(f"{'='*55}\n")
            for a in late_sub:
                print(f"  [LATE]  {a['course']}")
                print(f"    Task    : {a['name']}")
                print(f"    Due     : {a['due']}")
                print(f"    Late by : {a.get('late_by', '?')}")
                print()

    if old_assigns:
        new_ones = detect_new_assignments(old_assigns, assignments)
        if new_ones:
            print(f"\n{'='*55}")
            print(f"  📋  {len(new_ones)} NEW assignment(s) posted!")
            print(f"{'='*55}\n")
            for a in new_ones:
                print(f"  [NEW ASSIGNMENT]  {a['course']}")
                print(f"    Task   : {a['name']}")
                print(f"    Opened : {a.get('opened', 'N/A')}")
                print(f"    Due    : {a['due']}")
                print(f"    Submit : {a['submit_url']}")
                print()

    save_user_assignments(username, new_assign_snapshot)
    print(f"[+] Assignments saved → {get_user_assignments_path(username)}")


def signup_flow(users_db: dict):
    print("\n--- SIGNUP ---")
    username = input("Moodle username : ").strip()

    if username in users_db:
        print(f"[!] '{username}' already registered. Choose Login instead.")
        return None, None, None

    password = getpass.getpass("Moodle password : ")

    print("[*] Verifying credentials with Moodle …")
    try:
        session, sesskey = moodle_login(username, password)
    except Exception as e:
        print(f"[-] Signup failed: {e}")
        return None, None, None

    users_db[username] = {
        'username':   username,
        'password':   password,          # stored locally for auto re-login
        'cookies':    dict(session.cookies),
        'sesskey':    sesskey,
        'last_login': datetime.now(tz=IST).isoformat(),
    }
    save_users(users_db)
    print(f"[+] Signup successful! Welcome, {username}.")
    return session, sesskey, username


def login_flow(users_db: dict):
    print("\n--- LOGIN ---")
    username = input("Moodle username : ").strip()

    if username not in users_db:
        print(f"[!] '{username}' not found. Please Signup first.")
        return None, None, None

    password = getpass.getpass("Moodle password : ")

    users_db[username]['password'] = password

    try:
        session, sesskey = get_active_session(username, password, users_db)
        return session, sesskey, username
    except Exception as e:
        print(f"[-] Login failed: {e}")
        return None, None, None


def load_saved_session(users_db: dict):
    """Load saved credentials and return an active session for syncing."""
    print("\n--- SYNC ---")
    if not users_db:
        print("[!] No registered users found. Please Signup first.")
        return None, None, None

    if len(users_db) == 1:
        username = next(iter(users_db))
        print(f"[*] Using saved account: {username}")
    else:
        print("Registered users: " + ', '.join(users_db.keys()))
        username = input("Moodle username : ").strip()
        if username not in users_db:
            print(f"[!] '{username}' not found. Please Signup first.")
            return None, None, None

    password = users_db[username].get('password', '')
    if not password:
        print("[!] No saved password found. Please use Login instead.")
        return None, None, None

    try:
        session, sesskey = get_active_session(username, password, users_db)
        return session, sesskey, username
    except Exception as e:
        print(f"[-] Sync failed: {e}")
        return None, None, None


def main():
    ensure_data_dir()
    migrate_user_files()          # one-time: move flat files into per-user folders
    users_db = load_users()

    print()
    print("=" * 50)
    print("       Moodle Course Monitor")
    print("=" * 50)
    print()
    print("  1. Signup  (first time)")
    print("  2. Login   (enter username + password)")
    print("  3. Sync    (username only, uses saved credentials)")
    print()

    choice = input("Enter choice [1/2/3]: ").strip()

    if choice == '1':
        session, sesskey, username = signup_flow(users_db)
    elif choice == '2':
        session, sesskey, username = login_flow(users_db)
    elif choice == '3':
        session, sesskey, username = load_saved_session(users_db)
    else:
        print("[-] Invalid choice. Exiting.")
        sys.exit(1)

    if not all([session, sesskey, username]):
        sys.exit(1)

    run_sync(session, sesskey, username)


if __name__ == '__main__':
    main()
