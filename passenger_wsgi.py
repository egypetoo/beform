import os
import sys
import traceback

APP_DIR = os.path.dirname(__file__)
sys.path.insert(0, APP_DIR)

try:
    from app import app as application
except Exception:
    log_path = os.path.join(APP_DIR, "passenger_error.log")
    with open(log_path, "w", encoding="utf-8") as log_file:
        traceback.print_exc(file=log_file)
    raise