import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "users.db"
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,20}$")
DB_LOCK = Lock()

RESERVED_USERNAMES = {"hr", "all"}
RESERVED_SHEETS = {"all", "other"}
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &/\-]{1,39}$")
SEED_DEPARTMENTS = [
    ("web", "Web"),
    ("social", "Social"),
    ("accounting", "Accounting"),
    ("sales", "Sales"),
    ("hr", "HR"),
]


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with DB_LOCK:
        conn = db()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                team TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'team',
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                value TEXT UNIQUE NOT NULL,
                label TEXT UNIQUE NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                device TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(device, fingerprint)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS holidays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS form_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL,
                submitted_at TEXT NOT NULL,
                fingerprint_id TEXT NOT NULL,
                device TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                team TEXT NOT NULL DEFAULT '',
                request_type TEXT NOT NULL,
                request_date TEXT NOT NULL DEFAULT '',
                punch_in_time TEXT NOT NULL DEFAULT '',
                punch_out_time TEXT NOT NULL DEFAULT '',
                from_time TEXT NOT NULL DEFAULT '',
                to_time TEXT NOT NULL DEFAULT '',
                start_date TEXT NOT NULL DEFAULT '',
                end_date TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Pending',
                reviewed_by TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL DEFAULT '',
                rejection_reason TEXT NOT NULL DEFAULT '',
                sync_status TEXT NOT NULL DEFAULT 'pending',
                sync_error TEXT NOT NULL DEFAULT '',
                sync_attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_form_requests_fp ON form_requests(fingerprint_id, start_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_form_requests_sync ON form_requests(sync_status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_form_requests_dept ON form_requests(department)"
        )
        _ensure_employee_team_column(conn)
        _ensure_employee_leave_days_column(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payroll_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_start TEXT NOT NULL,
                device TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                department TEXT NOT NULL DEFAULT '',
                penalty_days REAL NOT NULL DEFAULT 0,
                bonus_days REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                updated_by TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                UNIQUE(cycle_start, device, fingerprint)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payroll_adjustments_cycle ON payroll_adjustments(cycle_start)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('leave_balance_visible', '0')"
        )
        conn.commit()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for value, label in SEED_DEPARTMENTS:
            conn.execute(
                "INSERT OR IGNORE INTO departments (value, label, active, created_at) VALUES (?, ?, 1, ?)",
                (value, label, now),
            )
        conn.commit()
        conn.close()


DEFAULT_LEAVE_DAYS = 15


def _ensure_employee_team_column(conn) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(employees)")}
    if "team" not in columns:
        conn.execute("ALTER TABLE employees ADD COLUMN team TEXT NOT NULL DEFAULT ''")


def _ensure_employee_leave_days_column(conn) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(employees)")}
    if "leave_days" not in columns:
        conn.execute(
            f"ALTER TABLE employees ADD COLUMN leave_days INTEGER NOT NULL DEFAULT {DEFAULT_LEAVE_DAYS}"
        )


def get_setting(key: str, default: str = "") -> str:
    init_db()
    conn = db()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row is None:
        return default
    return str(row["value"] if row["value"] is not None else default)


def set_setting(key: str, value: str) -> None:
    init_db()
    with DB_LOCK:
        conn = db()
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )
        conn.commit()
        conn.close()


def leave_balance_visible() -> bool:
    return get_setting("leave_balance_visible", "0") == "1"


def set_leave_balance_visible(visible: bool) -> None:
    set_setting("leave_balance_visible", "1" if visible else "0")


def normalize_leave_days(value, default: int = DEFAULT_LEAVE_DAYS) -> int:
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(0, min(days, 365))


