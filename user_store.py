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
        conn.commit()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for value, label in SEED_DEPARTMENTS:
            conn.execute(
                "INSERT OR IGNORE INTO departments (value, label, active, created_at) VALUES (?, ?, 1, ?)",
                (value, label, now),
            )
        conn.commit()
        conn.close()


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

    if not LABEL_RE.match(label):
        errors.append("Department name must be 2-40 characters: letters, numbers, spaces, &, / or dash.")
    elif label.lower() in RESERVED_SHEETS or value in RESERVED_SHEETS:
        errors.append("This department name is reserved.")
    elif len(value) < 3:
        errors.append("Department name is too short.")
    if not name:
        errors.append("Manager name is required.")
    if not USERNAME_RE.match(username):
        errors.append("Username must be 3-20 characters: letters, numbers, dot, dash, or underscore.")
    elif username_taken(username):
        errors.append("This username is reserved or already exists.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    existing = find_department(label)
    if existing and department_manager(existing["label"]):
        errors.append("This department already has a manager. Reset their password from the list below.")
    return errors


def create_department(label: str, name: str, username: str, password: str) -> dict:
    init_db()
    label = label.strip()
    username = normalize_username(username)
    value = slug_from_label(label)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                (username, name.strip(), label, generate_password_hash(password), now),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            conn.close()
            raise ValueError("Department or username already exists.") from exc
        conn.close()
    return {"value": value, "label": label, "username": username}


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


DEFAULT_DEVICES = ["F8", "F9"]
MAX_EMPLOYEE_IMPORT_ROWS = 800


def normalize_fingerprint_id(value) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].replace(".", "", 1).isdigit():
        text = text[:-2]
    return re.sub(r"\D", "", text)


def normalize_device(value: str) -> str:
    text = re.sub(r"\s+", "", (value or "").strip().upper())
    if re.fullmatch(r"F\d{1,3}", text):
        return text
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


def _row_to_employee(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "department": row["department"],
        "fingerprint": row["fingerprint"],
        "device": row["device"],
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


def match_employee(name: str, fingerprint: str, department: str) -> dict | None:
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
    named = [person for person in people if names_match(name, person["name"])]
    if not named:
        return None
    department_key = (department or "").strip().lower()
    in_department = [
        person for person in named
        if person["department"].strip().lower() == department_key
    ]
    if len(in_department) == 1:
        return in_department[0]
    if len(in_department) > 1:
        return in_department[0]
    return None


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


def validate_employee(name: str, department: str, fingerprint: str, device: str, departments: set, employee_id: int | None = None) -> list:
    errors = []
    name = (name or "").strip()
    department = (department or "").strip()
    fingerprint = normalize_fingerprint_id(fingerprint)
    device = normalize_device(device)
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
        errors.append("Device must be F8, F9, or similar (F then numbers).")
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


def create_employee(name: str, department: str, fingerprint: str, device: str) -> dict:
    name = name.strip()
    department = department.strip()
    fingerprint = normalize_fingerprint_id(fingerprint)
    device = normalize_device(device)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    init_db()
    with DB_LOCK:
        conn = db()
        try:
            cursor = conn.execute(
                """
                INSERT INTO employees (name, department, fingerprint, device, active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (name, department, fingerprint, device, now),
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
    }


def update_employee(employee_id: int, name: str, department: str, fingerprint: str, device: str) -> bool:
    name = name.strip()
    department = department.strip()
    fingerprint = normalize_fingerprint_id(fingerprint)
    device = normalize_device(device)
    init_db()
    with DB_LOCK:
        conn = db()
        try:
            cursor = conn.execute(
                """
                UPDATE employees
                SET name = ?, department = ?, fingerprint = ?, device = ?
                WHERE id = ?
                """,
                (name, department, fingerprint, device, employee_id),
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
        fingerprint = normalize_fingerprint_id(row.get("fingerprint") or row.get("ac-no.") or row.get("ac-no") or "")
        device = normalize_device(row.get("device") or row.get("machine") or "")
        if department not in departments:
            canonical = next((item for item in departments if item.lower() == department.lower()), "")
            department = canonical or department
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
                    SET name = ?, department = ?, active = 1
                    WHERE id = ?
                    """,
                    (name.strip(), department.strip(), existing["id"]),
                )
                created["updated"] += 1
            else:
                conn.execute(
                    """
                    INSERT INTO employees (name, department, fingerprint, device, active, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (name.strip(), department.strip(), fingerprint, device, now),
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


def delete_holiday(holiday_id: int) -> bool:
    init_db()
    with DB_LOCK:
        conn = db()
        cursor = conn.execute("DELETE FROM holidays WHERE id = ?", (holiday_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted
