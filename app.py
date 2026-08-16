from datetime import datetime
from pathlib import Path
import os

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
import requests

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
GOOGLE_SHEET_WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK", "").strip()

app = Flask(__name__)
app.secret_key = "hrform-secret-key"

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

COLUMNS = [
    "submitted_at",
    "fingerprint_id",
    "name",
    "department",
    "request_type",
    "request_date",
    "punch_in_time",
    "punch_out_time",
    "from_time",
    "to_time",
    "start_date",
    "end_date",
    "notes",
]

EXCEL_HEADERS = [
    "Submitted At",
    "Fingerprint Number",
    "Name",
    "Department",
    "Request Type",
    "Request Date",
    "Punch In Time",
    "Punch Out Time",
    "From Time",
    "To Time",
    "From Date",
    "To Date",
    "Notes",
]


def option_lookup():
    mapping = {}
    for group in LEAVE_GROUPS:
        for option in group["options"]:
            mapping[option["value"]] = option
    return mapping


def save_submission(row: dict) -> None:
    if not GOOGLE_SHEET_WEBHOOK:
        raise RuntimeError("Google Sheet is not connected")

    response = requests.post(
        GOOGLE_SHEET_WEBHOOK,
        json=row,
        timeout=20,
        allow_redirects=False,
    )
    if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
        response = requests.post(response.headers["Location"], json=row, timeout=20)
    response.raise_for_status()


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
        if request_type in TIME_RANGE_TYPES or request_type in DATE_RANGE_TYPES:
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
                    "start_date": start_date if request_type not in PUNCH_TYPES else "",
                    "end_date": end_date if request_type not in PUNCH_TYPES else "",
                    "notes": notes,
                }
            )
        except Exception:
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


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