def _row_to_user(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row["name"],
        "department": row["department"],
        "team": row["team"],
        "role": row["role"],
        "password_hash": row["password_hash"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


def list_users() -> list:
    init_db()
    conn = db()
    rows = conn.execute("SELECT * FROM users ORDER BY department, name").fetchall()
    conn.close()
    return [_row_to_user(row) for row in rows]


def find_user(username: str) -> dict | None:
    init_db()
    conn = db()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND active = 1",
        (username.strip().lower(),),
    ).fetchone()
    conn.close()
    return _row_to_user(row) if row else None


def teams_by_department() -> dict:
    init_db()
    conn = db()
    rows = conn.execute(
        """
        SELECT department, team, name
        FROM users
        WHERE role = 'team' AND active = 1
        ORDER BY department, name
        """
    ).fetchall()
    conn.close()
    grouped = {}
    for row in rows:
        grouped.setdefault(row["department"], [])
        if not any(item["value"] == row["team"] for item in grouped[row["department"]]):
            grouped[row["department"]].append({
                "value": row["team"],
                "label": row["team"],
            })
    return grouped


def slug_from_label(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (label or "").strip().lower()).strip("-")
    return slug[:20]


def username_taken(username: str) -> bool:
    username = normalize_username(username)
    if not username or username in RESERVED_USERNAMES:
        return True
    init_db()
    conn = db()
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return bool(row)


def department_manager(label: str) -> dict | None:
    init_db()
    conn = db()
    row = conn.execute(
        """
        SELECT * FROM users
        WHERE department = ? AND role = 'department' AND active = 1
        LIMIT 1
        """,
        ((label or "").strip(),),
    ).fetchone()
    conn.close()
    return _row_to_user(row) if row else None


def find_department(label: str) -> dict | None:
    init_db()
    conn = db()
    row = conn.execute(
        "SELECT * FROM departments WHERE lower(label) = lower(?) OR value = ?",
        ((label or "").strip(), slug_from_label(label)),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "value": row["value"],
        "label": row["label"],
        "active": bool(row["active"]),
    }


def list_custom_departments(active_only: bool = False) -> list:
    init_db()
    conn = db()
    sql = "SELECT * FROM departments"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY label"
    rows = conn.execute(sql).fetchall()
    managers = conn.execute(
        "SELECT * FROM users WHERE role = 'department' AND active = 1"
    ).fetchall()
    conn.close()
    by_label = {row["department"]: _row_to_user(row) for row in managers}
    return [
        {
            "id": row["id"],
            "value": row["value"],
            "label": row["label"],
            "active": bool(row["active"]),
            "manager": by_label.get(row["label"]),
        }
        for row in rows
    ]


def validate_new_department(label: str, name: str, username: str, password: str) -> list:
    errors = []
    label = (label or "").strip()
    name = (name or "").strip()
    username = normalize_username(username)
    password = password or ""
    value = slug_from_label(label)
    wants_manager = bool(name or username or password)

    if not LABEL_RE.match(label):
        errors.append("Department name must be 2-40 characters: letters, numbers, spaces, &, / or dash.")
    elif label.lower() in RESERVED_SHEETS or value in RESERVED_SHEETS:
        errors.append("This department name is reserved.")
    elif len(value) < 3:
        errors.append("Department name is too short.")
    existing = find_department(label)
    if existing and not wants_manager:
        errors.append("This department already exists. Add a manager below if you want a separate login.")
    if wants_manager:
        if not name:
            errors.append("Manager name is required, or leave all manager fields empty to keep it under HR Admin.")
        if not USERNAME_RE.match(username):
            errors.append("Username must be 3-20 characters: letters, numbers, dot, dash, or underscore.")
        elif username_taken(username):
            errors.append("This username is reserved or already exists.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if existing and department_manager(existing["label"]):
            errors.append("This department already has a manager. Reset their password from the list below.")
    return errors


def create_department(label: str, name: str, username: str, password: str) -> dict:
    init_db()
    label = label.strip()
    name = (name or "").strip()
    username = normalize_username(username)
    password = password or ""
    wants_manager = bool(name or username or password)
    value = slug_from_label(label)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    created_department = False
    with DB_LOCK:
        conn = db()
        try:
            existing = conn.execute(
                "SELECT label FROM departments WHERE lower(label) = lower(?) OR value = ?",
                (label, value),
            ).fetchone()
            if existing:
                label = existing["label"]
            else:
                conn.execute(
                    "INSERT INTO departments (value, label, active, created_at) VALUES (?, ?, 1, ?)",
                    (value, label, now),
                )
                created_department = True
            if not wants_manager:
                if not created_department:
                    conn.close()
                    raise ValueError("This department already exists.")
                conn.commit()
                conn.close()
                return {"value": value, "label": label, "username": "", "created_department": True}
            manager = conn.execute(
                "SELECT id FROM users WHERE department = ? AND role = 'department' AND active = 1",
                (label,),
            ).fetchone()
            if manager:
                conn.close()
                raise ValueError("This department already has a manager.")
            conn.execute(
                """
                INSERT INTO users (username, name, department, team, role, password_hash, active, created_at)
                VALUES (?, ?, ?, '', 'department', ?, 1, ?)
                """,
                (username, name, label, generate_password_hash(password), now),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            conn.close()
            raise ValueError("Department or username already exists.") from exc
        conn.close()
    return {
        "value": value,
        "label": label,
        "username": username,
        "created_department": created_department,
    }


def toggle_department(dept_id: int) -> bool:
    init_db()
    with DB_LOCK:
        conn = db()
        cursor = conn.execute(
            "UPDATE departments SET active = CASE WHEN active = 1 THEN 0 ELSE 1 END WHERE id = ?",
            (dept_id,),
        )
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated


def rename_department(dept_id: int, new_label: str) -> str:
    new_label = (new_label or "").strip()
    value = slug_from_label(new_label)
    if not LABEL_RE.match(new_label):
        raise ValueError("Department name must be 2-40 characters: letters, numbers, spaces, &, / or dash.")
    if new_label.lower() in RESERVED_SHEETS or value in RESERVED_SHEETS:
        raise ValueError("This department name is reserved.")
    if len(value) < 3:
        raise ValueError("Department name is too short.")
    init_db()
    with DB_LOCK:
        conn = db()
        row = conn.execute("SELECT * FROM departments WHERE id = ?", (dept_id,)).fetchone()
        if not row:
            conn.close()
            raise ValueError("Department not found.")
        old_label = row["label"]
        if old_label == new_label:
            conn.close()
            return old_label
        clash = conn.execute(
            "SELECT id FROM departments WHERE id != ? AND lower(label) = lower(?)",
            (dept_id, new_label),
        ).fetchone()
        if clash:
            conn.close()
            raise ValueError("Another department already uses this name.")
        conn.execute("UPDATE departments SET label = ? WHERE id = ?", (new_label, dept_id))
        conn.execute("UPDATE users SET department = ? WHERE department = ?", (new_label, old_label))
        conn.execute("UPDATE employees SET department = ? WHERE department = ?", (new_label, old_label))
        conn.commit()
        conn.close()
    return new_label


def update_person(user_id: int, name: str, team: str | None = None) -> bool:
    name = (name or "").strip()
    if not name:
        raise ValueError("Name is required.")
    init_db()
    with DB_LOCK:
        conn = db()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ? AND role IN ('team', 'department')",
            (user_id,),
        ).fetchone()
        if not row:
            conn.close()
            return False
        if row["role"] == "team":
            team = (team or "").strip()
            if not team:
                raise ValueError("Team name is required.")
            if len(team) > 60:
                raise ValueError("Team name is too long.")
            conn.execute("UPDATE users SET name = ?, team = ? WHERE id = ?", (name, team, user_id))
        else:
            conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
        conn.commit()
        conn.close()
    return True


def normalize_username(value: str) -> str:
    return (value or "").strip().lower()


def validate_new_user(username: str, name: str, department: str, team: str, password: str, departments: set) -> list:
    errors = []
    username = normalize_username(username)
    name = (name or "").strip()
    team = (team or "").strip()
    password = password or ""

    if not USERNAME_RE.match(username):
        errors.append("Username must be 3-20 characters: letters, numbers, dot, dash, or underscore.")
    elif username_taken(username):
        errors.append("This username is reserved or already exists.")
    if not name:
        errors.append("Name is required.")
    if department not in departments:
        errors.append("Please choose a valid department.")
    if not team:
        errors.append("Team name is required.")
    elif len(team) > 60:
        errors.append("Team name is too long.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    return errors


def create_user(username: str, name: str, department: str, team: str, password: str) -> None:
    init_db()
    username = normalize_username(username)
    with DB_LOCK:
        conn = db()
        try:
            conn.execute(
                """
                INSERT INTO users (username, name, department, team, role, password_hash, active, created_at)
                VALUES (?, ?, ?, ?, 'team', ?, 1, ?)
                """,
                (
                    username,
                    name.strip(),
                    department,
                    team.strip(),
                    generate_password_hash(password),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.close()
            raise ValueError("Username already exists.") from exc
        conn.close()


def set_password(user_id: int, password: str) -> bool:
    if len(password or "") < 8:
        return False
    init_db()
    with DB_LOCK:
        conn = db()
        cursor = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ? AND role IN ('team', 'department')",
            (generate_password_hash(password), user_id),
        )
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated


def ensure_department(label: str) -> str:
    label = (label or "").strip()
    value = slug_from_label(label)
    if not LABEL_RE.match(label) or label.lower() in RESERVED_SHEETS or value in RESERVED_SHEETS:
        raise ValueError("Invalid department name.")
    if len(value) < 3:
        raise ValueError("Department name is too short.")
    init_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with DB_LOCK:
        conn = db()
        existing = conn.execute(
            "SELECT label FROM departments WHERE lower(label) = lower(?) OR value = ?",
            (label, value),
        ).fetchone()
        if existing:
            canonical = existing["label"]
            conn.close()
            return canonical
        conn.execute(
            "INSERT INTO departments (value, label, active, created_at) VALUES (?, ?, 1, ?)",
            (value, label, now),
        )
        conn.commit()
        conn.close()
    return label


def import_people(rows: list) -> tuple[dict, list]:
    created = {"managers": 0, "teams": 0, "departments": 0}
    errors = []
    pending = []
    seen = set()

    for index, raw in enumerate(rows, start=2):
        row = {str(key or "").strip().lower(): str(value or "").strip() for key, value in raw.items()}
        if not any(row.get(key) for key in ("role", "department", "team", "name", "username", "password")):
            continue
        role = (row.get("role") or "").lower()
        if role in {"manager", "dept", "department manager"}:
            role = "department"
        if role in {"team_leader", "leader", "team leader"}:
            role = "team"
        if role not in {"department", "team"}:
            errors.append(f"Row {index}: role must be department or team.")
            continue
        pending.append({
            "index": index,
            "role": role,
            "department": row.get("department") or "",
            "team": row.get("team") or "",
            "name": row.get("name") or "",
            "username": row.get("username") or "",
            "password": row.get("password") or "",
        })

    pending.sort(key=lambda item: 0 if item["role"] == "department" else 1)
    labels = {item["label"] for item in list_custom_departments(active_only=True)}

    for item in pending:
        index = item["index"]
        username = normalize_username(item["username"])
        if username in seen:
            errors.append(f"Row {index}: username {username} is duplicated in the file.")
            continue
        if item["role"] == "department":
            issues = validate_new_department(item["department"], item["name"], item["username"], item["password"])
            if issues:
                errors.append(f"Row {index}: " + " ".join(issues))
                continue
            try:
                created_dept = create_department(item["department"], item["name"], item["username"], item["password"])
            except ValueError as exc:
                errors.append(f"Row {index}: {exc}")
                continue
            seen.add(created_dept["username"])
            labels.add(created_dept["label"])
            created["managers"] += 1
            created["departments"] += 1
            continue

        try:
            department = ensure_department(item["department"])
        except ValueError as exc:
            errors.append(f"Row {index}: {exc}")
            continue
        labels.add(department)
        issues = validate_new_user(item["username"], item["name"], department, item["team"], item["password"], labels)
        if issues:
            errors.append(f"Row {index}: " + " ".join(issues))
            continue
        try:
            create_user(item["username"], item["name"], department, item["team"], item["password"])
        except ValueError as exc:
            errors.append(f"Row {index}: {exc}")
            continue
        seen.add(username)
        created["teams"] += 1

    return created, errors


def toggle_user(user_id: int) -> bool:
    init_db()
    with DB_LOCK:
        conn = db()
        cursor = conn.execute(
            "UPDATE users SET active = CASE WHEN active = 1 THEN 0 ELSE 1 END WHERE id = ? AND role IN ('team', 'department')",
            (user_id,),
        )
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated


def delete_user(user_id: int) -> bool:
    init_db()
    with DB_LOCK:
        conn = db()
        cursor = conn.execute(
            "DELETE FROM users WHERE id = ? AND role IN ('team', 'department')",
            (user_id,),
        )
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted


DEFAULT_DEVICES = ["F8", "F9", "Maadi"]
NAMED_DEVICES = {
    "MAADI": "Maadi",
}
MAX_EMPLOYEE_IMPORT_ROWS = 800


def normalize_fingerprint_id(value) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].replace(".", "", 1).isdigit():
        text = text[:-2]
    return re.sub(r"\D", "", text)


def normalize_device(value: str) -> str:
    text = re.sub(r"\s+", "", (value or "").strip())
    upper = text.upper()
    if upper in NAMED_DEVICES:
        return NAMED_DEVICES[upper]
    if re.fullmatch(r"F\d{1,3}", upper):
        return upper
    return ""


def normalize_person_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


ENGLISH_NAME_RE = re.compile(r"[A-Za-z]+(?:[ .'\-][A-Za-z]+)*")


def is_english_person_name(value: str) -> bool:
    text = re.sub(r"\s+", " ", (value or "").strip())
    return bool(text) and bool(ENGLISH_NAME_RE.fullmatch(text))


def names_match(left: str, right: str) -> bool:
    first = normalize_person_name(left)
    second = normalize_person_name(right)
    if not first or not second:
        return False
    if first == second:
        return True
    return len(first) >= 4 and len(second) >= 4 and (first in second or second in first)


def normalize_employee_team(department: str, team: str) -> str:
    text = (team or "").strip()
    if not text:
        return ""
    for item in teams_by_department().get((department or "").strip(), []):
        if item["value"].lower() == text.lower():
            return item["value"]
    return text


def _row_to_employee(row) -> dict:
    leave_days = row["leave_days"] if "leave_days" in row.keys() else DEFAULT_LEAVE_DAYS
    return {
        "id": row["id"],
        "name": row["name"],
        "department": row["department"],
        "fingerprint": row["fingerprint"],
        "device": row["device"],
        "team": row["team"] if "team" in row.keys() else "",
        "leave_days": normalize_leave_days(leave_days),
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


def employee_count() -> int:
    init_db()
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM employees WHERE active = 1").fetchone()[0]
    conn.close()
    return int(total)


def list_employees() -> list:
    init_db()
    conn = db()
    rows = conn.execute(
        "SELECT * FROM employees ORDER BY department, name, device"
    ).fetchall()
    conn.close()
    return [_row_to_employee(row) for row in rows]


def list_devices() -> list:
    init_db()
    conn = db()
    rows = conn.execute(
        "SELECT DISTINCT device FROM employees ORDER BY device"
    ).fetchall()
    conn.close()
    devices = [row["device"] for row in rows if row["device"]]
    for item in DEFAULT_DEVICES:
        if item not in devices:
            devices.append(item)
    return devices


def match_employee(name: str, fingerprint: str, department: str, team: str = "", device: str = "") -> dict | None:
    fingerprint = normalize_fingerprint_id(fingerprint)
    if not fingerprint:
        return None
    init_db()
    conn = db()
    rows = conn.execute(
        "SELECT * FROM employees WHERE fingerprint = ? AND active = 1",
        (fingerprint,),
    ).fetchall()
    conn.close()
    people = [_row_to_employee(row) for row in rows]
    if not people:
        return None
    department_key = (department or "").strip().lower()
    in_department = [
        person for person in people
        if person["department"].strip().lower() == department_key
    ]
    if name:
        named = [person for person in in_department if names_match(name, person["name"])]
        if named:
            in_department = named
        elif in_department:
            return None
    team_key = (team or "").strip().lower()
    if team_key:
        in_department = [
            person for person in in_department
            if (person.get("team") or "").strip().lower() == team_key
        ]
    elif len(in_department) > 1:
        blank_team = [
            person for person in in_department
            if not (person.get("team") or "").strip()
        ]
        if blank_team:
            in_department = blank_team
    device_key = normalize_device(device)
    if device_key:
        in_department = [
            person for person in in_department
            if normalize_device(person.get("device") or "") == device_key
        ]
    if len(in_department) == 1:
        return in_department[0]
    if len(in_department) > 1:
        return in_department[0]
    return None


def departments_for_fingerprint(fingerprint: str) -> list:
    fingerprint = normalize_fingerprint_id(fingerprint)
    if not fingerprint:
        return []
    init_db()
    conn = db()
    rows = conn.execute(
        "SELECT * FROM employees WHERE fingerprint = ? AND active = 1",
        (fingerprint,),
    ).fetchall()
    conn.close()
    departments = []
    for row in rows:
        label = (_row_to_employee(row).get("department") or "").strip()
        if label and label not in departments:
            departments.append(label)
    return departments


def departments_for_person(name: str, fingerprint: str) -> list:
    fingerprint = normalize_fingerprint_id(fingerprint)
    if not fingerprint:
        return []
    init_db()
    conn = db()
    rows = conn.execute(
        "SELECT * FROM employees WHERE fingerprint = ? AND active = 1",
        (fingerprint,),
    ).fetchall()
    conn.close()
    departments = []
    for row in rows:
        person = _row_to_employee(row)
        if not names_match(name, person["name"]):
            continue
        label = (person["department"] or "").strip()
        if label and label not in departments:
            departments.append(label)
    return departments


def validate_employee(name: str, department: str, fingerprint: str, device: str, departments: set, employee_id: int | None = None, team: str = "") -> list:
    errors = []
    name = (name or "").strip()
    department = (department or "").strip()
    fingerprint = normalize_fingerprint_id(fingerprint)
    device = normalize_device(device)
    team = normalize_employee_team(department, team)
    if not name:
        errors.append("Employee name is required.")
    elif len(name) > 80:
        errors.append("Employee name is too long.")
    elif not is_english_person_name(name):
        errors.append("Employee name must be in English letters only.")
    if department not in departments:
        errors.append("Please choose a valid department.")
    if not fingerprint or not fingerprint.isdigit():
        errors.append("Fingerprint number must contain digits only.")
    elif len(fingerprint) > 10:
        errors.append("Fingerprint number is too long.")
    if not device:
        errors.append("Device must be F8, F9, Maadi, or similar.")
    if team:
        if len(team) > 60:
            errors.append("Team name is too long.")
        allowed = teams_by_department().get(department, [])
        if not allowed:
            errors.append(f"{department} has no teams. Leave the team column empty.")
        elif not any(item["value"].lower() == team.lower() for item in allowed):
            errors.append(f"Team '{team}' is not registered under {department}.")
    if errors:
        return errors
    init_db()
    conn = db()
    clash = conn.execute(
        "SELECT id FROM employees WHERE device = ? AND fingerprint = ? AND id != ?",
        (device, fingerprint, employee_id or 0),
    ).fetchone()
    others = conn.execute(
        "SELECT name, department FROM employees WHERE fingerprint = ? AND active = 1 AND id != ?",
        (fingerprint, employee_id or 0),
    ).fetchall()
    conn.close()
    if clash:
        errors.append(f"Fingerprint {fingerprint} is already registered on {device}.")
    for other in others:
        if names_match(name, other["name"]) and (other["department"] or "").strip().lower() != department.strip().lower():
            errors.append(
                f"{name} (fingerprint {fingerprint}) is already registered in {other['department']} "
                "and cannot be assigned to another department."
            )
            break
    return errors


def find_employees_by_person(name: str, fingerprint: str) -> list:
    fingerprint = normalize_fingerprint_id(fingerprint)
    if not fingerprint:
        return []
    init_db()
    conn = db()
    rows = conn.execute(
        "SELECT * FROM employees WHERE fingerprint = ? AND active = 1",
        (fingerprint,),
    ).fetchall()
    conn.close()
    return [
        person for person in (_row_to_employee(row) for row in rows)
        if names_match(name, person["name"])
    ]


def create_employee(name: str, department: str, fingerprint: str, device: str, team: str = "", leave_days=None) -> dict:
    name = name.strip()
    department = department.strip()
    fingerprint = normalize_fingerprint_id(fingerprint)
    device = normalize_device(device)
    team = normalize_employee_team(department, team)
    leave_days = normalize_leave_days(leave_days if leave_days is not None else DEFAULT_LEAVE_DAYS)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    init_db()
    with DB_LOCK:
        conn = db()
        try:
            cursor = conn.execute(
                """
                INSERT INTO employees (name, department, fingerprint, device, team, leave_days, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (name, department, fingerprint, device, team, leave_days, now),
            )
            conn.commit()
            employee_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            conn.close()
            raise ValueError(f"Fingerprint {fingerprint} is already registered on {device}.") from exc
        conn.close()
    return {
        "id": employee_id,
        "name": name,
        "department": department,
        "fingerprint": fingerprint,
        "device": device,
        "team": team,
        "leave_days": leave_days,
    }


def update_employee(employee_id: int, name: str, department: str, fingerprint: str, device: str, team: str = "", leave_days=None) -> bool:
    name = name.strip()
    department = department.strip()
    fingerprint = normalize_fingerprint_id(fingerprint)
    device = normalize_device(device)
    team = normalize_employee_team(department, team)
    leave_days = normalize_leave_days(leave_days if leave_days is not None else DEFAULT_LEAVE_DAYS)
    init_db()
    with DB_LOCK:
        conn = db()
        try:
            cursor = conn.execute(
                """
                UPDATE employees
                SET name = ?, department = ?, fingerprint = ?, device = ?, team = ?, leave_days = ?
                WHERE id = ?
                """,
                (name, department, fingerprint, device, team, leave_days, employee_id),
            )
            conn.commit()
            updated = cursor.rowcount > 0
        except sqlite3.IntegrityError as exc:
            conn.close()
            raise ValueError(f"Fingerprint {fingerprint} is already registered on {device}.") from exc
        conn.close()
    return updated


def delete_employee(employee_id: int) -> bool:
    return delete_employees([employee_id]) == 1


def delete_employees(employee_ids: list) -> int:
    ids = []
    for value in employee_ids:
        try:
            employee_id = int(value)
        except (TypeError, ValueError):
            continue
        if employee_id > 0 and employee_id not in ids:
            ids.append(employee_id)
    if not ids:
        return 0
    init_db()
    with DB_LOCK:
        conn = db()
        placeholders = ",".join("?" * len(ids))
        cursor = conn.execute(f"DELETE FROM employees WHERE id IN ({placeholders})", ids)
        conn.commit()
        deleted = cursor.rowcount
        conn.close()
        return deleted


def delete_all_employees() -> int:
    init_db()
    with DB_LOCK:
        conn = db()
        cursor = conn.execute("DELETE FROM employees")
        conn.commit()
        deleted = cursor.rowcount
        conn.close()
        return deleted


def import_employees(rows: list, departments: set) -> tuple[dict, list]:
    created = {"added": 0, "updated": 0}
    errors = []
    seen = set()

    for index, raw in enumerate(rows, start=2):
        row = {str(key or "").strip().lower(): str(value or "").strip() for key, value in raw.items()}
        if not any(row.get(key) for key in ("name", "department", "fingerprint", "device")):
            continue
        name = row.get("name") or ""
        department = row.get("department") or ""
        team = row.get("team") or ""
        fingerprint = normalize_fingerprint_id(row.get("fingerprint") or row.get("ac-no.") or row.get("ac-no") or "")
        device = normalize_device(row.get("device") or row.get("machine") or "")
        if department not in departments:
            canonical = next((item for item in departments if item.lower() == department.lower()), "")
            department = canonical or department
        team = normalize_employee_team(department, team)
        key = (device, fingerprint)
        if fingerprint and device and key in seen:
            errors.append(f"Row {index}: fingerprint {fingerprint} on {device} is duplicated in the file.")
            continue
        init_db()
        conn = db()
        existing = conn.execute(
            "SELECT id FROM employees WHERE device = ? AND fingerprint = ?",
            (device, fingerprint),
        ).fetchone() if device and fingerprint else None
        conn.close()
        issues = validate_employee(
            name,
            department,
            fingerprint,
            device,
            departments,
            existing["id"] if existing else None,
            team,
        )
        if issues:
            errors.append(f"Row {index}: " + " ".join(issues))
            continue
        seen.add(key)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with DB_LOCK:
            conn = db()
            if existing:
                conn.execute(
                    """
                    UPDATE employees
                    SET name = ?, department = ?, team = ?, active = 1
                    WHERE id = ?
                    """,
                    (name.strip(), department.strip(), team, existing["id"]),
                )
                created["updated"] += 1
            else:
                conn.execute(
                    """
                    INSERT INTO employees (name, department, fingerprint, device, team, active, created_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (name.strip(), department.strip(), fingerprint, device, team, now),
                )
                created["added"] += 1
            conn.commit()
            conn.close()

    return created, errors


def parse_holiday_day(value: str) -> str:
    text = (value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def list_holidays() -> list:
    init_db()
    conn = db()
    rows = conn.execute("SELECT * FROM holidays ORDER BY day DESC").fetchall()
    conn.close()
    return [
        {"id": row["id"], "day": row["day"], "name": row["name"] or "", "created_at": row["created_at"]}
        for row in rows
    ]


def holiday_map() -> dict:
    return {item["day"]: item["name"] for item in list_holidays()}


def holidays_touching(start: str, end: str) -> list:
    start_day = parse_holiday_day(start)
    end_day = parse_holiday_day(end or start)
    if not start_day:
        return []
    if not end_day:
        end_day = start_day
    if start_day > end_day:
        start_day, end_day = end_day, start_day
    init_db()
    conn = db()
    rows = conn.execute(
        "SELECT day, name FROM holidays WHERE day >= ? AND day <= ? ORDER BY day",
        (start_day, end_day),
    ).fetchall()
    conn.close()
    return [
        {"day": row["day"], "name": (row["name"] or "").strip() or "Official holiday"}
        for row in rows
    ]


def add_holidays(start: str, end: str, name: str) -> tuple[int, list]:
    start_day = parse_holiday_day(start)
    end_day = parse_holiday_day(end or start)
    title = (name or "").strip()[:80]
    errors = []
    if not start_day:
        errors.append("Choose a valid start date.")
    if not end_day:
        errors.append("Choose a valid end date.")
    if start_day and end_day and start_day > end_day:
        errors.append("From date cannot be after To date.")
    if errors:
        return 0, errors
    start_date = datetime.strptime(start_day, "%Y-%m-%d")
    end_date = datetime.strptime(end_day, "%Y-%m-%d")
    days = []
    current = start_date
    while current <= end_date:
        days.append(current.strftime("%Y-%m-%d"))
        if len(days) > 31:
            return 0, ["A holiday range cannot be longer than 31 days."]
        current += timedelta(days=1)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    added = 0
    init_db()
    with DB_LOCK:
        conn = db()
        for day in days:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO holidays (day, name, created_at) VALUES (?, ?, ?)",
                (day, title, now),
            )
            added += cursor.rowcount
        conn.commit()
        conn.close()
    if not added:
        errors.append("Those dates are already saved as official holidays.")
    return added, errors


def _form_request_dict(row) -> dict:
    return dict(row) if row is not None else {}


def form_request_to_sheet_row(row) -> dict:
    item = _form_request_dict(row)
    return {
        "Request ID": item.get("request_id") or "",
        "Submitted At": item.get("submitted_at") or "",
        "Fingerprint Number": item.get("fingerprint_id") or "",
        "Device": item.get("device") or "",
        "Name": item.get("name") or "",
        "Department": item.get("department") or "",
        "Request Type": item.get("request_type") or "",
        "Request Date": item.get("request_date") or "",
        "Punch In Time": item.get("punch_in_time") or "",
        "Punch Out Time": item.get("punch_out_time") or "",
        "From Time": item.get("from_time") or "",
        "To Time": item.get("to_time") or "",
        "From Date": item.get("start_date") or "",
        "To Date": item.get("end_date") or "",
        "Notes": item.get("notes") or "",
        "Status": item.get("status") or "Pending",
        "Reviewed By": item.get("reviewed_by") or "",
        "Reviewed At": item.get("reviewed_at") or "",
        "Rejection Reason": item.get("rejection_reason") or "",
        "Team": item.get("team") or "",
    }


def form_request_to_payload(row) -> dict:
    item = _form_request_dict(row)
    return {
        "request_id": item.get("request_id") or "",
        "submitted_at": item.get("submitted_at") or "",
        "fingerprint_id": item.get("fingerprint_id") or "",
        "device": item.get("device") or "",
        "name": item.get("name") or "",
        "department": item.get("department") or "",
        "team": item.get("team") or "",
        "request_type": item.get("request_type") or "",
        "request_date": item.get("request_date") or "",
        "punch_in_time": item.get("punch_in_time") or "",
        "punch_out_time": item.get("punch_out_time") or "",
        "from_time": item.get("from_time") or "",
        "to_time": item.get("to_time") or "",
        "start_date": item.get("start_date") or "",
        "end_date": item.get("end_date") or "",
        "notes": item.get("notes") or "",
        "status": item.get("status") or "Pending",
    }


def create_form_request(row: dict) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    init_db()
    with DB_LOCK:
        conn = db()
        try:
            conn.execute(
                """
                INSERT INTO form_requests (
                    request_id, submitted_at, fingerprint_id, device, name, department, team,
                    request_type, request_date, punch_in_time, punch_out_time, from_time, to_time,
                    start_date, end_date, notes, status, reviewed_by, reviewed_at, rejection_reason,
                    sync_status, sync_error, sync_attempts, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', '', 'pending', '', 0, ?)
                """,
                (
                    row.get("request_id") or "",
                    row.get("submitted_at") or now,
                    normalize_fingerprint_id(row.get("fingerprint_id")),
                    normalize_device(row.get("device") or ""),
                    (row.get("name") or "").strip(),
                    (row.get("department") or "").strip(),
                    (row.get("team") or "").strip(),
                    (row.get("request_type") or "").strip(),
                    row.get("request_date") or "",
                    row.get("punch_in_time") or "",
                    row.get("punch_out_time") or "",
                    row.get("from_time") or "",
                    row.get("to_time") or "",
                    row.get("start_date") or "",
                    row.get("end_date") or "",
                    row.get("notes") or "",
                    row.get("status") or "Pending",
                    now,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise ValueError("This request was already submitted.") from exc
        finally:
            conn.close()


def has_pending_form_requests() -> bool:
    init_db()
    conn = db()
    row = conn.execute(
        "SELECT 1 FROM form_requests WHERE sync_status = 'pending' LIMIT 1"
    ).fetchone()
    conn.close()
    return bool(row)


def pending_form_requests(limit: int = 8) -> list:
    init_db()
    conn = db()
    rows = conn.execute(
        """
        SELECT * FROM form_requests
        WHERE sync_status = 'pending'
        ORDER BY id ASC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    conn.close()
    return [_form_request_dict(row) for row in rows]


def form_requests_as_sheet_rows(fingerprint: str = "") -> list:
    init_db()
    conn = db()
    fp = normalize_fingerprint_id(fingerprint)
    if fp:
        rows = conn.execute(
            "SELECT * FROM form_requests WHERE fingerprint_id = ? ORDER BY submitted_at DESC, id DESC",
            (fp,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM form_requests ORDER BY submitted_at DESC, id DESC"
        ).fetchall()
    conn.close()
    return [form_request_to_sheet_row(row) for row in rows]


def unsynced_form_request_sheet_rows(department: str = "ALL") -> list:
    init_db()
    conn = db()
    sql = "SELECT * FROM form_requests WHERE sync_status != 'synced'"
    params = []
    if department and department != "ALL":
        sql += " AND lower(department) = lower(?)"
        params.append(department.strip())
    sql += " ORDER BY submitted_at DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [form_request_to_sheet_row(row) for row in rows]


def form_request_sheet_rows(department: str = "ALL") -> list:
    init_db()
    conn = db()
    sql = "SELECT * FROM form_requests"
    params = []
    if department and department != "ALL":
        sql += " WHERE lower(department) = lower(?)"
        params.append(department.strip())
    sql += " ORDER BY submitted_at DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [form_request_to_sheet_row(row) for row in rows]


def lookup_form_request_sheet_rows(fingerprint: str, name: str = "") -> list:
    rows = form_requests_as_sheet_rows(fingerprint)
    wanted_name = normalize_person_name(name)
    if not wanted_name:
        return rows
    return [
        row for row in rows
        if names_match(wanted_name, str(row.get("Name") or ""))
    ]


def get_form_requests_by_ids(request_ids: list) -> dict:
    ids = [str(item or "").strip() for item in request_ids if str(item or "").strip()]
    if not ids:
        return {}
    init_db()
    conn = db()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM form_requests WHERE request_id IN ({placeholders})",
        ids,
    ).fetchall()
    conn.close()
    return {row["request_id"]: _form_request_dict(row) for row in rows}


def mark_form_request_synced(request_id: str) -> None:
    init_db()
    with DB_LOCK:
        conn = db()
        conn.execute(
            """
            UPDATE form_requests
            SET sync_status = 'synced', sync_error = '', sync_attempts = sync_attempts + 1
            WHERE request_id = ?
            """,
            (request_id,),
        )
        conn.commit()
        conn.close()


def mark_form_request_blocked(request_id: str, reason: str) -> None:
    init_db()
    with DB_LOCK:
        conn = db()
        conn.execute(
            """
            UPDATE form_requests
            SET sync_status = 'blocked', sync_error = ?, sync_attempts = sync_attempts + 1
            WHERE request_id = ?
            """,
            ((reason or "").strip()[:300], request_id),
        )
        conn.commit()
        conn.close()


def bump_form_request_sync_attempt(request_id: str, error: str) -> None:
    init_db()
    with DB_LOCK:
        conn = db()
        conn.execute(
            """
            UPDATE form_requests
            SET sync_error = ?, sync_attempts = sync_attempts + 1
            WHERE request_id = ? AND sync_status = 'pending'
            """,
            ((error or "").strip()[:300], request_id),
        )
        conn.commit()
        conn.close()


def update_form_request_statuses(items: list, status: str, reviewed_by: str, reason: str = "") -> None:
    ids = [str(item.get("request_id") or "").strip() for item in items if item.get("request_id")]
    ids = [item for item in ids if item]
    if not ids:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rejection = reason if status == "Rejected" else ""
    init_db()
    with DB_LOCK:
        conn = db()
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"""
            UPDATE form_requests
            SET status = ?, reviewed_by = ?, reviewed_at = ?, rejection_reason = ?
            WHERE request_id IN ({placeholders})
            """,
            [status, reviewed_by or "", now, rejection, *ids],
        )
        conn.commit()
        conn.close()


def delete_holiday(holiday_id: int) -> bool:
    init_db()
    with DB_LOCK:
        conn = db()
        cursor = conn.execute("DELETE FROM holidays WHERE id = ?", (holiday_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted


def payroll_adjustments_map(cycle_start: str) -> dict:
    cycle = str(cycle_start or "").strip()[:10]
    if not cycle:
        return {}
    init_db()
    conn = db()
    rows = conn.execute(
        """
        SELECT device, fingerprint, name, department, penalty_days, bonus_days, notes
        FROM payroll_adjustments
        WHERE cycle_start = ?
        """,
        (cycle,),
    ).fetchall()
    conn.close()
    result = {}
    for row in rows:
        device = normalize_device(row["device"] or "")
        fingerprint = normalize_fingerprint_id(row["fingerprint"])
        if not fingerprint:
            continue
        result[(device, fingerprint)] = {
            "name": row["name"] or "",
            "department": row["department"] or "",
            "penalty_days": float(row["penalty_days"] or 0),
            "bonus_days": float(row["bonus_days"] or 0),
            "notes": row["notes"] or "",
        }
    return result


def save_payroll_adjustments(cycle_start: str, items: list, updated_by: str = "") -> int:
    cycle = str(cycle_start or "").strip()[:10]
    if not cycle:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved = 0
    init_db()
    with DB_LOCK:
        conn = db()
        try:
            for item in items:
                device = normalize_device(item.get("device") or "")
                fingerprint = normalize_fingerprint_id(item.get("fingerprint"))
                if not fingerprint:
                    continue
                penalty_days = max(0.0, float(item.get("penalty_days") or 0))
                bonus_days = max(0.0, float(item.get("bonus_days") or 0))
                name = str(item.get("name") or "").strip()
                department = str(item.get("department") or "").strip()
                notes = str(item.get("notes") or "").strip()
                if penalty_days <= 0 and bonus_days <= 0:
                    conn.execute(
                        """
                        DELETE FROM payroll_adjustments
                        WHERE cycle_start = ? AND device = ? AND fingerprint = ?
                        """,
                        (cycle, device, fingerprint),
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO payroll_adjustments (
                        cycle_start, device, fingerprint, name, department,
                        penalty_days, bonus_days, notes, updated_by, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cycle_start, device, fingerprint) DO UPDATE SET
                        name = excluded.name,
                        department = excluded.department,
                        penalty_days = excluded.penalty_days,
                        bonus_days = excluded.bonus_days,
                        notes = excluded.notes,
                        updated_by = excluded.updated_by,
                        updated_at = excluded.updated_at
                    """,
                    (
                        cycle,
                        device,
                        fingerprint,
                        name,
                        department,
                        penalty_days,
                        bonus_days,
                        notes,
                        (updated_by or "").strip(),
                        now,
                    ),
                )
                saved += 1
            conn.commit()
        finally:
            conn.close()
    return saved


def employees_for_payroll_adjustments(cycle_start: str) -> list:
    cycle = str(cycle_start or "").strip()[:10]
    adjustments = payroll_adjustments_map(cycle)
    rows = []
    for employee in list_employees():
        if not employee.get("active", True):
            continue
        device = normalize_device(employee.get("device") or "")
        fingerprint = normalize_fingerprint_id(employee.get("fingerprint"))
        if not fingerprint:
            continue
        adj = adjustments.get((device, fingerprint), {})
        rows.append({
            "name": employee.get("name") or "",
            "department": employee.get("department") or "",
            "device": device,
            "fingerprint": fingerprint,
            "penalty_days": adj.get("penalty_days") or 0,
            "bonus_days": adj.get("bonus_days") or 0,
        })
    rows.sort(
        key=lambda item: (
            (item.get("department") or "").lower(),
            (item.get("name") or "").lower(),
            item.get("device") or "",
            int(item["fingerprint"]) if str(item["fingerprint"]).isdigit() else 10**9,
        )
    )
    return rows
