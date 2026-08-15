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

# Log to the CONSOLE, not to a file.
# A file inside a server container is unreadable - the host's log panel only
# shows what a program prints. Writing the diagnosis somewhere nobody can open
# is the same as not writing it at all.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)

# The HTTP libraries log every single request at INFO. That buries our own
# messages in noise exactly when we need to read them.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

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

SEVERAL INVOICES: if the document contains more than one separate invoice,
return a JSON ARRAY with one object per invoice, in the order they appear.
For a single invoice, return the object on its own. Only split where there
are genuinely distinct invoices - a long invoice running over several pages
is still one invoice.

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


# --- Whatever shape came back, hand out a LIST of clean rows ---
# A document holds SOME invoices - maybe one, maybe ten. Deciding that once,
# here, means nothing downstream has to ask which it got. One invoice is just
# a list of length one.
#
# This is the bug that broke the 10-invoice PDF: the AI correctly returned ten
# records and the old code called .get() on them, which is a dict method.
def to_rows(data):
    # Sometimes it wraps the array instead of returning it bare:
    #   {"invoices": [ {...}, {...} ]}
    if isinstance(data, dict) and len(data) == 1:
        key, value = list(data.items())[0]
        if key not in FIELDS and isinstance(value, list):
            data = value

    if isinstance(data, dict):          # one invoice -> a list holding one
        data = [data]

    # Never trust the shape. Fill anything missing with None, drop anything
    # extra, and ignore junk entries - so every row matches COLUMNS exactly.
    rows = [
        {field: item.get(field) for field in FIELDS}
        for item in data if isinstance(item, dict)
    ]

    if not rows:                        # empty must be loud, not silent
        raise ValueError("The AI returned no usable invoice records.")
    return rows


# --- Extracting: text in, one or more sets of fields out ---
# Retries the way Project 4 taught: 3 attempts, growing waits, and a quota
# error is NOT retried because a daily limit does not reset in 10 seconds.
def extract_invoice(document):
    if not document.strip():
        raise ValueError(
            "No readable text in this file. Scanned images need OCR, "
            "which this version does not do."
        )

    last_error = None                       # remember WHY, not just THAT

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
            return to_rows(data)

        except Exception as error:
            last_error = error
            logging.warning(
                "attempt " + str(attempt) + " failed: "
                + type(error).__name__ + ": " + str(error)
            )
            if is_quota_error(error):
                logging.error("quota exhausted - not retrying")
                raise RuntimeError("Daily free-tier quota reached. Try again tomorrow.")
            time.sleep(attempt * 3)                     # 3s, 6s, 9s

    # Carry the real reason up to the surface. "It failed" is not a diagnosis.
    logging.error("gave up after 3 attempts: " + str(last_error))
    raise RuntimeError(
        "Failed after 3 attempts. Last error was "
        + type(last_error).__name__ + ": " + str(last_error)
    )


# --- One PDF that holds SEVERAL separate invoices, one per page ---
# Note this is a CHOICE the caller makes, not something we detect. A 3-page
# single invoice and 3 one-page invoices look identical to the code. Only the
# person holding the document knows which it is, so we let them say.
def split_pdf_pages(source, filename):
    reader = PdfReader(source)
    jobs = []
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        jobs.append((text, filename + " (page " + str(number) + ")"))
    return jobs


# --- Turn uploaded files into a list of (text, label) to work through ---
# Reading can fail on its own (a scan, a corrupt file), and that failure must
# be contained here rather than killing the whole batch.
def prepare_jobs(files, split_pages=False):
    jobs = []
    failures = []

    for source, filename in files:
        try:
            if split_pages and filename.lower().endswith(".pdf"):
                jobs.extend(split_pdf_pages(source, filename))
            else:
                jobs.append((read_document(source, filename), filename))
        except Exception as error:
            logging.warning("could not read " + filename + ": " + str(error))
            failures.append({"source_file": filename, "error": str(error)})

    return jobs, failures


# --- One piece of text, start to finish. Never raises. ---
# Returns (row, error). Exactly one of them is None, so the caller always
# knows which happened without inspecting the row.
def process_one(text, label):
    try:
        rows = extract_invoice(text)

        # If one document held several invoices, number them - otherwise every
        # row would carry the same name and you could not trace one back.
        for number, row in enumerate(rows, start=1):
            if len(rows) > 1:
                row["source_file"] = (label + " (" + str(number)
                                      + " of " + str(len(rows)) + ")")
            else:
                row["source_file"] = label

        return rows, None
    except Exception as error:
        logging.warning("failed on " + label + ": " + str(error))
        return None, str(error)


# --- Many. One bad item must never cost us the good ones. ---
# Project 4's rule, applied again: the batch always finishes.
def process_many(jobs):
    rows = []
    failures = []

    for text, label in jobs:
        found, error = process_one(text, label)
        if found:
            rows.extend(found)          # extend, not append - could be several
        else:
            failures.append({"source_file": label, "error": error})

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
    jobs, read_failures = prepare_jobs(files)
    rows, failures = process_many(jobs)
    failures = read_failures + failures

    for row in rows:
        print("  OK  ", row["source_file"], "->", row["vendor"], row["total"])
    for fail in failures:
        print("  FAIL", fail["source_file"], "->", fail["error"])

    output_name = "invoices_" + str(date.today()) + ".csv"
    with open(output_name, "w", newline="", encoding="utf-8") as f:
        f.write(rows_to_csv(rows))

    print("\nWrote", len(rows), "rows to", output_name, "-", len(failures), "failed")
