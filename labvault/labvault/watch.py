"""Folder watcher: auto-import PDFs dropped into an inbox.

Polls a directory (no external deps). Successfully imported files are moved to
an `imported/` subfolder; failures go to `failed/` with a `.error.txt` note.
This is the one place LabVault will delete/move the user's source file, because
the inbox is explicitly a drop zone.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import db
from .config import Settings
from .ingest import ingest_pdf
from .llm import make_backend


def watch_folder(watch_dir: Path, settings: Settings, *, interval: float = 5.0, use_llm: bool = True) -> int:
    watch_dir = Path(watch_dir)
    watch_dir.mkdir(parents=True, exist_ok=True)
    imported = watch_dir / "imported"
    failed = watch_dir / "failed"
    imported.mkdir(exist_ok=True)
    failed.mkdir(exist_ok=True)

    conn = db.connect(settings.db_path)
    db.init_db(conn)

    backend = None
    if use_llm and settings.llm_backend != "none":
        try:
            backend = make_backend(settings)
        except Exception as e:
            print(f"LLM unavailable ({e}); regex-only.")

    print(f"Watching {watch_dir} every {interval}s. Drop PDFs in to import. Ctrl-C to stop.")
    try:
        while True:
            for pdf in sorted(watch_dir.glob("*.pdf")):
                if not pdf.is_file():
                    continue
                print(f"→ {pdf.name}")
                r = ingest_pdf(pdf, conn, settings, backend, use_llm=use_llm)
                print(f"  {r.status}: {r.message}")
                try:
                    if r.status in ("ok", "skipped"):
                        pdf.replace(imported / pdf.name)
                    else:
                        pdf.replace(failed / pdf.name)
                        (failed / f"{pdf.name}.error.txt").write_text(r.message + "\n")
                except OSError as e:
                    print(f"  ! could not move {pdf.name}: {e}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0
