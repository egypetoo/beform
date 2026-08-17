from collections import defaultdict
from datetime import datetime, timedelta
import struct

import user_store

MAX_ATTENDANCE_BYTES = 8_000_000
MAX_ATTENDANCE_ROWS = 25000
XLS_COLUMNS = {
    "fingerprint": 1,
    "name": 2,
    "date": 3,
    "timetable": 4,
    "clock_in": 7,
    "clock_out": 8,
    "late": 11,
    "absent": 13,
}
HEADER_ALIASES = {
    "ac-no.": "fingerprint",
    "ac-no": "fingerprint",
    "ac no": "fingerprint",
    "ac_no": "fingerprint",
    "fingerprint": "fingerprint",
    "fingerprint number": "fingerprint",
    "name": "name",
    "date": "date",
    "timetable": "timetable",
    "device": "device",
    "clock in": "clock_in",
    "clock-in": "clock_in",
    "clockin": "clock_in",
    "clock out": "clock_out",
    "clock-out": "clock_out",
    "clockout": "clock_out",
    "late": "late",
    "absent": "absent",
}
COVERING_TYPES = {
    "work remotely",
    "business mission",
    "sick leave",
    "personal excuse",
    "unpaid leave",
    "missing punch in",
    "missing punch out",
    "annual vacation",
    "sickness vacation",
}
WINDOW_START = "09:00"
WINDOW_END = "10:00"
REQUIRED_MINUTES = 510  # 8 hours 30 minutes


def minutes_of(value: str) -> int | None:
    text = parse_time(value)
    if not text:
        return None
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


def format_hours(minutes: int) -> str:
    minutes = max(0, int(minutes or 0))
    hours, mins = divmod(minutes, 60)
    return f"{hours}:{mins:02d}"


def decimal_hours(minutes: int) -> str:
    return f"{max(0, int(minutes or 0)) / 60:.2f}"


def evaluate_shift(clock_in: str, clock_out: str) -> dict:
    in_minutes = minutes_of(clock_in)
    out_minutes = minutes_of(clock_out)
    late_minutes = 0
    worked_minutes = None
    short_minutes = 0
    if in_minutes is not None:
        late_minutes = max(0, in_minutes - minutes_of(WINDOW_END))
        if out_minutes is not None:
            if out_minutes < in_minutes:
                out_minutes += 24 * 60
            worked_minutes = out_minutes - in_minutes
            short_minutes = max(0, REQUIRED_MINUTES - worked_minutes)
    return {
        "late_minutes": late_minutes,
        "late": format_hours(late_minutes),
        "late_hours": decimal_hours(late_minutes),
        "worked_minutes": worked_minutes,
        "worked": format_hours(worked_minutes) if worked_minutes is not None else "",
        "worked_hours": decimal_hours(worked_minutes) if worked_minutes is not None else "",
        "short_minutes": short_minutes,
        "short": format_hours(short_minutes),
        "short_hours": decimal_hours(short_minutes),
    }


def parse_day(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit():
        serial = int(text)
        if serial > 20000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).strftime("%Y-%m-%d")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%d/%m/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def parse_time(value) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"true", "false", "none"}:
        return ""
    if ":" in text:
        parts = text.split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1][:2])
            return f"{hour:02d}:{minute:02d}"
        except ValueError:
            return ""
    return ""


def is_true(value) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def parse_xls_cells(raw: bytes) -> dict:
    cells = {}
    index = 0
    length = len(raw)
    while index < length - 10:
        if raw[index] == 4 and raw[index + 1] == 0:
            size = struct.unpack_from("<H", raw, index + 2)[0]
            record = raw[index + 4:index + 4 + size]
            if 8 <= size <= 120 and index + 4 + size <= length and len(record) == size:
                row, col = struct.unpack_from("<HH", record, 0)
                text_len = record[7]
                text = record[8:8 + text_len]
                if text_len and 8 + text_len <= size and all(32 <= byte < 127 for byte in text):
                    cells.setdefault(row, {})[col] = text.decode("ascii")
                    index += 4 + size
                    continue
        index += 1
    return cells


