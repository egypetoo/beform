from __future__ import annotations

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
    "unpaid leave",
    "annual vacation",
    "sickness vacation",
}
MISSING_PUNCH_TYPES = {
    "missing punch in",
    "missing punch out",
}
LATE_EXCUSE_TYPE = "personal excuse"
SATURDAY_WORK_TYPE = "monthly saturday work"
NO_CLOCK_OUT_DEPARTMENTS = {"sales", "مبيعات"}
WINDOW_START = "09:00"
FLEX_END = "10:00"
WINDOW_END = "10:15"
QUARTER_UNTIL = "11:00"
REQUIRED_MINUTES = 510
QUARTER_WORKED_MINUTES = 7 * 60 + 30
DAILY_GRACE_MINUTES = 15
MONTHLY_LATE_ALLOWANCE_MINUTES = 4 * 60
DEDUCTION_FULL = "يوم"
DEDUCTION_HALF = "نصف يوم"
DEDUCTION_QUARTER = "ربع يوم"
DEDUCTION_RANK = {
    "": 0,
    DEDUCTION_QUARTER: 1,
    DEDUCTION_HALF: 2,
    DEDUCTION_FULL: 3,
}
NOTE_AR = {
    "work remotely": "عمل عن بعد",
    "business mission": "مأمورية",
    "sick leave": "إجازة مرضية",
    "personal excuse": "إذن",
    "unpaid leave": "إجازة بدون راتب",
    "missing punch in": "نسيان بصمة",
    "missing punch out": "نسيان بصمة",
    "annual vacation": "اعتيادي",
    "sickness vacation": "إجازة مرضية",
    "monthly saturday work": "عمل السبت الشهري",
}


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


