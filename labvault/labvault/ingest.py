"""Ingest pipeline: PDF -> redacted text -> extracted results -> SQLite.

Orchestrates dedup, extraction, anonymization, normalization, and storage.
The original file is deleted after a successful ingest unless
LABVAULT_KEEP_ORIGINALS is set, in which case it's moved into the originals
vault. Only redacted text is ever written to the database.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .anonymize import anonymize
from .config import Settings
from .extract import extract_results
from .llm import LLMBackend, make_backend
from .normalize import compute_flag, parse_date, parse_reference_range, parse_value
from .pdf_extract import ExtractedPdf, PdfEncryptedError, PdfError, extract_pdf, file_sha256


@dataclass
class IngestReport:
    filename: str
    status: str  # ok | skipped | failed
    message: str = ""
    report_id: int | None = None
    n_results: int = 0
    n_review: int = 0
    used_ocr: bool = False
    warnings: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ingest_pdf(
    path: Path | str,
    conn: sqlite3.Connection,
    settings: Settings,
    backend: LLMBackend | None = None,
    *,
    use_llm: bool = True,
) -> IngestReport:
    path = Path(path)
    result = IngestReport(filename=path.name, status="failed")

    # 1. Dedup by content hash.
    try:
        file_hash = file_sha256(path)
    except OSError as e:
        result.message = f"Cannot read file: {e}"
        return result
    existing = conn.execute("SELECT id FROM reports WHERE file_hash = ?", (file_hash,)).fetchone()
    if existing:
        result.status = "skipped"
        result.report_id = existing["id"]
        result.message = "Already imported (identical file hash)."
        return result

    # 2. Extract text (+ OCR fallback).
    try:
        extracted: ExtractedPdf = extract_pdf(path)
    except PdfEncryptedError as e:
        result.message = str(e)
        return result
    except PdfError as e:
        result.message = str(e)
        return result
    result.used_ocr = extracted.used_ocr
    if extracted.used_ocr:
        result.warnings.append(f"OCR used on page(s): {extracted.ocr_pages}")

    # 3. Extract structured results from the ORIGINAL text (better for the LLM),
    #    then anonymize what we store.
    if backend is None and use_llm and settings.llm_backend != "none":
        try:
            backend = make_backend(settings)
        except Exception as e:
            result.warnings.append(f"LLM unavailable ({e}); using regex fallback.")
            backend = None

    extraction = extract_results(extracted.text, backend if use_llm else None)
    if not extraction.llm_used:
        result.warnings.append("LLM did not contribute; results are regex-only and flagged for review.")

    # 4. Anonymize text for storage.
    anon = anonymize(extracted.text, backend if use_llm else None, use_llm=use_llm)

    # 5. Resolve dates.
    report_collected = parse_date(extraction.collected_date)
    report_reported = parse_date(extraction.reported_date)
    fallback_date = report_collected or report_reported or _now_iso()[:10]

    # 6. Insert report row.
    status = "ok"
    cur = conn.execute(
        """INSERT INTO reports
           (file_hash, original_filename, source_lab, collected_date, reported_date,
            imported_at, page_count, used_ocr, status, redacted_text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            file_hash, path.name, extraction.source_lab, report_collected, report_reported,
            _now_iso(), extracted.page_count, int(extracted.used_ocr), status, anon.text,
        ),
    )
    report_id = cur.lastrowid
    result.report_id = report_id

    # 7. Normalize + store each result.
    n_results = 0
    n_review = 0
    for rr in extraction.results:
        marker_id, created = db.resolve_marker(conn, rr.marker)
        pv = parse_value(rr.value)
        pr = parse_reference_range(rr.ref_range)
        flag = compute_flag(pv.value_num, pr.low, pr.high, rr.flag)

        factor = db.conversion_factor(conn, marker_id, rr.unit)
        marker_row = conn.execute("SELECT canonical_unit FROM markers WHERE id = ?", (marker_id,)).fetchone()
        canonical_unit = marker_row["canonical_unit"] if marker_row else None
        value_canonical = pv.value_num * factor if (pv.value_num is not None and factor is not None) else None

        collected_at = parse_date(rr.collected_date) or fallback_date

        needs_review = 0
        if created:
            needs_review = 1  # unknown marker → confirm/merge in review queue
        if rr.source == "regex" or rr.confidence < 0.7:
            needs_review = 1
        if pv.value_num is None and pv.value_text and not _is_qualitative(pv.value_text):
            needs_review = 1
        if pv.value_num is not None and factor is None and (rr.unit or "").strip():
            needs_review = 1  # unit present but not convertible to canonical

        try:
            conn.execute(
                """INSERT INTO results
                   (report_id, marker_id, value_num, value_text, comparator, unit_raw,
                    value_canonical, unit_canonical, ref_low, ref_high, ref_text, flag,
                    collected_at, confidence, needs_review)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    report_id, marker_id, pv.value_num, pv.value_text, pv.comparator, rr.unit,
                    value_canonical, canonical_unit if value_canonical is not None else None,
                    pr.low, pr.high, pr.text or None, flag,
                    collected_at, rr.confidence, needs_review,
                ),
            )
        except sqlite3.IntegrityError:
            # Duplicate (marker, date, report, value) — already have it.
            continue
        n_results += 1
        n_review += needs_review

    if n_results == 0:
        status = "needs_review"
        result.warnings.append("No results were extracted; check the report or the LLM.")
    elif n_review > 0:
        status = "needs_review"
    conn.execute("UPDATE reports SET status = ? WHERE id = ?", (status, report_id))
    conn.commit()

    # 8. Handle the original file per privacy policy.
    _dispose_original(path, file_hash, settings, result)

    result.status = "ok"
    result.n_results = n_results
    result.n_review = n_review
    result.message = f"Imported {n_results} result(s)" + (f", {n_review} need review" if n_review else "")
    return result


def _is_qualitative(text: str) -> bool:
    return text.strip().lower() in (
        "negative", "positive", "reactive", "non-reactive", "nonreactive",
        "detected", "not detected", "none seen", "normal", "abnormal", "trace",
    )


def _dispose_original(path: Path, file_hash: str, settings: Settings, result: IngestReport) -> None:
    try:
        if settings.keep_originals:
            settings.originals_dir.mkdir(parents=True, exist_ok=True)
            dest = settings.originals_dir / f"{file_hash[:16]}_{path.name}"
            shutil.copy2(path, dest)
            result.warnings.append(f"Original archived to {dest}")
        # Note: we do NOT delete a file the user pointed us at directly from an
        # arbitrary location unless it lives in the watch inbox. Deleting is the
        # watcher's job; direct `ingest` leaves the user's file in place.
    except OSError as e:
        result.warnings.append(f"Could not archive original: {e}")


def ingest_paths(
    paths: list[Path], conn: sqlite3.Connection, settings: Settings, *, use_llm: bool = True
) -> list[IngestReport]:
    backend = None
    if use_llm and settings.llm_backend != "none":
        try:
            backend = make_backend(settings)
        except Exception:
            backend = None
    reports = []
    for p in paths:
        reports.append(ingest_pdf(p, conn, settings, backend, use_llm=use_llm))
    return reports
