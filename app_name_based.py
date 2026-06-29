"""
WhatsApp Sales Bot — Name-based version
----------------------------------------
Staff message your Twilio WhatsApp number with their NAME (as it appears in the
"SALESPERSON" column of the MM REPORT sheet) and get back:
  - their own total sales this month
  - their division's total sales this month

No phone-number directory needed — identification is purely by the name typed
in the message. (Trade-off: no real verification that the sender is who they
claim to be — fine for low-stakes internal use, not for anything sensitive.)

Required environment variables:
  GOOGLE_CREDENTIALS_JSON   -> full contents of your Google service account JSON key
  SPREADSHEET_ID            -> the ID from the MM REPORT Google Sheet URL

Expected sheet columns (tab name: "Sheet1" by default — change SHEET_TAB_NAME below
if yours differs):
  BARCODE, BILLNO, BILLDATE, ITEMNAME, SIZE, CUSTOMERNAME, SALESPERSON, DIVISION,
  DEPARTMENT, SECTION, BILLQTY, NETAMT, Store, Trans, month
"""

import os
import json
import re
from datetime import datetime

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SHEET_TAB_NAME = "Sheet1"  # change if your MM REPORT tab is named differently

KEYWORDS = ["sales", "division", "help", "my", "report", "show", "me"]


def get_sheet_client():
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def normalize(s):
    return re.sub(r"\s+", " ", s.strip()).lower()


def strip_keywords(text):
    words = text.split()
    kept = [w for w in words if normalize(w) not in KEYWORDS]
    return " ".join(kept)


def parse_date(date_str):
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def to_float(amount_str):
    try:
        return float(str(amount_str).replace(",", "").strip())
    except ValueError:
        return 0.0


def format_currency(amount):
    return f"₹{amount:,.0f}"


def find_matching_name(candidate, known_names):
    """Exact match first, then substring match in either direction."""
    if not candidate:
        return None, []
    norm_candidate = normalize(candidate)
    for name in known_names:
        if normalize(name) == norm_candidate:
            return name, []
    matches = [
        name
        for name in known_names
        if norm_candidate in normalize(name) or normalize(name) in norm_candidate
    ]
    if len(matches) == 1:
        return matches[0], []
    return None, matches  # 0 or multiple matches -> ambiguous/none


@app.route("/whatsapp-webhook", methods=["POST"])
def whatsapp_webhook():
    incoming_msg = request.form.get("Body", "").strip()
    resp = MessagingResponse()
    msg = resp.message()

    norm_msg = normalize(incoming_msg)

    if norm_msg == "help":
        msg.body(
            "Type your name as it appears in the sales report to get your summary.\n\n"
            "Examples:\n"
            "• 'Sreela Manoj' - your sales + your division's total\n"
            "• 'Sreela Manoj sales' - just your personal sales\n"
            "• 'Sreela Manoj division' - just your division's total"
        )
        return str(resp)

    try:
        client = get_sheet_client()
        sheet = client.open_by_key(os.environ["SPREADSHEET_ID"])
        rows = sheet.worksheet(SHEET_TAB_NAME).get_all_records()
    except Exception as e:
        msg.body(f"Sorry, something went wrong reaching the sales sheet. ({e})")
        return str(resp)

    known_names = sorted({str(r.get("SALESPERSON", "")).strip() for r in rows if r.get("SALESPERSON")})

    candidate = strip_keywords(incoming_msg)
    matched_name, ambiguous = find_matching_name(candidate, known_names)

    if matched_name is None:
        if ambiguous:
            msg.body(
                "That name matches more than one person — please type the full name "
                "exactly as it appears in the sales report."
            )
        else:
            msg.body(
                "I couldn't match that to a salesperson in the report. "
                "Please send your name exactly as it appears in the sales sheet, "
                "or type 'help' for instructions."
            )
        return str(resp)

    # Figure out this person's division (first row found with their name)
    division = next(
        (str(r.get("DIVISION", "")).strip() for r in rows if str(r.get("SALESPERSON", "")).strip() == matched_name),
        "",
    )

    now = datetime.now()

    def sum_for(filter_key, filter_value):
        total = 0.0
        for r in rows:
            if str(r.get(filter_key, "")).strip() != filter_value:
                continue
            d = parse_date(str(r.get("BILLDATE", "")))
            if d is None or d.month != now.month or d.year != now.year:
                continue
            total += to_float(r.get("NETAMT", 0))
        return total

    want_sales = "sales" in norm_msg or "division" not in norm_msg
    want_division = "division" in norm_msg or "sales" not in norm_msg

    lines = [f"Hi {matched_name} 👋"]
    if want_sales:
        personal_total = sum_for("SALESPERSON", matched_name)
        lines.append(f"Your sales (this month): {format_currency(personal_total)}")
    if want_division and division:
        div_total = sum_for("DIVISION", division)
        lines.append(f"{division} division total (this month): {format_currency(div_total)}")

    lines.append("\nType 'help' for more options.")
    msg.body("\n".join(lines))
    return str(resp)


@app.route("/", methods=["GET"])
def health_check():
    return "WhatsApp Sales Bot (name-based) is running."


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
