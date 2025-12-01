from flask import Flask, request, jsonify
import requests
import json
from datetime import datetime

app = Flask(__name__)

# ====== CONFIG ======
# TODO: put your REAL Monday API token here (the one that worked for create_item)
MONDAY_TOKEN = "YOUR_REAL_MONDAY_TOKEN"
BOARD_ID = 5088250215  # your Monday board ID

MONDAY_API_URL = "https://api.monday.com/v2"


def build_column_values(payload: dict) -> dict:
    """
    Map incoming 3CX webhook JSON (or query params) into Monday.com column values.
    Adjust the payload.get(...) keys if your 3CX field names differ.
    """

    # Caller number
    caller = (
        payload.get("caller")
        or payload.get("CallerID")
        or payload.get("from")
        or payload.get("callerNumber")   # from 3CX Custom CRM URL
        or "Unknown"
    )

    # Agent / destination
    agent = (
        payload.get("agent")
        or payload.get("Agent")
        or payload.get("extension")
        or payload.get("to")
        or ""
    )

    # Raw status from 3CX
    raw_status = (
        payload.get("status")
        or payload.get("Status")
        or "Unknown"
    )

    # Normalize and map 3CX statuses to Monday labels
    s = str(raw_status).strip().lower()

    if s in ("completed", "answered", "connected", "ringing"):
        status_label = "Answered"
    elif s in ("missed", "unanswered", "no answer", "failed", "busy"):
        status_label = "Unanswered"
    else:
        # Default if we don't recognize it
        status_label = "Unanswered"

    # Duration (seconds)
    duration = (
        payload.get("duration")
        or payload.get("Duration")
        or 0
    )

    # Start time / date
    raw_start = (
        payload.get("start_time")
        or payload.get("StartTime")
        or payload.get("timestamp")
        or None
    )

    if raw_start:
        try:
            # Handle 'Z' (UTC) if present
            raw_clean = str(raw_start).replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw_clean)
        except Exception:
            dt = datetime.utcnow()
    else:
        dt = datetime.utcnow()

    monday_date_value = dt.date().isoformat()  # 'YYYY-MM-DD'

    # Build Monday column values using your column IDs
    column_values = {
        # Date column
        "date4": {
            "date": monday_date_value
        },
        # Status column
        "status": {
            "label": status_label
        },
        # Caller number (text)
        "text_mky3718k": str(caller),
        # Agent / destination
        "text_mky3878e": str(agent),
        # Duration (seconds) as text
        "text_mky3yh4m": str(duration),
    }

    return column_values


@app.route("/3cx-webhook", methods=["GET", "POST"])
def threecx_webhook():
    """
    Receives data from 3CX and creates an item on your Monday board.

    - For GET: used by "Open Contact in Custom CRM" URL
      (callerNumber & callerName arrive as query parameters)
    - For POST: future use for a proper JSON webhook
    """

    # Basic auth check
    if not MONDAY_TOKEN:
        return jsonify({"error": "MONDAY_TOKEN is not set in app.py"}), 500

    if request.method == "GET":
        # 3CX Custom CRM URL hits this with query params
        payload = {
            "caller": request.args.get("callerNumber") or "Unknown",
            # 3CX Custom CRM URL does not give agent/duration, so we stub them
            "agent": "",
            "status": "ringing",
            "duration": 0,
            "start_time": datetime.utcnow().isoformat()
        }
    else:
        # JSON POST from a real 3CX webhook (not used yet)
        payload = request.json or {}

    print("Received 3CX payload:", json.dumps(payload, indent=2))

    # Build column values from incoming data
    column_values = build_column_values(payload)

    # Item name shown in the Monday board
    caller_value = column_values.get("text_mky3718k", "")
    item_name = f"Call from {caller_value or 'Unknown'}"

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
            "board_id": str(BOARD_ID),                 # must be string for ID!
            "item_name": item_name,
            "column_values": json.dumps(column_values)  # JSON string
        },
    }

    try:
        resp = requests.post(MONDAY_API_URL, json=body, headers=headers, timeout=10)
        print("Monday raw response:", resp.status_code, resp.text)
        data = resp.json()
    except Exception as e:
        print("Error calling Monday API:", e)
        return jsonify({"error": "Failed to reach Monday.com"}), 502

    if "errors" in data:
        return jsonify({
            "status": "monday_error",
            "monday_response": data,
        }), 500

    return jsonify({
        "status": "ok",
        "monday_response": data,
    }), 200


# Alias so /webhook also works (for your browser tests & 3CX)
@app.route("/webhook", methods=["GET", "POST"])
def webhook_alias():
    return threecx_webhook()


@app.route("/", methods=["GET"])
def root():
    return "3CX → Monday.com Webhook (Render) is running", 200


if __name__ == "__main__":
    print("Starting Flask server on http://127.0.0.1:5000 ...")
    app.run(host="0.0.0.0", port=5000, debug=True)
