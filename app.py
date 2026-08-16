from datetime import datetime
from functools import wraps
from pathlib import Path
import json
import os
import time
import uuid

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
import requests

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
GOOGLE_SHEET_WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK", "").strip()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "hrform-secret-key")

ROWS_CACHE = {}
CACHE_SECONDS = 60


def today_values():
    now = datetime.now()
    return {
        "today": now.strftime("%Y-%m-%d"),
        "today_display": now.strftime("%A, %B %d, %Y"),
    }

LEAVE_GROUPS = [
    {
        "title": "Leaves",
        "title_ar": "الإجازات",
        "options": [
            {"value": "business_mission", "en": "Business Mission", "ar": "مهمة عمل"},
            {"value": "sick_leave", "en": "Sick Leave", "ar": "إجازة مرضية"},
            {"value": "personal_excuse", "en": "Personal Excuse", "ar": "عذر شخصي"},
            {"value": "unpaid_leave", "en": "Unpaid Leave", "ar": "إجازة بدون راتب"},
            {"value": "missing_punch_in", "en": "Missing Punch In", "ar": "نسيان بصمة حضور"},
            {"value": "missing_punch_out", "en": "Missing Punch Out", "ar": "نسيان بصمة انصراف"},
        ],
    },
    {
        "title": "Vacations",
        "title_ar": "الإجازات السنوية / المرضية",
        "options": [
            {"value": "annual_vacation", "en": "Annual Vacation", "ar": "إجازة سنوية"},
            {"value": "sickness_vacation", "en": "Sickness Vacation", "ar": "إجازة مرضية طويلة"},
        ],
    },
    {
        "title": "Casual Vacations",
        "title_ar": "إجازات أخرى",
        "options": [
            {"value": "work_remotely", "en": "Work Remotely", "ar": "عمل عن بعد"},
        ],
    },
]

PUNCH_TYPES = {"missing_punch_in", "missing_punch_out"}
TIME_RANGE_TYPES = {"business_mission", "personal_excuse"}
DATE_RANGE_TYPES = {"sick_leave", "unpaid_leave", "annual_vacation", "sickness_vacation", "work_remotely"}

DEPARTMENTS = [
    {"value": "web", "label": "Web"},
    {"value": "social", "label": "Social"},
    {"value": "accounting", "label": "Accounting"},
    {"value": "sales", "label": "Sales"},
    {"value": "hr", "label": "HR"},
]
DEPARTMENT_VALUES = {item["value"] for item in DEPARTMENTS}


def get_managers():
    load_dotenv(BASE_DIR / ".env", override=True)
    return {
        "web": {
            "name": "Web Manager",
            "department": "Web",
            "password": os.getenv("MANAGER_WEB_PASSWORD", "web123"),
        },
        "social": {
            "name": "Social Manager",
            "department": "Social",
            "password": os.getenv("MANAGER_SOCIAL_PASSWORD", "social123"),
        },
        "accounting": {
            "name": "Accounting Manager",
            "department": "Accounting",
            "password": os.getenv("MANAGER_ACCOUNTING_PASSWORD", "accounting123"),
        },
        "sales": {
            "name": "Sales Manager",
            "department": "Sales",
            "password": os.getenv("MANAGER_SALES_PASSWORD", "sales123"),
        },
        "hr": {
            "name": "HR Manager",
            "department": "ALL",
            "password": os.getenv("MANAGER_HR_PASSWORD", "hr123"),
        },
    }


def option_lookup():
    mapping = {}
    for group in LEAVE_GROUPS:
        for option in group["options"]:
            mapping[option["value"]] = option
    return mapping


def webhook_url():
    return os.getenv("GOOGLE_SHEET_WEBHOOK", "").strip() or GOOGLE_SHEET_WEBHOOK


