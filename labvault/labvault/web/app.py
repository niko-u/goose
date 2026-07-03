"""FastAPI web app: dashboard, marker charts, reports, review queue, export, API.

Designed to run on the homelab (e.g. labs.compute.casa) with no in-app auth —
network security is handled by the reverse proxy / VPN. The JSON API under
/api is what Hermes can call to decorate the compute.casa tile or integrate.
"""

from __future__ import annotations

import io
import sqlite3
import tempfile
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db
from ..config import get_settings
from ..export import export_csv, export_json

BASE = Path(__file__).parent
app = FastAPI(title="LabVault", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

settings = get_settings()


def get_conn() -> sqlite3.Connection:
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    return conn


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    conn = get_conn()
    try:
        s = db.summary(conn)
        recent = conn.execute(
            "SELECT * FROM reports ORDER BY imported_at DESC LIMIT 8"
        ).fetchall()
        out_of_range = conn.execute(
            """SELECT m.id, m.canonical_name, m.canonical_unit, r.value_num, r.value_text,
                      r.flag, r.collected_at, r.ref_low, r.ref_high
               FROM results r JOIN markers m ON m.id = r.marker_id
               WHERE r.id IN (
                   SELECT id FROM (
                     SELECT id, ROW_NUMBER() OVER (PARTITION BY marker_id ORDER BY collected_at DESC, id DESC) rn
                     FROM results) WHERE rn = 1)
                 AND r.flag IN ('H','L','A')
               ORDER BY m.canonical_name""",
        ).fetchall()
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"summary": s, "recent": recent, "out_of_range": out_of_range},
        )
    finally:
        conn.close()


@app.get("/markers", response_class=HTMLResponse)
def markers_page(request: Request, q: str | None = None):
    conn = get_conn()
    try:
        rows = db.markers_overview(conn, q)
        return templates.TemplateResponse(
            request, "markers.html", {"markers": rows, "q": q or ""}
        )
    finally:
        conn.close()


@app.get("/marker/{marker_id}", response_class=HTMLResponse)
def marker_detail(request: Request, marker_id: int):
    conn = get_conn()
    try:
        marker = conn.execute("SELECT * FROM markers WHERE id = ?", (marker_id,)).fetchone()
        if not marker:
            raise HTTPException(404, "Marker not found")
        series = db.marker_series(conn, marker_id)
        return templates.TemplateResponse(
            request, "marker.html", {"marker": marker, "series": series}
        )
    finally:
        conn.close()


@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM reports ORDER BY collected_date DESC, imported_at DESC").fetchall()
        return templates.TemplateResponse(request, "reports.html", {"reports": rows})
    finally:
        conn.close()


@app.get("/report/{report_id}", response_class=HTMLResponse)
def report_detail(request: Request, report_id: int):
    conn = get_conn()
    try:
        report = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not report:
            raise HTTPException(404, "Report not found")
        results = conn.execute(
            """SELECT r.*, m.canonical_name, m.category FROM results r
               JOIN markers m ON m.id = r.marker_id WHERE r.report_id = ?
               ORDER BY m.category, m.canonical_name""",
            (report_id,),
        ).fetchall()
        return templates.TemplateResponse(
            request, "report.html", {"report": report, "results": results}
        )
    finally:
        conn.close()