def rows_from_xls(raw: bytes, fallback_device: str = "") -> list:
    cells = parse_xls_cells(raw)
    rows = []
    for columns in cells.values():
        fingerprint = user_store.normalize_fingerprint_id(columns.get(XLS_COLUMNS["fingerprint"], ""))
        day = parse_day(columns.get(XLS_COLUMNS["date"], ""))
        if not fingerprint or not day:
            continue
        timetable = columns.get(XLS_COLUMNS["timetable"], "")
        device = user_store.normalize_device(timetable) or fallback_device
        clock_in = parse_time(columns.get(XLS_COLUMNS["clock_in"], ""))
        clock_out = parse_time(columns.get(XLS_COLUMNS["clock_out"], ""))
        rows.append({
            "fingerprint": fingerprint,
            "name": str(columns.get(XLS_COLUMNS["name"], "")).strip(),
            "date": day,
            "device": device,
            "clock_in": clock_in,
            "clock_out": clock_out,
            "absent": is_true(columns.get(XLS_COLUMNS["absent"], "")) or not (clock_in or clock_out),
        })
        if len(rows) > MAX_ATTENDANCE_ROWS:
            raise ValueError("Attendance file has too many rows.")
    return rows


def rows_from_mapped(raw_rows: list, fallback_device: str = "") -> list:
    rows = []
    for raw in raw_rows:
        mapped = {}
        for key, value in raw.items():
            alias = HEADER_ALIASES.get(str(key or "").strip().lower())
            if alias:
                mapped[alias] = value
        fingerprint = user_store.normalize_fingerprint_id(mapped.get("fingerprint", ""))
        day = parse_day(mapped.get("date", ""))
        if not fingerprint or not day:
            continue
        device = user_store.normalize_device(mapped.get("device") or mapped.get("timetable") or "") or fallback_device
        clock_in = parse_time(mapped.get("clock_in", ""))
        clock_out = parse_time(mapped.get("clock_out", ""))
        rows.append({
            "fingerprint": fingerprint,
            "name": str(mapped.get("name") or "").strip(),
            "date": day,
            "device": device,
            "clock_in": clock_in,
            "clock_out": clock_out,
            "absent": is_true(mapped.get("absent", "")) or not (clock_in or clock_out),
        })
        if len(rows) > MAX_ATTENDANCE_ROWS:
            raise ValueError("Attendance file has too many rows.")
    return rows


def detect_device_from_name(filename: str) -> str:
    name = (filename or "").upper()
    if "F9" in name:
        return "F9"
    if "F8" in name:
        return "F8"
    return ""


def date_range(start: str, end: str) -> list:
    if not start:
        return []
    end = end or start
    try:
        begin = datetime.strptime(start[:10], "%Y-%m-%d")
        finish = datetime.strptime(end[:10], "%Y-%m-%d")
    except ValueError:
        return [start[:10]] if len(start) >= 10 else []
    if finish < begin:
        begin, finish = finish, begin
    days = []
    current = begin
    while current <= finish and len(days) < 60:
        days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return days


def request_covers(row: dict) -> bool:
    status = str(row.get("Status") or "Pending").strip().lower()
    if status == "rejected":
        return False
    request_type = str(row.get("Request Type") or "").strip().lower()
    return request_type in COVERING_TYPES


def coverage_index(requests: list) -> dict:
    index = defaultdict(list)
    for row in requests:
        if not request_covers(row):
            continue
        fingerprint = user_store.normalize_fingerprint_id(row.get("Fingerprint Number"))
        if not fingerprint:
            continue
        device = user_store.normalize_device(row.get("Device") or "")
        request_type = str(row.get("Request Type") or "").strip()
        for day in date_range(str(row.get("From Date") or ""), str(row.get("To Date") or "")):
            index[(device, fingerprint, day)].append(request_type)
            if not device:
                index[("", fingerprint, day)].append(request_type)
    return index


