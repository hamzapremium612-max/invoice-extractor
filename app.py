import os
import streamlit as st

# Borrow the engine. extractor.py guards its terminal batch with
# "if __name__ == '__main__'", so importing it does NOT run a batch.
from extractor import prepare_jobs, process_many, rows_to_csv, COLUMNS

st.set_page_config(page_title="Invoice Extractor", page_icon="🧾")

st.title("🧾 Invoice Extractor")
st.caption("Drop in invoices. Get back a spreadsheet.")

# Each item costs an AI call, paid by whoever owns the key.
# A cap is the cheapest possible Kill Switch.
MAX_FILES = 5
MAX_ITEMS = 12          # after page-splitting, one PDF can become many

st.warning(
    "**Anything you upload is sent to Google's AI service to be read.** "
    "This is a public demo — use invoices you are happy to share, not real "
    "medical, financial or client documents.",
    icon="⚠️",
)

st.sidebar.header("How it works")
st.sidebar.markdown(
    """
1. **Read** — text out of the PDF or .txt
2. **Extract** — the AI returns the fields as JSON
3. **Validate** — anything missing becomes empty, so every row matches
4. **Collect** — one row per file, into a CSV

The AI is told to return *only* JSON and to never guess a missing value.
Temperature is **0** — this is extraction, not writing, so the same invoice
must always give the same answer.

**`date_as_written`** is the date copied exactly as it appeared. `03/08/2026`
is ambiguous, so the conversion is shown next to the original rather than
asked to be trusted.
"""
)
st.sidebar.header("Known limits")
st.sidebar.markdown(
    """
- **No OCR.** A scanned image has no text layer, so there is nothing to read.
  Real OCR needs Tesseract, a system binary — not a pip package.
- Numeric dates are read as **day/month/year**. Check `date_as_written`
  if the source used the American convention.
- Only the first 6,000 characters of a document are sent.
- Max 5 files per run, to protect the daily quota.
- It reads what is written. It does not check the arithmetic.
"""
)

uploaded = st.file_uploader(
    "Upload invoices (PDF or .txt)",
    type=["pdf", "txt"],
    accept_multiple_files=True,
)

split_pages = st.checkbox(
    "This PDF holds a separate invoice on every page",
    help="The code cannot tell a 3-page invoice from 3 one-page invoices. "
         "Only you know which it is, so you tell it.",
)

use_samples = st.checkbox("Or try it with the bundled sample invoices")

# Build one list of (source, filename) pairs, whichever route the user took.
# The engine does not care where a file came from - it only needs something
# it can read and a name to label the row with.
files = []
if use_samples:
    for name in sorted(os.listdir("samples")):
        files.append((os.path.join("samples", name), name))
elif uploaded:
    for item in uploaded:
        files.append((item, item.name))

if files:
    if len(files) > MAX_FILES:
        st.warning("Only the first " + str(MAX_FILES) + " files will be processed.")
        files = files[:MAX_FILES]

    if st.button("Extract " + str(len(files)) + " file(s)", type="primary"):
        with st.spinner("Reading and extracting..."):
            jobs, read_failures = prepare_jobs(files, split_pages=split_pages)

            if len(jobs) > MAX_ITEMS:
                st.warning(
                    "That came to " + str(len(jobs)) + " invoices. Only the first "
                    + str(MAX_ITEMS) + " will be processed."
                )
                jobs = jobs[:MAX_ITEMS]

            rows, failures, warnings = process_many(jobs)
            failures = read_failures + failures

        # Partial reads are not failures - they produced rows. But they must
        # be said out loud, or an invoice goes missing with no explanation.
        for warning in warnings:
            st.warning(warning, icon="✂️")

        if rows:
            st.success("Extracted " + str(len(rows)) + " invoice(s).")
            st.dataframe(rows, column_order=COLUMNS, use_container_width=True)

            st.download_button(
                "Download CSV",
                data=rows_to_csv(rows),
                file_name="invoices.csv",
                mime="text/csv",
            )

        # A failure never stops the batch - it gets reported instead.
        if failures:
            st.warning(str(len(failures)) + " file(s) could not be read.")
            with st.expander("What went wrong"):
                for fail in failures:
                    st.markdown("**" + fail["source_file"] + "** — " + fail["error"])

        if not rows and not failures:
            st.info("Nothing to show.")
else:
    st.info("Upload a file, or tick the box above to try the samples.")
