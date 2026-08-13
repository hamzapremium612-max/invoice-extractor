# 🧾 Invoice Extractor

Drop in a pile of invoices, get back a spreadsheet. Vendor, invoice number,
date and total, pulled out of PDFs and text files and collected into one CSV.

Built as a learning project — the point is the reliability work around the AI
call, not the AI call itself.

**Live demo:** https://invoice-extractor-612.streamlit.app/

> The bundled samples in `samples/` are invented. No real invoices are in this
> repository, and none ever should be.

## How it works

```
upload  →  read text  →  extract as JSON  →  validate  →  one CSV
           pypdf          the AI call        our code     in memory
```

The AI does exactly one job: turn messy text into a fixed set of fields. Every
other step is plain code, because every other step has a right answer that
does not need judgment.

## The reliability work

This is the part worth reading.

| Problem | What it does |
|---|---|
| The AI returns an unexpected shape | Every response is rebuilt against a fixed field list — missing keys become empty, extra keys are dropped |
| One bad file in a batch of ten | Failures are contained per file. The batch always finishes and reports what broke |
| A rate limit or a network blip | 3 attempts, waits of 3s / 6s / 9s |
| The daily quota is gone | Detected and **not** retried — retrying a daily limit just burns more of it |
| A scanned image with no text layer | Caught early with a message that says what actually happened |
| A 200-page upload | Only the first 6,000 characters are sent |
| **An ambiguous date** | `03/08/2026` is read as day/month/year, **and** the original is returned as `date_as_written` so the conversion can be checked rather than trusted |

That last row is the one that matters most. The others crash loudly. A
misread date produces a confident wrong answer that flows straight into a
spreadsheet someone bills from.

## Running it locally

```bash
pip install -r requirements.txt
```

Put your key in a `.env` file next to `app.py`:

```
GEMINI_API_KEY=your-key-here
```

Then:

```bash
python -m streamlit run app.py     # the web app
python extractor.py                # the same engine, over the samples folder
```

`extractor.py` is the engine; `app.py` is one interface onto it. The terminal
batch is guarded by `if __name__ == "__main__"`, so importing the engine does
not run it.

## Deploying it

The key is read by `get_secret()`, which checks Streamlit's secrets store
first and falls back to `.env`. Same file, both environments, no edits.

On Streamlit Community Cloud, paste this into **Advanced settings → Secrets**:

```toml
GEMINI_API_KEY = "your-key-here"
```

## Known limits

- **No OCR.** A scanned image has no text layer and returns nothing. Real OCR
  needs Tesseract — a *system* binary, installed via `packages.txt`, not a pip
  package in `requirements.txt`.
- It reads what is written. It does not check whether the arithmetic adds up.
- Line items are ignored. Only the header fields are extracted.
- Only the first 6,000 characters of any one document are sent.
- Capped at 5 files and 12 invoices per run, to protect a free-tier quota.

## Two decisions worth explaining

**A PDF holding several invoices is a question, not a detection problem.**
Three one-page invoices and one three-page invoice are *identical* to the
code. So the app does not guess — there is a checkbox, and the person holding
the document answers it. Guessing here would turn a visible question into an
invisible wrong answer.

**Everything uploaded is sent to a third-party AI service to be read.** That
is stated on the page, because a demo that quietly forwards documents to
someone else's server is not being honest with whoever is using it.
