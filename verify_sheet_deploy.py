"""Verify Google Apps Script deployment. Run: python verify_sheet_deploy.py"""
from pathlib import Path
import os
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
wh = os.getenv("GOOGLE_SHEET_WEBHOOK", "").strip()
sec = os.getenv("SHEET_SECRET", "").strip()

print("Testing webhook...")
wrong = requests.post(wh, json={"secret": "WRONG", "action": "ping"}, timeout=45)
print("1) Wrong secret:", wrong.text[:200])
if "unauthorized" not in wrong.text:
    print("   FAIL - Script still OLD (should return unauthorized).")
else:
    print("   OK")

ping = requests.post(wh, json={"secret": sec, "action": "ping"}, timeout=45)
print("2) Ping:", ping.text[:250])
if "beform-2026-09-08" in ping.text:
    print("   OK - New script is live.")
else:
    print("   FAIL - New script NOT deployed yet.")

listing = requests.post(wh, json={"secret": sec, "action": "list", "department": "ALL"}, timeout=45)
print("3) List:", listing.text[:200])
if '"rows"' in listing.text:
    print("   OK")
else:
    print("   FAIL - list must return rows.")