def sheet_api(payload: dict) -> dict:
    webhook = webhook_url()
    if not webhook:
        raise RuntimeError("Google Sheet is not connected")

    response = requests.post(webhook, json=payload, timeout=20)
    response.raise_for_status()
    try:
        data = json.loads(response.text or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Invalid sheet response") from exc
    if not data.get("ok"):
        raise RuntimeError("Google Sheet did not confirm the save")
    return data


def save_submission(row: dict) -> None:
    payload = dict(row)
    payload["action"] = "create"
    sheet_api(payload)
    ROWS_CACHE.clear()


def list_requests(department: str) -> list:
    now = time.time()
    cached = ROWS_CACHE.get(department)
    if cached and now - cached["at"] < CACHE_SECONDS:
        return cached["rows"]

    data = sheet_api({"action": "list", "department": department})
    rows = data.get("rows", [])
    ROWS_CACHE[department] = {"at": now, "rows": rows}
    return rows


def set_request_status(items: list, status: str, reviewed_by: str) -> None:
    sheet_api(
        {
            "action": "set_status",
            "items": items,
            "status": status,
            "reviewed_by": reviewed_by,
        }
    )
    ROWS_CACHE.clear()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("manager"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.route("/", methods=["GET", "POST"])
def index():
    options = option_lookup()

    if request.method == "POST":
        fingerprint_id = request.form.get("fingerprint_id", "").strip()
        name = request.form.get("name", "").strip()
        department = request.form.get("department", "").strip()
        request_type = request.form.get("request_type", "").strip()
        request_date = request.form.get("request_date", "").strip() or datetime.now().strftime("%Y-%m-%d")
        punch_in_time = request.form.get("punch_in_time", "").strip()
        punch_out_time = request.form.get("punch_out_time", "").strip()
        from_time = request.form.get("from_time", "").strip()
        to_time = request.form.get("to_time", "").strip()
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        notes = request.form.get("notes", "").strip()

        errors = []
        if not fingerprint_id:
            errors.append("Fingerprint number is required")
        elif not fingerprint_id.isdigit():
            errors.append("Fingerprint number must contain digits only")
        if not name:
            errors.append("Name is required")
        if not department:
            errors.append("Department is required")
        elif department not in DEPARTMENT_VALUES:
            errors.append("Please select a valid department")
        if request_type not in options:
            errors.append("Request type is required")
        if request_type == "missing_punch_in" and not punch_in_time:
            errors.append("Actual punch-in time is required")
        if request_type == "missing_punch_out" and not punch_out_time:
            errors.append("Actual punch-out time is required")
        if request_type in TIME_RANGE_TYPES:
            if not from_time:
                errors.append("From time is required")
            if not to_time:
                errors.append("To time is required")
        if request_type in options:
            if not start_date:
                errors.append("From date is required")
            if not end_date:
                errors.append("To date is required")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "index.html",
                leave_groups=LEAVE_GROUPS,
                departments=DEPARTMENTS,
                form=request.form,
                **today_values(),
            )

        selected = options[request_type]
        department_label = next(item["label"] for item in DEPARTMENTS if item["value"] == department)
        try:
            save_submission(
                {
                    "request_id": uuid.uuid4().hex[:12].upper(),
                    "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "fingerprint_id": fingerprint_id,
                    "name": name,
                    "department": department_label,
                    "request_type": selected["en"],
                    "request_date": request_date,
                    "punch_in_time": punch_in_time if request_type == "missing_punch_in" else "",
                    "punch_out_time": punch_out_time if request_type == "missing_punch_out" else "",
                    "from_time": from_time if request_type in TIME_RANGE_TYPES else "",
                    "to_time": to_time if request_type in TIME_RANGE_TYPES else "",
                    "start_date": start_date,
                    "end_date": end_date,
                    "notes": notes,
                    "status": "Pending",
                }
            )
        except Exception as exc:
            (BASE_DIR / "sheet_error.log").write_text(str(exc), encoding="utf-8")
            flash("Could not save the request to the HR sheet. Please try again.", "error")
            return render_template(
                "index.html",
                leave_groups=LEAVE_GROUPS,
                departments=DEPARTMENTS,
                form=request.form,
                **today_values(),
            )
        return redirect(url_for("success"))

    return render_template(
        "index.html",
        leave_groups=LEAVE_GROUPS,
        departments=DEPARTMENTS,
        form={},
        **today_values(),
    )


@app.route("/success")
def success():
    return render_template("success.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("manager"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        manager = get_managers().get(username)
        if not manager or password != manager["password"]:
            flash("Invalid username or password.", "error")
            return render_template("login.html")

        session["manager"] = {
            "username": username,
            "name": manager["name"],
            "department": manager["department"],
        }
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    manager = session["manager"]
    status_filter = request.args.get("status", "Pending")
    search_query = request.args.get("q", "").strip()
    type_filter = request.args.get("type", "").strip()
    try:
        rows = list_requests(manager["department"])
    except Exception as exc:
        (BASE_DIR / "sheet_error.log").write_text(str(exc), encoding="utf-8")
        rows = []
        flash("Could not load requests from the HR sheet.", "error")

    request_types = sorted({
        str(row.get("Request Type") or "").strip()
        for row in rows
        if row.get("Request Type")
    })

    if status_filter and status_filter != "All":
        rows = [row for row in rows if (row.get("Status") or "Pending") == status_filter]
    if type_filter:
        rows = [row for row in rows if (row.get("Request Type") or "") == type_filter]
    if search_query:
        needle = search_query.lower()
        rows = [
            row for row in rows
            if needle in " ".join([
                str(row.get("Name") or ""),
                str(row.get("Fingerprint Number") or ""),
                str(row.get("Department") or ""),
                str(row.get("Request Type") or ""),
                str(row.get("Notes") or ""),
            ]).lower()
        ]

    return render_template(
        "dashboard.html",
        manager=manager,
        rows=rows,
        status_filter=status_filter,
        search_query=search_query,
        type_filter=type_filter,
        request_types=request_types,
        statuses=["Pending", "Approved", "Rejected", "All"],
    )


@app.route("/dashboard/status", methods=["POST"])
@login_required
def update_status():
    manager = session["manager"]
    status = request.form.get("status", "").strip()
    selected = request.form.getlist("selected")
    request_id = request.form.get("request_id", "").strip()
    department = request.form.get("department", "").strip() or manager["department"]

    items = []
    if selected:
        for value in selected:
            request_id_value, _, dept_value = value.partition("||")
            if request_id_value:
                items.append({
                    "request_id": request_id_value.strip(),
                    "department": dept_value.strip() or department,
                })
    elif request_id:
        items.append({"request_id": request_id, "department": department})

    if status not in {"Approved", "Rejected"} or not items:
        flash("Select at least one request first.", "error")
        return redirect(url_for("dashboard", status=request.form.get("status_filter", "Pending")))

    try:
        set_request_status(items, status, manager["name"])
        flash(f"{len(items)} request(s) {status.lower()} successfully.", "success")
    except Exception as exc:
        (BASE_DIR / "sheet_error.log").write_text(str(exc), encoding="utf-8")
        flash("Could not update the request status.", "error")

    return redirect(url_for(
        "dashboard",
        status=request.form.get("status_filter", "Pending"),
        q=request.form.get("q", ""),
        type=request.form.get("type_filter", ""),
    ))


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
