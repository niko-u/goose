"""SQLite schema, migrations, and query helpers.

Single-file database. Every result row links a report to a canonical marker,
so a marker's history over time is a simple indexed query. Marker name
variants ("HDL", "HDL Cholesterol", "HDL-C") map to one marker via the
alias table.
"""

from __future__ import annotations

import json
import re
import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    file_hash TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    source_lab TEXT,
    collected_date TEXT,          -- ISO date; report-level default for results
    reported_date TEXT,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    page_count INTEGER NOT NULL DEFAULT 0,
    used_ocr INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',   -- ok | needs_review | failed
    redacted_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS markers (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'Other',
    canonical_unit TEXT,
    description TEXT,
    is_seeded INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS marker_aliases (
    alias TEXT NOT NULL UNIQUE COLLATE NOCASE,
    marker_id INTEGER NOT NULL REFERENCES markers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS unit_conversions (
    marker_id INTEGER NOT NULL REFERENCES markers(id) ON DELETE CASCADE,
    from_unit TEXT NOT NULL COLLATE NOCASE,
    factor REAL NOT NULL,          -- value_in_from_unit * factor = value in canonical_unit
    PRIMARY KEY (marker_id, from_unit)
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    marker_id INTEGER NOT NULL REFERENCES markers(id) ON DELETE CASCADE,
    value_num REAL,               -- numeric part if parseable
    value_text TEXT,              -- verbatim value ("Negative", "<0.01", "1:40")
    comparator TEXT,              -- '<' | '>' | '<=' | '>=' | NULL
    unit_raw TEXT,
    value_canonical REAL,         -- value_num converted to marker's canonical unit
    unit_canonical TEXT,
    ref_low REAL,
    ref_high REAL,
    ref_text TEXT,
    flag TEXT,                    -- H | L | A | NULL
    collected_at TEXT NOT NULL,   -- ISO date; falls back to report date or import date
    confidence REAL NOT NULL DEFAULT 1.0,
    needs_review INTEGER NOT NULL DEFAULT 0,
    reviewed_at TEXT,
    notes TEXT,
    UNIQUE (marker_id, collected_at, report_id, value_text)
);

CREATE INDEX IF NOT EXISTS idx_results_marker_time ON results(marker_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_results_report ON results(report_id);
CREATE INDEX IF NOT EXISTS idx_results_review ON results(needs_review) WHERE needs_review = 1;
"""

_norm_re = re.compile(r"[^a-z0-9%]+")


def normalize_alias(name: str) -> str:
    """Fold a marker name to a lookup key: lowercase, strip punctuation/spacing."""
    return _norm_re.sub(" ", name.lower()).strip()


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.executescript(SCHEMA)
    if version < 1:
        seed_markers(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def seed_markers(conn: sqlite3.Connection) -> None:
    data = json.loads(resources.files("labvault.data").joinpath("markers_seed.json").read_text())
    for m in data["markers"]:
        cur = conn.execute(
            "INSERT OR IGNORE INTO markers (canonical_name, category, canonical_unit, description, is_seeded)"
            " VALUES (?, ?, ?, ?, 1)",
            (m["name"], m.get("category", "Other"), m.get("unit"), m.get("description")),
        )
        if cur.rowcount:
            marker_id = cur.lastrowid
        else:
            marker_id = conn.execute(
                "SELECT id FROM markers WHERE canonical_name = ?", (m["name"],)
            ).fetchone()[0]
        aliases = set(m.get("aliases", [])) | {m["name"]}
        for alias in aliases:
            conn.execute(
                "INSERT OR IGNORE INTO marker_aliases (alias, marker_id) VALUES (?, ?)",
                (normalize_alias(alias), marker_id),
            )
        for unit, factor in m.get("conversions", {}).items():
            conn.execute(
                "INSERT OR IGNORE INTO unit_conversions (marker_id, from_unit, factor) VALUES (?, ?, ?)",
                (marker_id, unit, factor),
            )


# --- marker resolution -------------------------------------------------------

def resolve_marker(conn: sqlite3.Connection, raw_name: str) -> tuple[int, bool]:
    """Return (marker_id, created). Unknown names create a new marker so no
    data is dropped; the caller should flag those results for review."""
    key = normalize_alias(raw_name)
    row = conn.execute("SELECT marker_id FROM marker_aliases WHERE alias = ?", (key,)).fetchone()
    if row:
        return row["marker_id"], False
    display = " ".join(w if w.isupper() else w.capitalize() for w in raw_name.strip().split())
    cur = conn.execute(
        "INSERT OR IGNORE INTO markers (canonical_name, category) VALUES (?, 'Uncategorized')",
        (display,),
    )
    if cur.rowcount:
        marker_id = cur.lastrowid
    else:
        marker_id = conn.execute(
            "SELECT id FROM markers WHERE canonical_name = ?", (display,)
        ).fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO marker_aliases (alias, marker_id) VALUES (?, ?)", (key, marker_id))
    return marker_id, True


def conversion_factor(conn: sqlite3.Connection, marker_id: int, from_unit: str | None) -> float | None:
    """Factor to canonical unit, 1.0 if already canonical, None if unknown."""
    marker = conn.execute("SELECT canonical_unit FROM markers WHERE id = ?", (marker_id,)).fetchone()
    if marker is None:
        return None
    canonical = (marker["canonical_unit"] or "").strip()
    unit = (from_unit or "").strip()
    if not canonical:
        return 1.0
    if unit.lower() == canonical.lower() or not unit:
        return 1.0
    row = conn.execute(
        "SELECT factor FROM unit_conversions WHERE marker_id = ? AND from_unit = ?",
        (marker_id, unit),
    ).fetchone()
    return row["factor"] if row else None


# --- queries used by web/CLI -------------------------------------------------

def marker_series(conn: sqlite3.Connection, marker_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.*, rep.original_filename, rep.source_lab
           FROM results r JOIN reports rep ON rep.id = r.report_id
           WHERE r.marker_id = ? ORDER BY r.collected_at, r.id""",
        (marker_id,),
    ).fetchall()


def markers_overview(conn: sqlite3.Connection, query: str | None = None) -> list[sqlite3.Row]:
    """Every marker that has data, with its latest value and range status."""
    sql = """
        SELECT m.id, m.canonical_name, m.category, m.canonical_unit,
               COUNT(r.id) AS n_results,
               MAX(r.collected_at) AS last_date,
               (SELECT value_canonical FROM results WHERE marker_id = m.id ORDER BY collected_at DESC, id DESC LIMIT 1) AS last_value,
               (SELECT value_text FROM results WHERE marker_id = m.id ORDER BY collected_at DESC, id DESC LIMIT 1) AS last_value_text,
               (SELECT flag FROM results WHERE marker_id = m.id ORDER BY collected_at DESC, id DESC LIMIT 1) AS last_flag,
               (SELECT ref_low FROM results WHERE marker_id = m.id ORDER BY collected_at DESC, id DESC LIMIT 1) AS last_ref_low,
               (SELECT ref_high FROM results WHERE marker_id = m.id ORDER BY collected_at DESC, id DESC LIMIT 1) AS last_ref_high
        FROM markers m JOIN results r ON r.marker_id = m.id
    """
    params: tuple = ()
    if query:
        sql += " WHERE m.canonical_name LIKE ? OR m.category LIKE ?"
        params = (f"%{query}%", f"%{query}%")
    sql += " GROUP BY m.id ORDER BY m.category, m.canonical_name"
    return conn.execute(sql, params).fetchall()


def summary(conn: sqlite3.Connection) -> dict:
    n_reports = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    n_results = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    n_markers = conn.execute("SELECT COUNT(DISTINCT marker_id) FROM results").fetchone()[0]
    n_review = conn.execute("SELECT COUNT(*) FROM results WHERE needs_review = 1").fetchone()[0]
    out_of_range = conn.execute(
        """SELECT COUNT(*) FROM (
             SELECT marker_id, flag,
                    ROW_NUMBER() OVER (PARTITION BY marker_id ORDER BY collected_at DESC, id DESC) AS rn
             FROM results) WHERE rn = 1 AND flag IN ('H','L','A')"""
    ).fetchone()[0]
    last = conn.execute("SELECT MAX(collected_at) FROM results").fetchone()[0]
    return {
        "reports": n_reports,
        "results": n_results,
        "markers": n_markers,
        "needs_review": n_review,
        "latest_out_of_range": out_of_range,
        "last_collected": last,
    }
