import os
import streamlit as st

# Borrow the engine. extractor.py guards its terminal batch with
# "if __name__ == '__main__'", so importing it does NOT run a batch.
from extractor import (prepare_jobs, process_many, rows_to_csv,
                       is_image_job, COLUMNS)

st.set_page_config(page_title="Invoice Extractor", page_icon="🧾")

st.title("🧾 Invoice Extractor")
st.caption("Drop in invoices. Get back a spreadsheet.")

REPO = "https://github.com/hamzapremium612-max/invoice-extractor"

# Each item costs an AI call, paid by whoever owns the key.
# A cap is the cheapest possible Kill Switch.
MAX_FILES = 5
MAX_ITEMS = 12          # after page-splitting, one PDF can become many

# Pictures get their own, lower ceiling. Not because they cost more quota - a
# request is a request - but because each one takes 6-12 seconds against about
# 2 for text. Twelve is a two-minute spinner, and a stranger trying a demo does
# not wait two minutes; they assume it has hung and close the tab.
#
# "A picture" means a photo OR a scanned PDF, which is why this is applied
# after prepare_jobs rather than by file extension.
MAX_IMAGES = 3

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
- **Photos are read as pictures, not by OCR.** OCR flattens a page into a
  stream of words, and an invoice is a *table* — once "Total" and its number
  are separated, you are guessing which belongs to which. The model sees the
  layout. Tested on real phone photos, two of them sideways.
- **Scanned PDFs work too.** A PDF is a *container*: inside is either real
  text or one big picture per page. CamScanner and friends produce the second
  kind — beautiful to look at, zero characters inside. When a page has almost
  no text, its picture is read instead. A multi-page scan is treated as one
  invoice, not one per page.
- Numeric dates are read as **day/month/year**. Check `date_as_written`
  if the source used the American convention.
- **`document_type` matters.** A *sale return* is money going
  back to the customer, and its total looks exactly like an invoice total.
  It is named in its own column rather than silently negated.
- Only the first 6,000 characters of a document are sent.
- Max 5 files per run, to protect the daily quota.
- It reads what is written. It does not check the arithmetic.
"""
)

uploaded = st.file_uploader(
    "Upload invoices — PDF, scan, photo or .txt",
    type=["pdf", "txt", "png", "jpg", "jpeg", "webp"],
    accept_multiple_files=True,
    help="A photo of a paper invoice works, and so does a CamScanner PDF. "
         "Hold the phone reasonably still; sideways is fine.",
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

            # Cap pictures HERE, not by file extension, because a scanned PDF
            # is a .pdf that turns out to be a picture. Only prepare_jobs
            # knows which is which - it had to open the file to find out.
            #
            # The extras are DROPPED and SAID OUT LOUD. A file that vanishes
            # without a word is how someone ends up believing an invoice was
            # processed when it never was.
            kept = []
            pictures = 0
            skipped = 0
            for payload, label in jobs:
                if is_image_job(payload):
                    pictures = pictures + 1
                    if pictures > MAX_IMAGES:
                        skipped = skipped + 1
                        continue
                kept.append((payload, label))
            jobs = kept

            if skipped:
                st.warning(
                    "Photos and scans take several seconds each, so only the "
                    "first " + str(MAX_IMAGES) + " are processed. "
                    + str(skipped) + " were not read.",
                    icon="📷",
                )

            if len(jobs) > MAX_ITEMS:
                st.warning(
                    "That came to " + str(len(jobs)) + " invoices. Only the first "
                    + str(MAX_ITEMS) + " will be processed."
                )
                jobs = jobs[:MAX_ITEMS]

            rows, failures, warnings, quota_hit = process_many(jobs)
            failures = read_failures + failures

        # Two different kinds land here now, and neither is a failure:
        #   - a partial read: rows were produced, but part of a file was not seen
        #   - a cross-row clash: every row is fine, but two of them disagree
        # Both mean "it worked, now look at this", which is why they are
        # warnings and not errors. The icon stays neutral because "cut" is only
        # true of the first kind.
        for warning in warnings:
            st.warning(warning, icon="⚠️")

        # Running out on a free tier is a NORMAL ending for a public demo, not
        # a bug, and it must not read like one. Said plainly here rather than
        # buried under "files that could not be read" - those files were fine,
        # and telling a visitor his invoices failed sends him away believing
        # his documents are the problem. They are not; the demo is busy.
        if quota_hit:
            partial = ""
            if rows:
                partial = ("The " + str(len(rows)) + " invoice(s) below came "
                           "through before it ran out. ")
            st.info(
                "**This free demo has used up today's quota.**"
                "\n\n" + partial +
                "It runs on a free API tier with a daily cap, and enough "
                "people have tried it today to reach it. The cap resets "
                "tomorrow."
                "\n\n**Nothing was wrong with your files.** If you would "
                "rather not wait, the code is open — clone it and run it "
                "with your own key: " + REPO,
                icon="🔋",
            )

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