def covering_types(index: dict, device: str, fingerprint: str, day: str) -> list:
    found = []
    seen = set()
    for key in ((device, fingerprint, day), ("", fingerprint, day)):
        for item in index.get(key, []):
            if item not in seen:
                seen.add(item)
                found.append(item)
    return found


def weekday_label(day: str) -> str:
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b")
    except ValueError:
        return day


def build_report(punches: list, requests: list, employees: list) -> dict:
    by_person = defaultdict(list)
    for punch in punches:
        if not punch.get("device"):
            continue
        by_person[(punch["device"], punch["fingerprint"])].append(punch)

    employees_by_key = {
        (item["device"], item["fingerprint"]): item
        for item in employees
        if item.get("active", True)
    }
    covered = coverage_index(requests)
    people = []
    missing_total = 0
    late_total = 0
    short_total = 0
    covered_total = 0
    late_minutes_total = 0
    short_minutes_total = 0

    for key, days in sorted(
        by_person.items(),
        key=lambda item: (
            item[0][0],
            (item[1][0].get("name") or "").lower(),
            item[0][1],
        ),
    ):
        device, fingerprint = key
        employee = employees_by_key.get(key)
        sample = days[0]
        missing = []
        late = []
        short = []
        covered_days = []
        late_minutes = 0
        short_minutes = 0
        for punch in sorted(days, key=lambda item: item["date"]):
            types = covering_types(covered, device, fingerprint, punch["date"])
            has_punch = bool(punch["clock_in"] or punch["clock_out"]) and not punch["absent"]
            if punch["absent"] or not has_punch:
                if types:
                    covered_days.append({
                        "date": punch["date"],
                        "label": weekday_label(punch["date"]),
                        "types": types,
                    })
                    covered_total += 1
                else:
                    missing.append({
                        "date": punch["date"],
                        "label": weekday_label(punch["date"]),
                    })
                    missing_total += 1
                continue
            shift = evaluate_shift(punch["clock_in"], punch["clock_out"])
            if shift["late_minutes"]:
                late.append({
                    "date": punch["date"],
                    "label": weekday_label(punch["date"]),
                    "clock_in": punch["clock_in"],
                    "clock_out": punch["clock_out"],
                    **shift,
                })
                late_total += 1
                late_minutes += shift["late_minutes"]
                late_minutes_total += shift["late_minutes"]
            if shift["short_minutes"]:
                short.append({
                    "date": punch["date"],
                    "label": weekday_label(punch["date"]),
                    "clock_in": punch["clock_in"],
                    "clock_out": punch["clock_out"],
                    **shift,
                })
                short_total += 1
                short_minutes += shift["short_minutes"]
                short_minutes_total += shift["short_minutes"]
        if not missing and not late and not short and not covered_days:
            continue
        people.append({
            "name": (employee or {}).get("name") or sample["name"] or fingerprint,
            "department": (employee or {}).get("department") or "",
            "fingerprint": fingerprint,
            "device": device,
            "registered": bool(employee),
            "missing": missing,
            "late": late,
            "short": short,
            "covered": covered_days,
            "late_minutes": late_minutes,
            "short_minutes": short_minutes,
            "late_hours": decimal_hours(late_minutes),
            "short_hours": decimal_hours(short_minutes),
            "late_text": format_hours(late_minutes),
            "short_text": format_hours(short_minutes),
        })

    people.sort(key=lambda item: (item["department"], item["name"].lower(), item["device"]))
    dates = [punch["date"] for punch in punches if punch.get("date")]
    return {
        "people": people,
        "missing_total": missing_total,
        "late_total": late_total,
        "short_total": short_total,
        "covered_total": covered_total,
        "late_minutes_total": late_minutes_total,
        "short_minutes_total": short_minutes_total,
        "late_hours_total": decimal_hours(late_minutes_total),
        "short_hours_total": decimal_hours(short_minutes_total),
        "late_text_total": format_hours(late_minutes_total),
        "short_text_total": format_hours(short_minutes_total),
        "punch_rows": len(punches),
        "from_date": min(dates) if dates else "",
        "to_date": max(dates) if dates else "",
    }


