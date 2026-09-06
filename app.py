import os
import pandas as pd
import streamlit as st

# Borrow the engine. extractor.py guards its terminal batch with
# "if __name__ == '__main__'", so importing it does NOT run a batch.
from extractor import (prepare_jobs, process_many, rows_to_csv,
                       is_image_job, summarise, row_flag, COLUMNS)


def money(value):
    """One place decides how a number is printed, so the headline figure and
    the breakdown under it can never disagree about the same number."""
    return "{:,.2f}".format(value)

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
5. **Summarise** — the total, and what was left out of it

**Every run ends with a summary, including when nothing is wrong.** If it says
*no returns found* and you know you handed it two, the reading is wrong and you
find out immediately. A message that only appears when there is a problem makes
silence mean two things at once.

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
- **Returns are named, and also signed.** A *sale return* is money going back
  to the customer, and its total looks exactly like an invoice total. `total`
  stays exactly as printed on the paper; **`signed_total`** is the same number
  with its direction applied, and it is the column that is safe to `SUM`.
  Naming it in `document_type` alone was not enough — a label tells a person
  something and tells a formula nothing.
- **`signed_total` is blank for quotes and anything unclassified**, so they drop
  out of a sum. That is deliberate, and the app says so out loud when it
  happens — a row leaving a total quietly is how money disappears. Those rows
  are **amber** in the table; subtracted returns are **red**. Out of a hundred
  rows you should not have to hunt for the two worth checking.
- **Two currencies in one batch means no total at all.** Adding rupees to
  dollars is not slightly wrong, it is meaningless, so the number is withheld
  rather than shown with a caveat.
- **It reads what is written. It does not check the arithmetic**, and it cannot
  tell you a document was classified wrongly. The summary exists so that *you*
  can, because you know what you put in.
- Only the first **20,000 characters** of a text document are sent. Going over
  that is never silent — the row says so.
- Max 5 files per run, to protect the daily quota.
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
            summary = summarise(rows)
            unit = ""
            if len(summary["currencies"]) == 1:
                unit = " " + summary["currencies"][0]

            # ---- THE SUMMARY, and it speaks on every run ------------------
            #
            # A column is read by whoever thinks to read it. document_type sat
            # beside the money doing nothing, which is what a reader on the
            # post caught: the label helped a person and did nothing for a
            # formula. A sentence with a number in it gets read.
            #
            # IT REPORTS EVEN WHEN NOTHING IS WRONG, and that is the part that
            # is easy to leave out. Mentioning returns only when returns exist
            # makes silence mean two things at once - "none found" and "none
            # looked for". The person who handed over the pile KNOWS he put
            # two returns in it, so a confident "no returns found" is loudly
            # wrong to the one reader who can tell. Same reason world-brief's
            # watchdog reports on a schedule instead of only on failure.
            if summary["mixed_currency"]:
                st.error(
                    "**More than one currency in this batch: "
                    + ", ".join(summary["currencies"]) + ". No total is shown**, "
                    "because adding them would be meaningless rather than "
                    "merely inaccurate. Filter by currency and total each one "
                    "separately.",
                    icon="🚫",
                )
            elif summary["clean"]:
                st.success(
                    "**Net total " + money(summary["net"]) + unit + "**"
                    + "  \n\nAll " + str(summary["counted"])
                    + " row(s) counted. **No sale returns found, and nothing "
                    "was left out.**"
                    "  \n\nIf you know some of these were returns or refunds, "
                    "then this reading is wrong. Check before you use it.",
                    icon="✅",
                )
            else:
                lines = ["**Net total " + money(summary["net"]) + unit + "**", ""]
                if summary["invoices"]:
                    lines.append("- " + str(summary["invoices"])
                                 + " invoice(s) added: **+"
                                 + money(summary["invoice_total"]) + "**")
                if summary["returns"]:
                    lines.append("- " + str(summary["returns"])
                                 + " return(s) subtracted: **"
                                 + money(summary["return_total"])
                                 + "** — shown in red below")
                if summary["unknown"]:
                    lines.append("- " + str(summary["unknown"])
                                 + " row(s) **NOT counted**, holding "
                                 + money(summary["unknown_value"])
                                 + " — document type unknown, shown in amber")
                if summary["no_total"]:
                    lines.append("- " + str(summary["no_total"])
                                 + " row(s) had no total at all — shown in grey")
                lines.append("")
                lines.append("**Check the coloured rows.** If something you "
                             "know is a return is not red, the reading is wrong.")
                st.warning("\n".join(lines), icon="🧮")

            # ---- The table, with the rows worth checking picked out --------
            # Out of a hundred rows the reader must know WHICH two to check by
            # hand. Both a background AND a text colour are set, because the
            # page renders in the viewer's theme and a pale tint under white
            # text is unreadable.
            frame = pd.DataFrame(rows, columns=COLUMNS)
            flags = [row_flag(row) for row in rows]
            paint = {
                "unknown":    "background-color: #fff3cd; color: #664d03",
                "subtracted": "background-color: #f8d7da; color: #58151c",
                "no_total":   "background-color: #e2e3e5; color: #41464b",
            }

            def colour(frame_row):
                return [paint.get(flags[frame_row.name], "")] * len(frame_row)

            st.dataframe(frame.style.apply(colour, axis=1),
                         use_container_width=True)

            # Only explain colours that are actually on screen. A legend for
            # an empty legend is noise.
            if any(flags):
                st.caption(
                    "🟥 subtracted as a return  ·  "
                    "🟨 not counted, type unknown  ·  "
                    "⬜ no total found"
                )

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
