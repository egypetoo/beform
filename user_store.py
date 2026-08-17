import re
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import Lock

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "users.db"
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,20}$")
DB_LOCK = Lock()

RESERVED_USERNAMES = {"web", "social", "accounting", "sales", "hr", "all"}
RESERVED_SHEETS = {"all", "other"}
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &/\-]{1,39}$")


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


def list_custom_departments(active_only: bool = False) -> list:
    init_db()
    conn = db()
    sql = "SELECT * FROM departments"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY label"
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [
        {
            "id": row["id"],
            "value": row["value"],
            "label": row["label"],
            "active": bool(row["active"]),
        }
        for row in rows
    ]


def validate_new_department(label: str, name: str, username: str, password: str, taken_values: set, taken_labels: set) -> list:
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
    elif value in taken_values or label.lower() in {item.lower() for item in taken_labels}:
        errors.append("This department already exists.")
    elif len(value) < 3:
        errors.append("Department name is too short to create a username.")
    if not name:
        errors.append("Manager name is required.")
    if not USERNAME_RE.match(username):
        errors.append("Username must be 3-20 characters: letters, numbers, dot, dash, or underscore.")
    elif username in RESERVED_USERNAMES:
        errors.append("This username is reserved.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
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
            conn.execute(
                "INSERT INTO departments (value, label, active, created_at) VALUES (?, ?, 1, ?)",
                (value, label, now),
            )
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
    elif username in RESERVED_USERNAMES:
        errors.append("This username is reserved for a department manager.")
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