def report_sheets(report: dict) -> list:
    people_rows = []
    day_rows = []
    for person in report.get("people") or []:
        registered = "Yes" if person.get("registered") else "No"
        missing_dates = ", ".join(item["date"] for item in person.get("missing") or [])
        covered_dates = ", ".join(item["date"] for item in person.get("covered") or [])
        people_rows.append([
            person.get("name") or "",
            person.get("department") or "",
            person.get("device") or "",
            person.get("fingerprint") or "",
            registered,
            len(person.get("missing") or []),
            len(person.get("covered") or []),
            len(person.get("late") or []),
            person.get("late_hours") or "0.00",
            person.get("late_text") or "0:00",
            len(person.get("short") or []),
            person.get("short_hours") or "0.00",
            person.get("short_text") or "0:00",
            missing_dates,
            covered_dates,
        ])
        base = [
            person.get("name") or "",
            person.get("department") or "",
            person.get("device") or "",
            person.get("fingerprint") or "",
            registered,
        ]
        for item in person.get("missing") or []:
            day_rows.append([*base, "No punch & no form", item["date"], item.get("label") or "", "", "", "", "", "", "", "", ""])
        for item in person.get("covered") or []:
            day_rows.append([
                *base,
                "Covered by form",
                item["date"],
                item.get("label") or "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                ", ".join(item.get("types") or []),
            ])
        shift_days = {}
        for item in person.get("late") or []:
            shift_days[item["date"]] = item
        for item in person.get("short") or []:
            shift_days[item["date"]] = item
        for day, item in sorted(shift_days.items()):
            late_min = item.get("late_minutes") or 0
            short_min = item.get("short_minutes") or 0
            if late_min and short_min:
                status = "Late + short day"
            elif late_min:
                status = "Late"
            else:
                status = "Short day"
            day_rows.append([
                *base,
                status,
                item["date"],
                item.get("label") or "",
                item.get("clock_in") or "",
                item.get("clock_out") or "",
                item.get("worked_hours") or "",
                item.get("late_hours") or "0.00",
                item.get("late") or "0:00",
                item.get("short_hours") or "0.00",
                item.get("short") or "0:00",
                "",
            ])
    return [
        {
            "name": "Summary",
            "headers": ["Metric", "Value"],
            "rows": [
                ["From", report.get("from_date") or ""],
                ["To", report.get("to_date") or ""],
                ["Morning window", f"{WINDOW_START} to {WINDOW_END}"],
                ["Required work hours", "8.50"],
                ["Machine rows", report.get("punch_rows") or 0],
                ["No punch & no form", report.get("missing_total") or 0],
                ["Covered by form", report.get("covered_total") or 0],
                ["Late days", report.get("late_total") or 0],
                ["Late hours", report.get("late_hours_total") or "0.00"],
                ["Late hours (h:mm)", report.get("late_text_total") or "0:00"],
                ["Short days", report.get("short_total") or 0],
                ["Short hours", report.get("short_hours_total") or "0.00"],
                ["People in report", len(report.get("people") or [])],
            ],
        },
        {
            "name": "People",
            "headers": [
                "Name", "Department", "Device", "Fingerprint", "In employee list",
                "Missing days", "Covered days", "Late days", "Late hours", "Late (h:mm)",
                "Short days", "Short hours", "Short (h:mm)",
                "Missing dates", "Covered dates",
            ],
            "rows": people_rows,
        },
        {
            "name": "Days",
            "headers": [
                "Name", "Department", "Device", "Fingerprint", "In employee list",
                "Status", "Date", "Weekday", "Clock In", "Clock Out",
                "Worked hours", "Late hours", "Late (h:mm)", "Short hours", "Short (h:mm)",
                "Form types",
            ],
            "rows": day_rows,
        },
    ]
