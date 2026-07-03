# 🧪 LabVault

**Privacy-first medical lab report tracker.** Drop in your lab PDFs — from any lab,
any layout, digital or scanned — and LabVault extracts every result, strips your
personal identifiers, and tracks each marker over time in a local database with a
web dashboard you can host on your homelab.

Your medical data **never leaves your machine**. Extraction and de-identification
run entirely on a **local LLM** (Gemma via Ollama, or any OpenAI-compatible server
like LM Studio / llama.cpp). No data is ever sent to a cloud model — LabVault
refuses to talk to a non-private LLM endpoint unless you explicitly override it.

---

## What it does

- **Ingest any PDF** — PyMuPDF for digital PDFs, automatic **OCR** (Tesseract) for
  scanned/photographed reports, clear errors for password-protected files.
- **Anonymize** — a deterministic regex pass removes names, DOB, MRN/accession
  numbers, phone, email, addresses, age; then the local LLM does a second
  redaction pass. **Only the redacted text is ever stored**; the original PDF is
  not retained unless you opt in.
- **Extract values** — the local LLM pulls every result into structured JSON
  (marker, value, unit, reference range, flag, date). A deterministic table
  parser backs it up and takes over entirely if the LLM is offline.
- **Track over time** — the same marker across many reports/dates becomes one
  time series. Marker name variants ("HDL", "HDL-C", "HDL Cholesterol") map to a
  single canonical marker via an alias table. Units are normalized/converted so a
  marker's history is comparable even when labs report different units.
- **Review queue** — low-confidence extractions, unknown markers, and
  unconvertible units are flagged for a quick human approve / reassign / reject.
- **View & export** — dashboard, per-marker charts with reference-range bands,
  searchable marker list, report browser; export CSV/JSON, plus a JSON API for
  programmatic integration (e.g. a homelab agent).

## Quick start

```bash
# 1. Install (Python 3.11+)
uv tool install .            # or: pipx install .    or: pip install -e .

# 2. Point it at your local Gemma (Ollama shown; see docs for LM Studio)
export LABVAULT_LLM_BACKEND=ollama
export LABVAULT_LLM_MODEL=gemma3          # ollama pull gemma3
export LABVAULT_LLM_BASE_URL=http://localhost:11434

# 3. Check your environment (LLM reachable? OCR available? privacy check?)
labvault doctor

# 4. Import reports and browse
labvault ingest ~/Downloads/labcorp_2024.pdf
labvault serve                            # http://0.0.0.0:8087
```

For scanned PDFs, install OCR extras and Tesseract:

```bash
pip install 'labvault[ocr]'
brew install tesseract        # macOS
```

## Commands

| Command | What it does |
|---|---|
| `labvault ingest FILE...` | Import one or more PDFs |
| `labvault serve` | Run the web UI |
| `labvault watch --dir ~/lab-inbox` | Auto-import PDFs dropped into a folder |
| `labvault export --format csv -o labs.csv` | Export all data (or `--marker ID`) |
| `labvault db --stats` | Show row counts |
| `labvault doctor` | Check LLM/OCR/privacy configuration |

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `LABVAULT_DATA_DIR` | `~/.labvault` | Where the DB and (optional) originals live |
| `LABVAULT_DB_PATH` | `<data>/labvault.db` | SQLite file path |
| `LABVAULT_LLM_BACKEND` | `ollama` | `ollama`, `openai` (OpenAI-compatible), or `none` |
| `LABVAULT_LLM_BASE_URL` | backend default | e.g. `http://localhost:11434` or `http://localhost:1234/v1` |
| `LABVAULT_LLM_MODEL` | `gemma3` | Model name to request |
| `LABVAULT_KEEP_ORIGINALS` | `false` | Archive original PDFs under `<data>/originals/` |
| `LABVAULT_ALLOW_REMOTE_LLM` | `false` | Allow a non-private LLM host (off by default) |
| `LABVAULT_HOST` / `LABVAULT_PORT` | `0.0.0.0` / `8087` | Web bind address |
| `LABVAULT_WATCH_DIR` | — | Default folder for `labvault watch` |

## Privacy model

1. **Local-only LLM.** `labvault doctor` and every LLM call run a privacy check
   that refuses any endpoint not resolving to loopback / private (RFC1918) /
   link-local addresses, unless `LABVAULT_ALLOW_REMOTE_LLM=1`.
2. **Redacted storage.** The database stores only de-identified text. Original
   PDFs are discarded after a successful import unless you opt in.
3. **No third-party calls in the UI.** Chart.js is vendored; the web app makes
   zero external network requests.
4. **Your data, your box.** Everything is a single SQLite file you control.

See [`docs/deploy-mac.md`](docs/deploy-mac.md) for running it as a service on a
Mac mini and [`docs/compute-casa.md`](docs/compute-casa.md) for the homelab tile
and reverse-proxy setup.

## Development

```bash
uv venv && . .venv/bin/activate
uv pip install -e '.[ocr,dev]'
pytest
```

Tests generate synthetic lab PDFs with fake data and mock the LLM — no real
medical data is ever used.

## Disclaimer

LabVault is a personal data-organization tool, **not a medical device**. Extracted
values can contain OCR or LLM errors — always verify against your original report
before drawing conclusions, and consult a clinician for interpretation.
