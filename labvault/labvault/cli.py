"""LabVault command line.

    labvault ingest report1.pdf report2.pdf   # import PDFs
    labvault serve                             # run the web UI
    labvault watch                             # auto-import a drop folder
    labvault export --format csv               # dump all data
    labvault db --init | --stats               # database maintenance
    labvault doctor                            # check environment/LLM/OCR
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db
from .config import get_settings


def _cmd_ingest(args) -> int:
    from .ingest import ingest_paths

    settings = get_settings()
    settings.ensure_dirs()
    conn = db.connect(settings.db_path)
    db.init_db(conn)

    paths = [Path(p) for p in args.files]
    missing = [p for p in paths if not p.exists()]
    for p in missing:
        print(f"! not found: {p}", file=sys.stderr)
    paths = [p for p in paths if p.exists()]
    if not paths:
        return 1

    reports = ingest_paths(paths, conn, settings, use_llm=not args.no_llm)
    rc = 0
    for r in reports:
        icon = {"ok": "✓", "skipped": "•", "failed": "✗"}.get(r.status, "?")
        print(f"{icon} {r.filename}: {r.message or r.status}")
        for w in r.warnings:
            print(f"    - {w}")
        if r.status == "failed":
            rc = 1
    return rc


def _cmd_serve(args) -> int:
    import uvicorn

    settings = get_settings()
    settings.ensure_dirs()
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    conn.close()
    print(f"LabVault serving on http://{settings.host}:{settings.port}  (db: {settings.db_path})")
    uvicorn.run("labvault.web.app:app", host=settings.host, port=settings.port, log_level="info")
    return 0


def _cmd_watch(args) -> int:
    from .watch import watch_folder

    settings = get_settings()
    settings.ensure_dirs()
    watch_dir = Path(args.dir) if args.dir else settings.watch_dir
    if not watch_dir:
        print("Set LABVAULT_WATCH_DIR or pass --dir", file=sys.stderr)
        return 1
    return watch_folder(watch_dir, settings, interval=args.interval, use_llm=not args.no_llm)


def _cmd_export(args) -> int:
    from .export import export_csv, export_json

    settings = get_settings()
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    out = sys.stdout
    if args.output:
        out = open(args.output, "w", newline="")
    try:
        if args.format == "csv":
            export_csv(conn, out, marker_id=args.marker)
        else:
            export_json(conn, out, marker_id=args.marker)
    finally:
        if args.output:
            out.close()
            print(f"Wrote {args.output}", file=sys.stderr)
    return 0


def _cmd_db(args) -> int:
    settings = get_settings()
    settings.ensure_dirs()
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    if args.stats:
        s = db.summary(conn)
        for k, v in s.items():
            print(f"{k:>20}: {v}")
    else:
        print(f"Database initialized at {settings.db_path}")
    return 0


def _cmd_doctor(args) -> int:
    from .llm import make_backend
    from .pdf_extract import _ocr_available

    settings = get_settings()
    print(f"Data dir      : {settings.data_dir}")
    print(f"Database      : {settings.db_path}")
    print(f"LLM backend   : {settings.llm_backend}  ({settings.llm_base_url}, model={settings.llm_model})")
    print(f"Keep originals: {settings.keep_originals}")
    print(f"OCR available : {_ocr_available()}")
    try:
        settings.check_llm_privacy()
        print("Privacy check : PASS (endpoint is local/private)")
    except Exception as e:
        print(f"Privacy check : FAIL — {e}")
    if settings.llm_backend != "none":
        try:
            backend = make_backend(settings)
            ok = backend.available()
            print(f"LLM reachable : {'YES' if ok else 'NO'}")
        except Exception as e:
            print(f"LLM reachable : NO — {e}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="labvault", description="Privacy-first lab report anonymizer & tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser("ingest", help="import one or more PDF reports")
    p_ing.add_argument("files", nargs="+")
    p_ing.add_argument("--no-llm", action="store_true", help="skip the local LLM; regex-only (all results flagged)")
    p_ing.set_defaults(func=_cmd_ingest)

    p_srv = sub.add_parser("serve", help="run the web UI")
    p_srv.set_defaults(func=_cmd_serve)

    p_watch = sub.add_parser("watch", help="auto-import PDFs dropped into a folder")
    p_watch.add_argument("--dir", help="folder to watch (default LABVAULT_WATCH_DIR)")
    p_watch.add_argument("--interval", type=float, default=5.0)
    p_watch.add_argument("--no-llm", action="store_true")
    p_watch.set_defaults(func=_cmd_watch)

    p_exp = sub.add_parser("export", help="export data as CSV or JSON")
    p_exp.add_argument("--format", choices=["csv", "json"], default="csv")
    p_exp.add_argument("--marker", type=int, help="limit to one marker id")
    p_exp.add_argument("--output", "-o", help="write to file instead of stdout")
    p_exp.set_defaults(func=_cmd_export)

    p_db = sub.add_parser("db", help="database maintenance")
    p_db.add_argument("--init", action="store_true", help="create/upgrade schema")
    p_db.add_argument("--stats", action="store_true", help="print row counts")
    p_db.set_defaults(func=_cmd_db)

    p_doc = sub.add_parser("doctor", help="check environment, OCR, and LLM connectivity")
    p_doc.set_defaults(func=_cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