@app.post("/report/{report_id}/delete")
def delete_report(report_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/reports", status_code=303)


@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request):
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT r.*, m.canonical_name, m.category, rep.original_filename
               FROM results r JOIN markers m ON m.id = r.marker_id
               JOIN reports rep ON rep.id = r.report_id
               WHERE r.needs_review = 1 ORDER BY r.confidence, m.canonical_name""",
        ).fetchall()
        markers = conn.execute("SELECT id, canonical_name FROM markers ORDER BY canonical_name").fetchall()
        return templates.TemplateResponse(
            request, "review.html", {"rows": rows, "markers": markers}
        )
    finally:
        conn.close()


@app.post("/review/{result_id}/approve")
def review_approve(result_id: int):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE results SET needs_review = 0, reviewed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (result_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/review", status_code=303)


@app.post("/review/{result_id}/reject")
def review_reject(result_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM results WHERE id = ?", (result_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/review", status_code=303)


@app.post("/review/{result_id}/reassign")
def review_reassign(result_id: int, marker_id: int = Form(...), add_alias: str | None = Form(None)):
    """Move a result to a different marker; optionally teach the alias so future
    imports resolve automatically."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT r.marker_id, m.canonical_name FROM results r JOIN markers m ON m.id = r.marker_id WHERE r.id = ?",
            (result_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404)
        old_marker_id = row["marker_id"]
        old_name = row["canonical_name"]
        conn.execute(
            "UPDATE results SET marker_id = ?, needs_review = 0, reviewed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (marker_id, result_id),
        )
        if add_alias and old_name:
            conn.execute(
                "INSERT OR IGNORE INTO marker_aliases (alias, marker_id) VALUES (?, ?)",
                (db.normalize_alias(old_name), marker_id),
            )
        # Clean up the auto-created orphan marker if nothing else references it.
        remaining = conn.execute("SELECT COUNT(*) FROM results WHERE marker_id = ?", (old_marker_id,)).fetchone()[0]
        seeded = conn.execute("SELECT is_seeded FROM markers WHERE id = ?", (old_marker_id,)).fetchone()
        if remaining == 0 and seeded and not seeded["is_seeded"]:
            conn.execute("DELETE FROM markers WHERE id = ?", (old_marker_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/review", status_code=303)


@app.post("/result/{result_id}/delete")
def delete_result(result_id: int):
    conn = get_conn()
    try:
        row = conn.execute("SELECT marker_id FROM results WHERE id = ?", (result_id,)).fetchone()
        conn.execute("DELETE FROM results WHERE id = ?", (result_id,))
        conn.commit()
        marker_id = row["marker_id"] if row else None
    finally:
        conn.close()
    return RedirectResponse(f"/marker/{marker_id}" if marker_id else "/markers", status_code=303)


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse(request, "upload.html", {})


@app.post("/upload")
async def upload_post(request: Request, files: list[UploadFile]):
    from ..ingest import ingest_pdf
    from ..llm import make_backend

    conn = get_conn()
    backend = None
    if settings.llm_backend != "none":
        try:
            backend = make_backend(settings)
        except Exception:
            backend = None
    results = []
    try:
        for uf in files:
            if not uf.filename.lower().endswith(".pdf"):
                results.append({"filename": uf.filename, "status": "failed", "message": "Not a PDF"})
                continue
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
                tmp.write(await uf.read())
                tmp.flush()
                r = ingest_pdf(Path(tmp.name), conn, settings, backend)
            results.append({
                "filename": uf.filename, "status": r.status,
                "message": r.message, "warnings": r.warnings,
                "report_id": r.report_id,
            })
    finally:
        conn.close()
    return templates.TemplateResponse(request, "upload.html", {"results": results})


# --- Export --------------------------------------------------------------

@app.get("/export.csv")
def export_csv_endpoint(marker: int | None = None):
    conn = get_conn()
    buf = io.StringIO()
    try:
        export_csv(conn, buf, marker_id=marker)
    finally:
        conn.close()
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=labvault_export.csv"},
    )


@app.get("/export.json")
def export_json_endpoint(marker: int | None = None):
    conn = get_conn()
    buf = io.StringIO()
    try:
        export_json(conn, buf, marker_id=marker)
    finally:
        conn.close()
    return JSONResponse(content=__import__("json").loads(buf.getvalue()))


# --- JSON API for Hermes -------------------------------------------------

@app.get("/api/summary")
def api_summary():
    conn = get_conn()
    try:
        return db.summary(conn)
    finally:
        conn.close()


@app.get("/api/markers")
def api_markers(q: str | None = None):
    conn = get_conn()
    try:
        rows = db.markers_overview(conn, q)
        return {"markers": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/marker/{marker_id}")
def api_marker_series(marker_id: int):
    conn = get_conn()
    try:
        marker = conn.execute("SELECT * FROM markers WHERE id = ?", (marker_id,)).fetchone()
        if not marker:
            raise HTTPException(404)
        series = db.marker_series(conn, marker_id)
        return {
            "marker": dict(marker),
            "series": [
                {
                    "collected_at": r["collected_at"],
                    "value": r["value_canonical"] if r["value_canonical"] is not None else r["value_num"],
                    "value_text": r["value_text"],
                    "unit": r["unit_canonical"] or r["unit_raw"],
                    "flag": r["flag"],
                    "ref_low": r["ref_low"],
                    "ref_high": r["ref_high"],
                }
                for r in series
            ],
        }
    finally:
        conn.close()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