def ceil_hours_minutes(minutes: int) -> int:
    minutes = max(0, int(minutes or 0))
    if not minutes:
        return 0
    return ((minutes + 59) // 60) * 60


def late_penalty(clock_in: str) -> str:
    in_minutes = minutes_of(clock_in)
    allowed = minutes_of(WINDOW_END)
    quarter_until = minutes_of(QUARTER_UNTIL)
    if in_minutes is None or allowed is None or quarter_until is None:
        return ""
    if in_minutes <= allowed:
        return ""
    if in_minutes <= quarter_until:
        return DEDUCTION_QUARTER
    return DEDUCTION_HALF


def clock_out_penalty(shift: dict) -> tuple[str, str]:
    early = int(shift.get("early_out") or 0)
    if early <= 0:
        return "", ""
    late = int(shift.get("late_minutes") or 0)
    remaining_early = max(0, early - max(0, DAILY_GRACE_MINUTES - late))
    if remaining_early <= 0:
        return "", ""
    worked = shift.get("credited_minutes")
    if worked is None:
        worked = shift.get("worked_minutes")
    if worked is not None and int(worked) >= QUARTER_WORKED_MINUTES:
        return DEDUCTION_QUARTER, "عدم إكمال 8 ساعات ونصف"
    return DEDUCTION_HALF, "عدم إكمال 7 ساعات ونصف"


def worse_penalty(left: str, right: str) -> str:
    if DEDUCTION_RANK.get(right, 0) > DEDUCTION_RANK.get(left, 0):
        return right
    return left


def excuse_duration_minutes(from_time: str, to_time: str) -> int | None:
    start = minutes_of(from_time)
    end = minutes_of(to_time)
    if start is None or end is None:
        return None
    if end <= start:
        return None
    return end - start


def is_whole_hour_excuse(from_time: str, to_time: str) -> bool:
    duration = excuse_duration_minutes(from_time, to_time)
    return duration is not None and duration >= 60 and duration % 60 == 0


def evaluate_shift(clock_in: str, clock_out: str) -> dict:
    in_minutes = minutes_of(clock_in)
    out_minutes = minutes_of(clock_out)
    start = minutes_of(WINDOW_START)
    flex_end = minutes_of(FLEX_END)
    allowed = minutes_of(WINDOW_END)
    late_minutes = 0
    late_from_start = 0
    late_past_flex = 0
    early_out = 0
    worked_minutes = None
    credited_minutes = None
    short_minutes = 0
    grace_used = 0
    short_after_grace = 0
    expected_out = 0
    if in_minutes is not None:
        late_minutes = max(0, in_minutes - allowed) if allowed is not None else 0
        late_from_start = max(0, in_minutes - start) if start is not None else 0
        late_past_flex = max(0, in_minutes - flex_end) if flex_end is not None else 0
        effective_start = in_minutes if start is None else max(in_minutes, start)
        expected_out = effective_start + REQUIRED_MINUTES
        if out_minutes is not None:
            raw_out = out_minutes
            if raw_out < in_minutes:
                raw_out += 24 * 60
            worked_minutes = raw_out - in_minutes
            credited_minutes = max(0, raw_out - effective_start)
            short_minutes = max(0, REQUIRED_MINUTES - credited_minutes)
            early_out = max(0, expected_out - raw_out)
            deviation = late_minutes + early_out
            grace_used = min(DAILY_GRACE_MINUTES, deviation)
            short_after_grace = max(0, short_minutes - DAILY_GRACE_MINUTES)
    return {
        "late_minutes": late_minutes,
        "late": format_hours(late_minutes),
        "late_hours": decimal_hours(late_minutes),
        "late_from_start": late_from_start,
        "late_past_flex": late_past_flex,
        "early_out": early_out,
        "grace_used": grace_used,
        "worked_minutes": worked_minutes,
        "credited_minutes": credited_minutes,
        "expected_out": expected_out,
        "worked": format_hours(worked_minutes) if worked_minutes is not None else "",
        "worked_hours": decimal_hours(worked_minutes) if worked_minutes is not None else "",
        "short_minutes": short_minutes,
        "short": format_hours(short_minutes),
        "short_hours": decimal_hours(short_minutes),
        "short_after_grace": short_after_grace,
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
    if "MAADI" in name:
        return "Maadi"
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
    if status != "approved":
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


def type_index(requests: list, wanted) -> dict:
    if isinstance(wanted, str):
        wanted = {(wanted or "").strip().lower()}
    else:
        wanted = {str(item).strip().lower() for item in wanted}
    index = defaultdict(list)
    for row in requests:
        status = str(row.get("Status") or "Pending").strip().lower()
        if status != "approved":
            continue
        request_type = str(row.get("Request Type") or "").strip().lower()
        if request_type not in wanted:
            continue
        fingerprint = user_store.normalize_fingerprint_id(row.get("Fingerprint Number"))
        if not fingerprint:
            continue
        device = user_store.normalize_device(row.get("Device") or "")
        for day in date_range(str(row.get("From Date") or ""), str(row.get("To Date") or "")):
            index[(device, fingerprint, day)].append(request_type)
            if not device:
                index[("", fingerprint, day)].append(request_type)
    return index


def saturday_work_index(requests: list) -> dict:
    return type_index(requests, SATURDAY_WORK_TYPE)


def late_excuse_index(requests: list) -> dict:
    return type_index(requests, LATE_EXCUSE_TYPE)


def missing_punch_index(requests: list) -> dict:
    return type_index(requests, MISSING_PUNCH_TYPES)


def fingerprint_sort_key(value) -> tuple:
    text = str(value or "").strip()
    if text.isdigit():
        return (0, int(text))
    return (1, text.lower())


def weekday_index(day: str) -> int | None:
    try:
        return datetime.strptime(day, "%Y-%m-%d").weekday()
    except ValueError:
        return None


def is_friday(day: str) -> bool:
    return weekday_index(day) == 4


def is_saturday(day: str) -> bool:
    return weekday_index(day) == 5


def work_calendar(start: str, end: str) -> list:
    return [day for day in date_range(start, end) if not is_friday(day)]


def weekday_label(day: str) -> str:
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b")
    except ValueError:
        return day


def weekday_ddd(day: str) -> str:
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%a")
    except ValueError:
        return ""


def sheet_date(day: str) -> str:
    try:
        value = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{value.month}/{value.day}/{value.year}"


def notes_ar(types: list) -> str:
    labels = []
    seen = set()
    for item in types or []:
        label = NOTE_AR.get(str(item).strip().lower(), str(item).strip())
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    return " - ".join(labels)


def skips_clock_out(department: str) -> bool:
    text = " ".join((department or "").strip().lower().split())
    if not text:
        return False
    return text in NO_CLOCK_OUT_DEPARTMENTS or "sales" in text or "مبيعات" in text


def missing_punch_reason(types: list) -> str:
    labels = []
    seen = set()
    for item in types or []:
        key = str(item).strip().lower()
        if key == "missing punch in":
            label = "نسيان بصمة حضور"
        elif key == "missing punch out":
            label = "نسيان بصمة انصراف"
        else:
            label = ""
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    return " - ".join(labels) or "نسيان بصمة"


def classify_day(
    punch: dict,
    types: list,
    saturday_work: bool = False,
    late_excuse: bool = False,
    remaining_allowance: int = MONTHLY_LATE_ALLOWANCE_MINUTES,
    missing_punch_types: list | None = None,
    department: str = "",
    holiday: str = "",
) -> dict:
    missing_punch_types = list(missing_punch_types or [])
    skip_out = skips_clock_out(department)
    missing_keys = {str(item).strip().lower() for item in missing_punch_types}
    has_missing_in = "missing punch in" in missing_keys
    has_missing_out = "missing punch out" in missing_keys
    note_types = list(types or [])
    if late_excuse:
        note_types.append(LATE_EXCUSE_TYPE)
    if saturday_work:
        note_types.append(SATURDAY_WORK_TYPE)
    note_types.extend(missing_punch_types)
    notes = notes_ar(note_types)
    remaining = max(0, int(remaining_allowance or 0))
    day = punch.get("date") or ""
    clock_in = punch.get("clock_in") or ""
    clock_out = punch.get("clock_out") or ""
    empty = {"notes": notes, "deduction": "", "reason": "", "remaining": remaining, "used": 0}
    if is_friday(day):
        return empty
    if holiday:
        holiday_note = str(holiday).strip() or "إجازة رسمية"
        notes = f"{holiday_note} - {notes}" if notes else holiday_note
        return {**empty, "notes": notes}
    if is_saturday(day) and not clock_in and not clock_out:
        return empty
    if types:
        return empty
    if not clock_in:
        if has_missing_in:
            if clock_out or has_missing_out or skip_out:
                return empty
            return {
                **empty,
                "notes": notes,
                "deduction": DEDUCTION_HALF,
                "reason": "نسيان بصمة انصراف",
            }
        if clock_out:
            return {
                **empty,
                "notes": notes,
                "deduction": DEDUCTION_HALF,
                "reason": "نسيان بصمة حضور",
            }
        return {
            **empty,
            "notes": notes,
            "deduction": DEDUCTION_FULL,
            "reason": "عدم البصمة ولا يوجد طلب",
        }
    if not clock_out and not skip_out:
        if has_missing_out:
            return empty
        return {
            **empty,
            "notes": notes,
            "deduction": DEDUCTION_HALF,
            "reason": "نسيان بصمة انصراف",
        }
    shift = evaluate_shift(clock_in, clock_out)
    morning = late_penalty(clock_in)
    used = 0
    morning_reason = ""
    if morning:
        late_minutes = shift["late_minutes"]
        needed = ceil_hours_minutes(late_minutes)
        if late_excuse and remaining >= needed:
            morning = ""
            remaining -= needed
            used = needed
        else:
            morning_reason = "تأخير من 10:16 إلى 11:00" if morning == DEDUCTION_QUARTER else "تأخير بعد 11:00"
            if late_excuse:
                morning_reason = f"{morning_reason} - رصيد الإذن لا يكفي"
            used = remaining if late_excuse else 0
            remaining -= used
    evening = ""
    evening_reason = ""
    if not skip_out:
        evening, evening_reason = clock_out_penalty(shift)
    deduction = worse_penalty(morning, evening)
    if not deduction:
        return {
            "notes": notes,
            "deduction": "",
            "reason": "",
            "remaining": remaining,
            "used": used,
        }
    reason = morning_reason if morning and deduction == morning else evening_reason
    return {
        "notes": notes,
        "deduction": deduction,
        "reason": reason,
        "remaining": remaining,
        "used": used,
    }


def build_report(punches: list, requests: list, employees: list, holidays: dict | None = None) -> dict:
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
    saturday_work = saturday_work_index(requests)
    late_excuses = late_excuse_index(requests)
    missing_punches = missing_punch_index(requests)
    punch_dates = [punch["date"] for punch in punches if punch.get("date")]
    from_date = min(punch_dates) if punch_dates else ""
    to_date = max(punch_dates) if punch_dates else ""
    holidays = holidays or {}
    calendar = work_calendar(from_date, to_date)
    people = []
    all_export = []
    missing_total = 0
    late_total = 0
    short_total = 0
    covered_total = 0
    late_minutes_total = 0
    short_minutes_total = 0

    for key, days in sorted(
        by_person.items(),
        key=lambda item: (
            int(item[0][1]) if str(item[0][1]).isdigit() else 10**9,
            (item[1][0].get("name") or "").lower(),
            item[0][0],
        ),
    ):
        device, fingerprint = key
        employee = employees_by_key.get(key)
        sample = days[0]
        punches_by_day = {}
        for punch in days:
            punches_by_day[punch["date"]] = punch
        missing = []
        late = []
        short = []
        covered_days = []
        export_rows = []
        late_minutes = 0
        short_minutes = 0
        full_days = 0
        half_days = 0
        quarter_days = 0
        allowance_used = 0
        remaining = MONTHLY_LATE_ALLOWANCE_MINUTES
        name = (employee or {}).get("name") or sample.get("name") or fingerprint
        department = (employee or {}).get("department") or ""
        for day in calendar:
            punch = punches_by_day.get(day) or {
                "fingerprint": fingerprint,
                "name": name,
                "date": day,
                "device": device,
                "clock_in": "",
                "clock_out": "",
                "absent": True,
            }
            types = covering_types(covered, device, fingerprint, day)
            worked_saturday = bool(covering_types(saturday_work, device, fingerprint, day))
            late_excuse = bool(covering_types(late_excuses, device, fingerprint, day))
            missing_types = covering_types(missing_punches, device, fingerprint, day)
            holiday_label = (holidays.get(day) or "إجازة رسمية") if day in holidays else ""
            result = classify_day(
                punch,
                types,
                worked_saturday,
                late_excuse,
                remaining,
                missing_types,
                department,
                holiday_label,
            )
            remaining = result["remaining"]
            allowance_used += result["used"]
            notes = result["notes"]
            deduction = result["deduction"]
            export_rows.append({
                "fingerprint": fingerprint,
                "name": name,
                "department": department,
                "device": device,
                "date": day,
                "sheet_date": sheet_date(day),
                "weekday": weekday_ddd(day),
                "clock_in": punch.get("clock_in") or "",
                "clock_out": punch.get("clock_out") or "",
                "notes": notes,
                "deduction": deduction,
                "reason": result.get("reason") or "",
                "allowance_used": result["used"],
                "allowance_left": remaining,
            })
            if types or late_excuse or missing_types or worked_saturday:
                covered_days.append({
                    "date": day,
                    "label": weekday_label(day),
                    "types": types or missing_types or (["Monthly Saturday Work"] if worked_saturday else [LATE_EXCUSE_TYPE]),
                })
                covered_total += 1
            if deduction == DEDUCTION_FULL:
                missing.append({
                    "date": day,
                    "label": weekday_label(day),
                    "deduction": DEDUCTION_FULL,
                })
                missing_total += 1
                full_days += 1
            elif deduction in {DEDUCTION_HALF, DEDUCTION_QUARTER}:
                shift = evaluate_shift(punch.get("clock_in") or "", punch.get("clock_out") or "")
                late.append({
                    "date": day,
                    "label": weekday_label(day),
                    "clock_in": punch.get("clock_in") or "",
                    "clock_out": punch.get("clock_out") or "",
                    "deduction": deduction,
                    **shift,
                })
                late_total += 1
                late_minutes += shift["late_minutes"]
                late_minutes_total += shift["late_minutes"]
                if deduction == DEDUCTION_HALF:
                    half_days += 1
                else:
                    quarter_days += 1
        saturday_days = [day for day in calendar if is_saturday(day) and day not in holidays]
        has_monthly_saturday = False
        for day in saturday_days:
            punch = punches_by_day.get(day) or {}
            if punch.get("clock_in") or punch.get("clock_out") or covering_types(saturday_work, device, fingerprint, day):
                has_monthly_saturday = True
                break
        if saturday_days and not has_monthly_saturday:
            target = saturday_days[-1]
            for row in export_rows:
                if row["date"] != target:
                    continue
                if row["deduction"]:
                    break
                row["deduction"] = DEDUCTION_FULL
                row["reason"] = "عدم إدخال السبت الشهري"
                if not row["notes"]:
                    row["notes"] = "عمل السبت الشهري"
                missing.append({
                    "date": target,
                    "label": weekday_label(target),
                    "deduction": DEDUCTION_FULL,
                })
                missing_total += 1
                full_days += 1
                break
        all_export.extend(export_rows)
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
            "full_days": full_days,
            "half_days": half_days,
            "quarter_days": quarter_days,
            "deduction_total": full_days + half_days * 0.5 + quarter_days * 0.25,
            "allowance_used": allowance_used,
            "allowance_left": remaining,
            "allowance_used_text": format_hours(allowance_used),
            "allowance_left_text": format_hours(remaining),
        })

    people.sort(key=lambda item: (fingerprint_sort_key(item["fingerprint"]), item["device"]))
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
        "from_date": from_date,
        "to_date": to_date,
        "export_rows": all_export,
        "full_days_total": sum(person.get("full_days") or 0 for person in people),
        "half_days_total": sum(person.get("half_days") or 0 for person in people),
        "quarter_days_total": sum(person.get("quarter_days") or 0 for person in people),
    }


