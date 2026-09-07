from pathlib import Path
import os
import sqlite3
import uuid
from datetime import datetime

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
wh = os.getenv("GOOGLE_SHEET_WEBHOOK", "").strip()
sec = os.getenv("SHEET_SECRET", "").strip()

print("=== PING ===")
r = requests.post(wh, json={"secret": sec, "action": "ping"}, timeout=45)
print(r.text[:250])

print("\n=== LIST ALL ===")
r = requests.post(wh, json={"secret": sec, "action": "list", "department": "ALL"}, timeout=60)
data = r.json()
rows = data.get("rows") or []
print("ok", data.get("ok"), "rows", len(rows))
if rows:
    print("sample keys", list(rows[0].keys())[:8])
    print("latest", rows[0].get("Request ID"), rows[0].get("Name"), rows[0].get("Submitted At"), rows[0].get("Notes"))

print("\n=== CREATE TEST ===")
rid = "T" + uuid.uuid4().hex[:10].upper()
payload = {
    "secret": sec,
    "action": "create",
    "request_id": rid,
    "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "fingerprint_id": "999991",
    "device": "F8",
    "name": "Sheet Sync Probe",
    "department": "Web",
    "team": "",
    "request_type": "Work Remotely",
    "request_date": datetime.now().strftime("%Y-%m-%d"),
    "punch_in_time": "",
    "punch_out_time": "",
    "from_time": "",
    "to_time": "",
    "start_date": datetime.now().strftime("%Y-%m-%d"),
    "end_date": datetime.now().strftime("%Y-%m-%d"),
    "notes": "probe-delete-me",
    "status": "Pending",
}
r = requests.post(wh, json=payload, timeout=60)
print("create", r.text[:300])

print("\n=== LIST AFTER CREATE ===")
r = requests.post(wh, json={"secret": sec, "action": "list", "department": "ALL"}, timeout=60)
rows = (r.json().get("rows") or [])
found = [x for x in rows if str(x.get("Request ID")) == rid]
print("found_probe", len(found), found[:1])

print("\n=== LOCAL DB ===")
conn = sqlite3.connect(BASE / "users.db")
conn.row_factory = sqlite3.Row
print("counts", list(conn.execute("SELECT sync_status, COUNT(*) c FROM form_requests GROUP BY sync_status")))
pending = conn.execute(
    """
    SELECT request_id, name, department, sync_status, sync_attempts, sync_error, submitted_at
    FROM form_requests
    WHERE sync_status = 'pending'
    ORDER BY id DESC LIMIT 10
    """
).fetchall()
print("pending", len(pending))
for row in pending:
    print(dict(row))
recent = conn.execute(
    """
    SELECT request_id, name, sync_status, sync_attempts, sync_error, submitted_at
    FROM form_requests
    ORDER BY id DESC LIMIT 5
    """
).fetchall()
print("recent:")
for row in recent:
    print(dict(row))
conn.close()

log = BASE / "sheet_error.log"
print("\n=== LOG ===")
print(log.read_text(encoding="utf-8", errors="ignore")[:600] if log.exists() else "none")
