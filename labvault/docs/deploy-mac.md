# Running LabVault on a Mac mini

This sets up LabVault as an always-on service on your Mac mini, using your local
Gemma via Ollama, with OCR for scanned reports.

## 1. Prerequisites

```bash
# Python & the uv tool manager
brew install python@3.11 uv

# OCR (for scanned/photographed reports)
brew install tesseract

# Local model — Ollama with Gemma
brew install ollama
ollama serve &            # or the menu-bar app
ollama pull gemma3        # ~3B/4B is plenty; use gemma3:12b if you have RAM
```

> Prefer LM Studio or llama.cpp? Set `LABVAULT_LLM_BACKEND=openai` and
> `LABVAULT_LLM_BASE_URL=http://localhost:1234/v1` instead of the Ollama vars
> below. Anything OpenAI-compatible works.

## 2. Install LabVault

```bash
git clone https://github.com/niko-u/labvault.git
cd labvault
uv tool install '.[ocr]'      # installs the `labvault` command with OCR extras
```

## 3. Configure

Create `~/.labvault/env` (or put these in your launchd plist below):

```bash
LABVAULT_DATA_DIR=/Users/niko/.labvault
LABVAULT_LLM_BACKEND=ollama
LABVAULT_LLM_BASE_URL=http://localhost:11434
LABVAULT_LLM_MODEL=gemma3
LABVAULT_HOST=127.0.0.1        # proxy fronts it; see compute-casa.md
LABVAULT_PORT=8087
LABVAULT_KEEP_ORIGINALS=false  # true = archive originals under ~/.labvault/originals
LABVAULT_WATCH_DIR=/Users/niko/lab-inbox
```

Verify everything:

```bash
labvault doctor
# Data dir / DB / LLM backend printed
# Privacy check : PASS
# OCR available : True
# LLM reachable : YES
```

## 4. Run as a launchd service

Save as `~/Library/LaunchAgents/casa.labvault.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>casa.labvault</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/niko/.local/bin/labvault</string>
        <string>serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>LABVAULT_DATA_DIR</key><string>/Users/niko/.labvault</string>
        <key>LABVAULT_LLM_BACKEND</key><string>ollama</string>
        <key>LABVAULT_LLM_BASE_URL</key><string>http://localhost:11434</string>
        <key>LABVAULT_LLM_MODEL</key><string>gemma3</string>
        <key>LABVAULT_HOST</key><string>127.0.0.1</string>
        <key>LABVAULT_PORT</key><string>8087</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/Users/niko/.labvault/labvault.log</string>
    <key>StandardErrorPath</key><string>/Users/niko/.labvault/labvault.err</string>
</dict>
</plist>
```

(Confirm the `labvault` path with `which labvault`; `uv tool install` usually puts
it in `~/.local/bin`.)

Load it:

```bash
launchctl load ~/Library/LaunchAgents/casa.labvault.plist
launchctl start casa.labvault
curl -s localhost:8087/healthz    # {"status":"ok"}
```

## 5. Two ways to add reports

- **Web upload**: open `https://labs.compute.casa/upload` and drag PDFs in.
- **Drop folder**: run the watcher so anything saved to `~/lab-inbox` is imported
  automatically (great for a scanner or a Hazel rule). Imported files move to
  `~/lab-inbox/imported/`, failures to `~/lab-inbox/failed/`.

  Optional second launchd agent for the watcher:

  ```xml
  <key>ProgramArguments</key>
  <array>
      <string>/Users/niko/.local/bin/labvault</string>
      <string>watch</string>
      <string>--dir</string>
      <string>/Users/niko/lab-inbox</string>
  </array>
  ```

## 6. Backups

Everything is one SQLite file. Back it up however you back up the Mac mini:

```bash
cp ~/.labvault/labvault.db ~/Backups/labvault-$(date +%F).db
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `LLM reachable : NO` | Is `ollama serve` running? `curl localhost:11434/api/tags` |
| Everything lands in Review | LLM was unreachable at import → regex-only. Fix the LLM and re-import. |
| Scanned PDF → "No extractable text" | `brew install tesseract`, reinstall with `'.[ocr]'` |
| `Privacy check : FAIL` | Your `LABVAULT_LLM_BASE_URL` isn't a private/loopback host. That's the guard working. |
| Password-protected PDF error | Re-export without encryption (Preview → Export as PDF). |
