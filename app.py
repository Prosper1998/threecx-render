from flask import Flask, request, jsonify
import requests
import json
from datetime import datetime
import os

app = Flask(__name__)

# ====== CONFIG ======
MONDAY_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJ0aWQiOjU5MTU3ODE5OCwiYWFpIjoxMSwidWlkIjo5NjQ0Nzc2MywiaWFkIjoiMjAyNS0xMS0yOFQwNTo1OTozMi4wMDBaIiwicGVyIjoibWU6d3JpdGUiLCJhY3RpZCI6Mjc3MzY3MTQsInJnbiI6ImV1YzEifQ.9Wez76_J_cHuK15tNti7hcrZJn455qeq-uDyC66IxKE"
BOARD_ID = 5088250215
MONDAY_API_URL = "https://api.monday.com/v2"


def build_column_values(payload: dict) -> dict:
    caller = (
        payload.get("caller")
        or payload.get("CallerID")
        or payload.get("from")
        or "Unknown"
    )

    agent = (
        payload.get("agent")
        or payload.get("Agent")
        or payload.get("extension")
        or payload.get("to")
        or ""
    )

    raw_status = (
        payload.get("status")
        or payload.get("Status")
        or "Unknown"
    )

    s = str(raw_status).strip().lower()

    if s in ("completed", "answered", "connected"):
        status_label = "Answered"
    elif s in ("missed", "unanswered", "no answer", "failed", "busy"):
        status_label = "Unanswered"
    else:
        status_label = "Unanswered"

    duration = (
        payload.get("duration")
        or payload.get("Duration")
        or 0
    )

    raw_start = (
        payload.get("start_time")
        or payload.get("StartTime")
        or payload.get("timestamp")
        or None
    )

    if raw_start:
        try:
            raw_clean = str(raw_start).replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw_clean)
        except Exception:
            dt = datetime.utcnow()
    else:
        dt = datetime.utcnow()

    monday_date_value = dt.date().isoformat()

    column_values = {
        "date4": {"date": monday_date_value},
        "status": {"label": status_label},
        "text_mky3718k": str(caller),
        "text_mky3878e": str(agent),
        "text_mky3yh4m": str(duration),
    }

    return column_values


@app.route("/3cx-webhook", methods=["POST"])
def threecx_webhook():
    if not MONDAY_TOKEN:
        return jsonify({"error": "MONDAY_TOKEN is not set"}), 500

    payload = request.json or {}
    print("Received 3CX payload:", json.dumps(payload, indent=2))

    column_values = build_column_values(payload)

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
            "board_id": str(BOARD_ID),
            "item_name": item_name,
            "column_values": json.dumps(column_values),
        },
    }

    try:
        resp = requests.post(MONDAY_API_URL, json=body, headers=headers, timeout=10)
    except Exception as e:
        print("Error calling Monday API:", e)
        return jsonify({"error": "Failed to reach Monday.com"}), 502

    print("Monday raw response:", resp.status_code, resp.text)

    data = resp.json()
    if "errors" in data:
        return jsonify({"status": "monday_error", "monday_response": data}), 500

    return jsonify({"status": "ok", "monday_response": data}), 200


@app.route("/", methods=["GET"])
def root():
    return "3CX → Monday.com Webhook (Render) is running", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Flask server on 0.0.0.0:{port} ...")
    app.run(host="0.0.0.0", port=port, debug=False)
