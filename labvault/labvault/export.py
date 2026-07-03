"""CSV / JSON export of stored results."""

from __future__ import annotations

import csv
import json
import sqlite3
from typing import TextIO

_COLUMNS = [
    "marker", "category", "collected_at", "value_num", "value_text", "comparator",
    "unit_raw", "value_canonical", "unit_canonical", "ref_low", "ref_high", "ref_text",
    "flag", "confidence", "needs_review", "source_report", "source_lab",
]


def _rows(conn: sqlite3.Connection, marker_id: int | None) -> list[sqlite3.Row]:
    sql = """
        SELECT m.canonical_name AS marker, m.category AS category,
               r.collected_at, r.value_num, r.value_text, r.comparator,
               r.unit_raw, r.value_canonical, r.unit_canonical,
               r.ref_low, r.ref_high, r.ref_text, r.flag, r.confidence, r.needs_review,
               rep.original_filename AS source_report, rep.source_lab AS source_lab
        FROM results r
        JOIN markers m ON m.id = r.marker_id
        JOIN reports rep ON rep.id = r.report_id
    """
    params: tuple = ()
    if marker_id:
        sql += " WHERE r.marker_id = ?"
        params = (marker_id,)
    sql += " ORDER BY m.canonical_name, r.collected_at"
    return conn.execute(sql, params).fetchall()


def export_csv(conn: sqlite3.Connection, out: TextIO, marker_id: int | None = None) -> None:
    writer = csv.DictWriter(out, fieldnames=_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in _rows(conn, marker_id):
        writer.writerow({k: row[k] for k in _COLUMNS})


def export_json(conn: sqlite3.Connection, out: TextIO, marker_id: int | None = None) -> None:
    data = [{k: row[k] for k in _COLUMNS} for row in _rows(conn, marker_id)]
    json.dump({"results": data, "count": len(data)}, out, indent=2, default=str)
