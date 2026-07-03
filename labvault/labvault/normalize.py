"""Value parsing and normalization.

Turns messy result strings into structured fields:
  "<0.01"      -> comparator '<', value_num 0.01
  "1,234"      -> value_num 1234
  "Negative"   -> value_text 'Negative', value_num None
  "12.3 H"     -> value_num 12.3, flag 'H'
  "3.5-5.1"    -> ref_low 3.5, ref_high 5.1  (reference-range parsing)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

_COMPARATOR_RE = re.compile(r"^\s*(<=|>=|<|>)\s*")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_TRAILING_FLAG_RE = re.compile(r"\b(H|L|A|HH|LL|CRITICAL|HIGH|LOW|ABNORMAL)\b\s*$", re.IGNORECASE)

_FLAG_MAP = {
    "H": "H", "HH": "H", "HIGH": "H",
    "L": "L", "LL": "L", "LOW": "L",
    "A": "A", "ABNORMAL": "A", "CRITICAL": "A",
}


@dataclass
class ParsedValue:
    value_num: float | None
    value_text: str
    comparator: str | None
    flag: str | None


def parse_value(raw: str) -> ParsedValue:
    text = (raw or "").strip()
    if not text:
        return ParsedValue(None, "", None, None)

    flag = None
    m = _TRAILING_FLAG_RE.search(text)
    if m:
        flag = _FLAG_MAP.get(m.group(1).upper())
        text_wo_flag = text[: m.start()].strip()
    else:
        text_wo_flag = text

    comparator = None
    cm = _COMPARATOR_RE.match(text_wo_flag)
    body = text_wo_flag
    if cm:
        comparator = cm.group(1)
        body = text_wo_flag[cm.end():].strip()

    # Ratios like "1:40" or "A/G 1.8" — keep as text but still try a number.
    cleaned = body.replace(",", "")
    num = None
    # Only treat as numeric if the body is essentially just a number.
    if re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        num = float(cleaned)

    return ParsedValue(value_num=num, value_text=text, comparator=comparator, flag=flag)


@dataclass
class ParsedRange:
    low: float | None
    high: float | None
    text: str


def parse_reference_range(raw: str | None) -> ParsedRange:
    if not raw:
        return ParsedRange(None, None, "")
    text = raw.strip()
    cleaned = text.replace(",", "")

    # "3.5 - 5.1"  or "3.5–5.1"
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*[-–—]\s*(-?\d+(?:\.\d+)?)", cleaned)
    if m:
        return ParsedRange(float(m.group(1)), float(m.group(2)), text)
    # "< 200" / "<=200"
    m = re.search(r"(?:<=|<)\s*(\d+(?:\.\d+)?)", cleaned)
    if m:
        return ParsedRange(None, float(m.group(1)), text)
    # "> 40" / ">=40"
    m = re.search(r"(?:>=|>)\s*(\d+(?:\.\d+)?)", cleaned)
    if m:
        return ParsedRange(float(m.group(1)), None, text)
    return ParsedRange(None, None, text)


def compute_flag(value: float | None, low: float | None, high: float | None, existing: str | None) -> str | None:
    """Prefer an explicit flag from the report; otherwise derive from range."""
    if existing:
        return existing
    if value is None:
        return None
    if high is not None and value > high:
        return "H"
    if low is not None and value < low:
        return "L"
    return None


_DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%d/%m/%Y",
    "%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%d-%b-%Y", "%Y/%m/%d",
    "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
]


def parse_date(raw: str | None) -> str | None:
    """Normalize a date string to ISO 'YYYY-MM-DD', or None."""
    if not raw:
        return None
    s = raw.strip()
    # Trim a trailing time if present but keep date.
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.date().isoformat()
        except ValueError:
            continue
    # Loose fallback: pull the first mm/dd/yyyy or yyyy-mm-dd token.
    m = re.search(r"\b(\d{4}-\d{1,2}-\d{1,2})\b", s)
    if m:
        try:
            return date.fromisoformat(m.group(1)).isoformat()
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", s)
    if m:
        mm, dd, yy = m.groups()
        yy = ("20" + yy) if len(yy) == 2 else yy
        try:
            return date(int(yy), int(mm), int(dd)).isoformat()
        except ValueError:
            pass
    return None
