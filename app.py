import os
import imaplib
import email
from email.header import decode_header
import io
import csv
from datetime import datetime
import json

import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ========= EMAIL / IMAP CONFIG =========
# Set these in Render environment variables!
IMAP_HOST = os.environ.get("IMAP_HOST", "imap.yourmailserver.com")
IMAP_USER = os.environ.get("IMAP_USER", "production@jlsyachts.com")
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")
IMAP_FOLDER = os.environ.get("IMAP_FOLDER", "INBOX")

# ========= MONDAY CONFIG (from your webhook app) =========
# You can override these with env vars in Render if you like.
MONDAY_TOKEN = os.environ.get(
    "MONDAY_TOKEN",
    "eyJhbGciOiJIUzI1NiJ9.eyJ0aWQiOjU5MTU3ODE5OCwiYWFpIjoxMSwidWlkIjo5NjQ0Nzc2MywiaWFkIjoiMjAyNS0xMS0yOFQwNTo1OTozMi4wMDBaIiwicGVyIjoibWU6d3JpdGUiLCJhY3RpZCI6Mjc3MzY3MTQsInJnbiI6ImV1YzEifQ.9Wez76_J_cHuK15tNti7hcrZJn455qeq-uDyC66IxKE"
)

BOARD_ID = os.environ.get("BOARD_ID", "5088250215")
MONDAY_API_URL = "https://api.monday.com/v2"


# ========= MONDAY HELPER =========

def build_column_values_from_row(row: dict):
    """
    Map a 3CX CSV row into Monday column values.

    Example CSV columns (from your 3CX report):
    - "Call Time"
    - "Call ID"
    - "From"
    - "To"
    - "Direction"
    - "Status"
    - "Ringing"
    - "Talking"
    - "Call Activity Details"
    """

    call_time_raw = row.get("Call Time", "").strip()
    status_raw = row.get("Status", "").strip()
    from_field = row.get("From", "").strip()
    to_field = row.get("To", "").strip()
    ringing_raw = row.get("Ringing", "").strip()   # e.g. "00:00:10"
    talking_raw = row.get("Talking", "").strip()   # e.g. "00:02:15"

    # ---- Parse date (Call Time) into YYYY-MM-DD for Monday date column ----
    # 3CX usually uses "DD/MM/YYYY HH:MM:SS"
    if call_time_raw:
        try:
            dt = datetime.strptime(call_time_raw, "%d/%m/%Y %H:%M:%S")
        except Exception:
            try:
                dt = datetime.fromisoformat(call_time_raw)
            except Exception:
                dt = datetime.utcnow()
    else:
        dt = datetime.utcnow()

    monday_date_value = dt.date().isoformat()

    # ---- Status mapping -> Monday labels "Answered" / "Unanswered" ----
    s = status_raw.lower()
    if any(x in s for x in ["answered", "completed"]):
        status_label = "Answered"
    else:
        status_label = "Unanswered"

    # ---- Duration in seconds from Ringing + Talking ----
    def hms_to_seconds(hms: str) -> int:
        if not hms:
            return 0
        try:
            parts = hms.split(":")
            if len(parts) != 3:
                return 0
            h, m, s = map(int, parts)
            return h * 3600 + m * 60 + s
        except Exception:
            return 0

    ringing_sec = hms_to_seconds(ringing_raw)
    talking_sec = hms_to_seconds(talking_raw)
    total_sec = ringing_sec + talking_sec

    # ---- Build Monday column values using your column IDs ----
    column_values = {
        # Date column (same as in webhook app: "date4")
        "date4": {
            "date": monday_date_value
        },
        # Status column (labels: Answered / Unanswered)
        "status": {
            "label": status_label
        },
        # Caller / From (same column id as webhook: text_mky3718k)
        "text_mky3718k": from_field,
        # To / callee (we use text_mky3878e from the previous mapping)
        "text_mky3878e": to_field,
        # Duration in seconds (same column id as webhook uses for start_time)
        "text_mky3yh4m": str(total_sec),
        # Extra details (caller display name / call details)
        "text_mky7sv9z": row.get("Call Activity Details", "").strip(),
    }

    return column_values, dt


