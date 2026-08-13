import os
import csv
import io
import json
import time
import logging
from dotenv import load_dotenv
from openai import OpenAI
from pypdf import PdfReader

load_dotenv()


# The key lives in a different place depending on WHERE this runs.
# Deployed on Streamlit Cloud -> st.secrets. On the laptop -> .env.
# Try the cloud first, fall back to local, and SAY which one worked.
def get_secret(name):
    try:
        import streamlit as st
        print("get_secret: st.secrets readable. Keys:", list(st.secrets.keys()))
        return st.secrets[name]
    except Exception as error:
        print("get_secret: st.secrets failed ->", type(error).__name__, ":", error)

    value = os.getenv(name)
    print("get_secret: os.getenv fallback ->", "FOUND" if value else "NOT FOUND")
    return value


gemini_key = get_secret("GEMINI_API_KEY")

if not gemini_key:
    raise RuntimeError(
        "GEMINI_API_KEY not found.\n"
        "  Locally: put it in a .env file next to this one.\n"
        '  On Streamlit Cloud: Settings -> Secrets ->  GEMINI_API_KEY = "your-key"'
    )

logging.basicConfig(
    filename="extractor.log",
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)

ai = OpenAI(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=gemini_key,
)

MODEL = "gemini-3.1-flash-lite"

# The four fields we promise to return. Named once, used everywhere - so the
# prompt, the validation and the CSV columns can never drift apart.
FIELDS = ["vendor", "invoice_number", "date", "date_as_written", "total"]
COLUMNS = ["source_file"] + FIELDS

# A stranger can upload a 200-page PDF. Sending all of it would cost real money
# for no gain - an invoice's useful data is always near the top.
MAX_CHARS = 6000

INSTRUCTIONS = """You extract data from invoices.
Return ONLY a JSON object with exactly these fields:
{
  "vendor": "the company that ISSUED the invoice, not the recipient",
  "invoice_number": "the invoice number",
  "date": "the invoice date in YYYY-MM-DD format",
  "date_as_written": "the invoice date copied EXACTLY as it appears in the document",
  "total": "the final total amount due, as a number without currency symbols"
}
If a field is missing from the document, use null. Never guess a value.

DATES: a numeric date like 03/08/2026 is ambiguous. Read it as DAY/MONTH/YEAR,
not month/day/year. Always also return date_as_written so a human can check
the conversion instead of trusting it."""


# Some failures are worth retrying (a blip). A used-up daily quota is not -
# retrying just burns more of it. Tell them apart.
def is_quota_error(error):
    text = str(error)
    return "RESOURCE_EXHAUSTED" in text or "exceeded your current quota" in text


# --- Reading: works for a file PATH or an UPLOADED file ---
# On the laptop we hand this a path string. In the web app the file never
# touches the disk - Streamlit hands us an object that is already in memory.
# One function serves both, because it asks what the thing CAN DO, not what
# it is: "does it have a .read() method?"
def read_document(source, filename):
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(source)          # pypdf accepts a path OR a file object
        text = ""
        for page in reader.pages:
            text = text + (page.extract_text() or "")   # a scanned page returns None
        return text

    if hasattr(source, "read"):             # an uploaded file, already in memory
        return source.read().decode("utf-8", errors="replace")

    with open(source, encoding="utf-8") as f:           # a path on disk
        return f.read()


# --- Extracting: text in, four fields out ---
# Retries the way Project 4 taught: 3 attempts, growing waits, and a quota
# error is NOT retried because a daily limit does not reset in 10 seconds.
def extract_invoice(document):
    if not document.strip():
        raise ValueError(
            "No readable text in this file. Scanned images need OCR, "
            "which this version does not do."
        )

    for attempt in range(1, 4):
        try:
            reply = ai.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": INSTRUCTIONS},
                    {"role": "user", "content": document[:MAX_CHARS]},
                ],
                temperature=0,                          # extraction, not writing
                response_format={"type": "json_object"},
            )
            data = json.loads(reply.choices[0].message.content)

            # Never trust the shape of what came back. Fill anything missing
            # with None and drop anything extra, so every row matches COLUMNS.
            return {field: data.get(field) for field in FIELDS}

        except Exception as error:
            logging.warning("attempt " + str(attempt) + " failed: " + str(error))
            if is_quota_error(error):
                logging.error("quota exhausted - not retrying")
                raise RuntimeError("Daily free-tier quota reached. Try again tomorrow.")
            if isinstance(error, ValueError):
                raise                                   # a bad file, not a blip
            time.sleep(attempt * 3)                     # 3s, 6s, 9s

    logging.error("gave up after 3 attempts")
    raise RuntimeError("The AI service did not respond after 3 attempts.")


# --- One file, start to finish. Never raises. ---
# Returns (row, error). Exactly one of them is None, so the caller always
# knows which happened without inspecting the row.
def process_one(source, filename):
    try:
        text = read_document(source, filename)
        row = extract_invoice(text)
        row["source_file"] = filename
        return row, None
    except Exception as error:
        logging.warning("failed on " + filename + ": " + str(error))
        return None, str(error)


# --- Many files. One bad file must never cost us the good ones. ---
# Project 4's rule, applied again: the batch always finishes.
def process_many(files):
    rows = []
    failures = []

    for source, filename in files:
        row, error = process_one(source, filename)
        if row:
            rows.append(row)
        else:
            failures.append({"source_file": filename, "error": error})

    return rows, failures


# --- The CSV, built in memory instead of written to disk ---
# On a server there is nowhere useful to write a file to, and the user could
# not reach it anyway. So we build the text and hand it back as a string for
# the browser to download.
def rows_to_csv(rows):
    buffer = io.StringIO()                  # a file that only exists in memory
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


# --- The terminal interface ---
# Only runs when this file is launched directly. When app.py imports it, this
# block is skipped - so the web app borrows the engine without running a batch.
if __name__ == "__main__":
    from datetime import date

    folder = "samples"
    filenames = os.listdir(folder)
    print("Found", len(filenames), "files in", folder)

    files = [(os.path.join(folder, name), name) for name in filenames]
    rows, failures = process_many(files)

    for row in rows:
        print("  OK  ", row["source_file"], "->", row["vendor"], row["total"])
    for fail in failures:
        print("  FAIL", fail["source_file"], "->", fail["error"])

    output_name = "invoices_" + str(date.today()) + ".csv"
    with open(output_name, "w", newline="", encoding="utf-8") as f:
        f.write(rows_to_csv(rows))

    print("\nWrote", len(rows), "rows to", output_name, "-", len(failures), "failed")
