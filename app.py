from flask import Flask, request, jsonify
import requests
import json
from datetime import datetime

app = Flask(__name__)

# ====== CONFIG ======
# ⚠️ Make sure this is your REAL Monday API token
MONDAY_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJ0aWQiOjU5MTU3ODE5OCwiYWFpIjoxMSwidWlkIjo5NjQ0Nzc2MywiaWFkIjoiMjAyNS0xMS0yOFQwNTo1OTozMi4wMDBaIiwicGVyIjoibWU6d3JpdGUiLCJhY3RpZCI6Mjc3MzY3MTQsInJnbiI6ImV1YzEifQ.9Wez76_J_cHuK15tNti7hcrZJn455qeq-uDyC66IxKE"

# Your Monday.com board ID
BOARD_ID = 5088250215

MONDAY_API_URL = "https://api.monday.com/v2"


# ---------------------------------------------------------
#  BUILD MONDAY COLUMN VALUES
# ---------------------------------------------------------
def build_column_values(payload: dict) -> dict:
    """Maps incoming 3CX data into Monday.com columns."""

    # Caller number
    caller = (
        payload.get("caller")
        or payload.get("callerNumber")
        or "Unknown"
    )

    # Caller Display Name
    caller_name = (
        payload.get("callerName")
        or payload.get("CallerDisplayName")
        or ""
    )

    # Start time: use what came from 3CX, or fallback to now
    start_time = payload.get("start_time") or datetime.utcnow().isoformat()

    # Build Monday column map
    column_values = {
        # Date column (today)
        "date4": {
            "date": datetime.utcnow().date().isoformat()
        },

        # Status (always ringing since this is triggered on incoming)
        "status": {
            "label": "Answered"
        },

        # Caller Number
        "text_mky3718k": str(caller),

        # Caller Display Name
        "text_mky7sv9z": str(caller_name),

        # Start Time (ISO string)
        "text_mky3yh4m": str(start_time),
    }

    return column_values


# ---------------------------------------------------------
#  MAIN WEBHOOK ROUTE (/3cx-webhook and /webhook)
# ---------------------------------------------------------
@app.route("/3cx-webhook", methods=["GET", "POST"])
def threecx_webhook():
    """Receives data from 3CX and creates an item on your Monday board."""

    if not MONDAY_TOKEN:
        return jsonify({"error": "MONDAY_TOKEN missing"}), 500

    # Handle simple GET from 3CX
    if request.method == "GET":
        payload = {
            "caller": request.args.get("callerNumber") or "Unknown",
            "callerName": request.args.get("callerName") or "",
            "status": "ringing",
            "start_time": datetime.utcnow().isoformat(),
        }

    # Handle POST (future 3CX webhooks)
    else:
        payload = request.json or {}

    print("Received 3CX payload:", json.dumps(payload, indent=2))

    # Build Monday column values
    column_values = build_column_values(payload)

    # Item name on Monday board
    caller_value = column_values.get("text_mky3718k", "")
    item_name = f"Call from {caller_value or 'Unknown'}"

    # GraphQL mutation
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

    # Send to Monday.com
    try:
        resp = requests.post(MONDAY_API_URL, json=body, headers=headers, timeout=10)
        print("Monday raw response:", resp.status_code, resp.text)
        data = resp.json()
    except Exception as e:
        print("Error contacting Monday:", e)
        return jsonify({"error": "Failed to reach Monday"}), 502

    if "errors" in data:
        return jsonify({"status": "monday_error", "monday_response": data}), 500

    return jsonify({"status": "ok", "monday_response": data}), 200


# Alias route so 3CX can call /webhook (shorter)
@app.route("/webhook", methods=["GET", "POST"])
def webhook_alias():
    return threecx_webhook()


@app.route("/", methods=["GET"])
def root():
    return "3CX → Monday.com Webhook (Render) is running", 200


# Local debug mode
if __name__ == "__main__":
    print("Starting Flask server locally http://127.0.0.1:5000 ...")
    app.run(host="0.0.0.0", port=5000, debug=True)