def format_days_label(value) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if amount <= 0:
        return ""
    if amount == 1:
        return DEDUCTION_FULL
    if amount == 0.5:
        return DEDUCTION_HALF
    if amount == 0.25:
        return DEDUCTION_QUARTER
    if amount == int(amount):
        return f"{int(amount)} يوم"
    text = f"{amount:g}"
    return f"{text} يوم"


def report_sheets(report: dict, adjustments: dict | None = None) -> list:
    adjustments = adjustments or {}
    daily_rows = []
    for item in report.get("export_rows") or []:
        adj = adjustments.get((item.get("device") or "", item.get("fingerprint") or ""), {})
        daily_rows.append([
            item.get("fingerprint") or "",
            item.get("name") or "",
            item.get("sheet_date") or "",
            item.get("weekday") or "",
            item.get("clock_in") or "",
            item.get("clock_out") or "",
            "",
            item.get("notes") or "",
            item.get("deduction") or "",
            item.get("reason") or "",
            format_days_label(adj.get("penalty_days")),
            format_days_label(adj.get("bonus_days")),
        ])
    totals = {}
    for item in report.get("export_rows") or []:
        key = (item.get("device") or "", item.get("fingerprint") or "", item.get("name") or "")
        totals.setdefault(key, {
            "name": item.get("name") or "",
            "fingerprint": item.get("fingerprint") or "",
            "device": item.get("device") or "",
            "department": item.get("department") or "",
            "full": 0,
            "half": 0,
            "quarter": 0,
            "used": 0,
            "left": MONTHLY_LATE_ALLOWANCE_MINUTES,
        })
        totals[key]["left"] = item.get("allowance_left", totals[key]["left"])
        totals[key]["used"] += int(item.get("allowance_used") or 0)
        if item.get("deduction") == DEDUCTION_FULL:
            totals[key]["full"] += 1
        elif item.get("deduction") == DEDUCTION_HALF:
            totals[key]["half"] += 1
        elif item.get("deduction") == DEDUCTION_QUARTER:
            totals[key]["quarter"] += 1
    for (device, fingerprint), adj in adjustments.items():
        key = (device, fingerprint, adj.get("name") or "")
        if key not in totals:
            totals[key] = {
                "name": adj.get("name") or fingerprint,
                "fingerprint": fingerprint,
                "device": device,
                "department": adj.get("department") or "",
                "full": 0,
                "half": 0,
                "quarter": 0,
                "used": 0,
                "left": MONTHLY_LATE_ALLOWANCE_MINUTES,
            }
    summary_rows = []
    for item in sorted(totals.values(), key=lambda row: (fingerprint_sort_key(row["fingerprint"]), row["device"])):
        adj = adjustments.get((item["device"], item["fingerprint"]), {})
        penalty_days = adj.get("penalty_days") or 0
        bonus_days = adj.get("bonus_days") or 0
        if (
            not item["full"]
            and not item["half"]
            and not item["quarter"]
            and not item["used"]
            and not penalty_days
            and not bonus_days
        ):
            continue
        summary_rows.append([
            item["name"],
            item["fingerprint"],
            item["device"],
            item["department"],
            item["full"],
            item["half"],
            item["quarter"],
            item["full"] + item["half"] * 0.5 + item["quarter"] * 0.25,
            format_hours(item["used"]),
            format_hours(item["left"]),
            format_days_label(penalty_days),
            format_days_label(bonus_days),
        ])
    return [
        {
            "name": "ادخال مواعيد الحضور والانصراف",
            "headers": [
                "رقم البصمه",
                "الاسم",
                "التاريخ",
                "",
                "بصمة الحضور الفعلي",
                "بصمة الانصراف الفعلي",
                "",
                "الملاحظات",
                "الخصومات",
                "سبب الخصم",
                "الجزاءات",
                "المكافآت",
            ],
            "rows": daily_rows,
        },
        {
            "name": "إجمالي الخصومات",
            "headers": [
                "الاسم",
                "رقم البصمه",
                "الجهاز",
                "القسم",
                "خصم يوم",
                "خصم نصف يوم",
                "خصم ربع يوم",
                "إجمالي الخصم",
                "إذن التأخير المستخدم",
                "إذن التأخير المتبقي",
                "الجزاءات",
                "المكافآت",
            ],
            "rows": summary_rows,
        },
    ]
