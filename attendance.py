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


def is_late_value(value) -> bool:
    text = str(value or "").strip()
    if not text or text in {"0", "00:00", "0:00", "0.0"}:
        return False
    return bool(parse_time(text) or text)


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
        late = parse_time(columns.get(XLS_COLUMNS["late"], "")) or str(columns.get(XLS_COLUMNS["late"], "")).strip()
        rows.append({
            "fingerprint": fingerprint,
            "name": str(columns.get(XLS_COLUMNS["name"], "")).strip(),
            "date": day,
            "device": device,
            "clock_in": clock_in,
            "clock_out": clock_out,
            "late": late if is_late_value(late) else "",
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
        late_raw = mapped.get("late", "")
        rows.append({
            "fingerprint": fingerprint,
            "name": str(mapped.get("name") or "").strip(),
            "date": day,
            "device": device,
            "clock_in": clock_in,
            "clock_out": clock_out,
            "late": parse_time(late_raw) if is_late_value(late_raw) else "",
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
    covered_total = 0

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
        covered_days = []
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
            elif punch["late"]:
                late.append({
                    "date": punch["date"],
                    "label": weekday_label(punch["date"]),
                    "clock_in": punch["clock_in"],
                    "late": punch["late"],
                })
                late_total += 1
        if not missing and not late and not covered_days:
            continue
        people.append({
            "name": (employee or {}).get("name") or sample["name"] or fingerprint,
            "department": (employee or {}).get("department") or "",
            "fingerprint": fingerprint,
            "device": device,
            "registered": bool(employee),
            "missing": missing,
            "late": late,
            "covered": covered_days,
        })

    people.sort(key=lambda item: (item["department"], item["name"].lower(), item["device"]))
    dates = [punch["date"] for punch in punches if punch.get("date")]
    return {
        "people": people,
        "missing_total": missing_total,
        "late_total": late_total,
        "covered_total": covered_total,
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
        late_dates = ", ".join(item["date"] for item in person.get("late") or [])
        people_rows.append([
            person.get("name") or "",
            person.get("department") or "",
            person.get("device") or "",
            person.get("fingerprint") or "",
            registered,
            len(person.get("missing") or []),
            len(person.get("covered") or []),
            len(person.get("late") or []),
            missing_dates,
            covered_dates,
            late_dates,
        ])
        base = [
            person.get("name") or "",
            person.get("department") or "",
            person.get("device") or "",
            person.get("fingerprint") or "",
            registered,
        ]
        for item in person.get("missing") or []:
            day_rows.append([*base, "No punch & no form", item["date"], item.get("label") or "", "", "", ""])
        for item in person.get("covered") or []:
            day_rows.append([
                *base,
                "Covered by form",
                item["date"],
                item.get("label") or "",
                "",
                "",
                ", ".join(item.get("types") or []),
            ])
        for item in person.get("late") or []:
            day_rows.append([
                *base,
                "Late",
                item["date"],
                item.get("label") or "",
                item.get("clock_in") or "",
                item.get("late") or "",
                "",
            ])
    return [
        {
            "name": "Summary",
            "headers": ["Metric", "Value"],
            "rows": [
                ["From", report.get("from_date") or ""],
                ["To", report.get("to_date") or ""],
                ["Machine rows", report.get("punch_rows") or 0],
                ["No punch & no form", report.get("missing_total") or 0],
                ["Covered by form", report.get("covered_total") or 0],
                ["Late arrivals", report.get("late_total") or 0],
                ["People in report", len(report.get("people") or [])],
            ],
        },
        {
            "name": "People",
            "headers": [
                "Name", "Department", "Device", "Fingerprint", "In employee list",
                "Missing days", "Covered days", "Late days",
                "Missing dates", "Covered dates", "Late dates",
            ],
            "rows": people_rows,
        },
        {
            "name": "Days",
            "headers": [
                "Name", "Department", "Device", "Fingerprint", "In employee list",
                "Status", "Date", "Weekday", "Clock In", "Late", "Form types",
            ],
            "rows": day_rows,
        },
    ]