def create_monday_item(column_values: dict, dt: datetime, row: dict) -> dict:
    """
    Create an item on Monday.com for a single call.
    """
    caller = column_values.get("text_mky3718k", "")
    call_id = row.get("Call ID", "").strip()

    if caller:
        item_name = f"Call {call_id} from {caller}"
    else:
        item_name = f"Call {call_id}"

    mutation = """
    mutation ($board_id: ID!, $item_name: String!, $column_values: JSON!) {
      create_item (
        board_id: $board_id,
        item_name: $item_name,
        column_values: $column_values
      ) {
        id
        name
      }
    }
    """

    headers = {
        "Authorization": MONDAY_TOKEN,
        "Content-Type": "application/json",
    }

    body = {
        "query": mutation,
        "variables": {
            "board_id": str(BOARD_ID),
            "item_name": item_name,
            "column_values": json.dumps(column_values),
        },
    }

    resp = requests.post(MONDAY_API_URL, json=body, headers=headers, timeout=20)
    print("Monday response:", resp.status_code, resp.text)
    data = resp.json()
    return data


# ========= EMAIL / CSV IMPORT LOGIC =========

def connect_imap():
    if not (IMAP_HOST and IMAP_USER and IMAP_PASSWORD):
        raise RuntimeError("IMAP settings missing (IMAP_HOST/IMAP_USER/IMAP_PASSWORD).")

    print(f"Connecting to IMAP server {IMAP_HOST} as {IMAP_USER}")
    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    mail.login(IMAP_USER, IMAP_PASSWORD)
    return mail


def find_latest_csv_attachment(mail) -> str:
    """
    Returns the CSV content (string) from the latest email that contains a CSV attachment.
    """
    mail.select(IMAP_FOLDER)
    # Get all messages, we’ll just take the latest that has a CSV
    result, data = mail.search(None, "ALL")
    if result != "OK":
        raise RuntimeError("Failed to search mailbox.")

    ids = data[0].split()
    if not ids:
        raise RuntimeError("No emails found in mailbox.")

    # Walk messages from latest to oldest until we find one with a CSV
    for msg_id in reversed(ids):
        res, msg_data = mail.fetch(msg_id, "(RFC822)")
        if res != "OK":
            continue

        msg = email.message_from_bytes(msg_data[0][1])

        subject = msg.get("Subject", "")
        print("Checking message:", subject)

        if msg.is_multipart():
            for part in msg.walk():
                content_disposition = part.get("Content-Disposition", "")
                if "attachment" in content_disposition:
                    filename = part.get_filename()
                    if not filename:
                        continue

                    decoded_name, enc = decode_header(filename)[0]
                    if isinstance(decoded_name, bytes):
                        decoded_name = decoded_name.decode(enc or "utf-8", errors="ignore")

                    if decoded_name.lower().endswith(".csv"):
                        print("Found CSV attachment:", decoded_name)
                        payload = part.get_payload(decode=True)
                        if payload is None:
                            continue

                        # Decode as text
                        try:
                            csv_text = payload.decode("utf-8-sig", errors="ignore")
                        except Exception:
                            csv_text = payload.decode("latin1", errors="ignore")
                        return csv_text
        else:
            continue

    raise RuntimeError("No CSV attachment found in recent emails.")


def import_latest_report():
    """
    Main function:
    - connects to IMAP
    - finds latest CSV report
    - parses rows
    - pushes each row to Monday.com
    """
    if not MONDAY_TOKEN:
        raise RuntimeError("MONDAY_TOKEN env var is not set and no default provided.")

    mail = connect_imap()
    try:
        csv_text = find_latest_csv_attachment(mail)
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    f = io.StringIO(csv_text)
    reader = csv.DictReader(f)

    rows_processed = 0
    items_created = 0
    errors = []

    for row in reader:
        rows_processed += 1
        try:
            column_values, dt = build_column_values_from_row(row)
            data = create_monday_item(column_values, dt, row)
            if "errors" in data:
                errors.append(data["errors"])
            else:
                items_created += 1
        except Exception as e:
            print("Error processing row:", e)
            errors.append(str(e))

    return {
        "rows_processed": rows_processed,
        "items_created": items_created,
        "errors": errors,
    }


# ========= FLASK ROUTES =========

@app.route("/", methods=["GET"])
def root():
    return "3CX Daily CSV → Monday.com importer is running", 200


@app.route("/run-import", methods=["GET", "POST"])
def run_import_route():
    try:
        result = import_latest_report()
        return jsonify({"status": "ok", "result": result}), 200
    except Exception as e:
        print("Import error:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
