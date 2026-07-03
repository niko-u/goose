"""Lab-value extraction from redacted report text.

Primary path: ask the local LLM for strict JSON of every result it can find.
Fallback path: a deterministic line/table parser for common Quest/LabCorp-style
layouts (marker  value  unit  reference-range  flag). Both produce the same
RawResult shape; results that only came from the fallback (or failed LLM
validation) are marked low-confidence so they land in the review queue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm import LLMBackend, LLMUnavailable, extract_json

MAX_CHUNK_CHARS = 7000


@dataclass
class RawResult:
    marker: str
    value: str
    unit: str | None = None
    ref_range: str | None = None
    flag: str | None = None
    collected_date: str | None = None
    confidence: float = 1.0
    source: str = "llm"  # "llm" | "regex"


@dataclass
class ExtractionOutput:
    results: list[RawResult] = field(default_factory=list)
    source_lab: str | None = None
    collected_date: str | None = None
    reported_date: str | None = None
    llm_used: bool = False


_SYSTEM = (
    "You extract laboratory test results from de-identified lab report text. "
    "Return ONLY a JSON object with this exact shape:\n"
    '{"source_lab": string|null, "collected_date": string|null, '
    '"reported_date": string|null, "results": [{"marker": string, "value": string, '
    '"unit": string|null, "ref_range": string|null, "flag": string|null, '
    '"collected_date": string|null}]}\n'
    "Rules: include EVERY numeric or qualitative test result (e.g. Negative/Positive). "
    "Copy values, units, and reference ranges verbatim as strings. 'flag' is H, L, or A "
    "if the report marks the value high/low/abnormal, else null. Put a result-specific "
    "collection date in the result's collected_date only if it differs from the top-level "
    "one. Do NOT invent values. Do NOT include patient identifiers. Output only the JSON."
)


def _chunk_text(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    chunks, cur, cur_len = [], [], 0
    for line in text.splitlines(keepends=True):
        if cur_len + len(line) > MAX_CHUNK_CHARS and cur:
            chunks.append("".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line)
    if cur:
        chunks.append("".join(cur))
    return chunks


def _validate_result(obj: dict) -> RawResult | None:
    marker = (obj.get("marker") or "").strip()
    value = obj.get("value")
    if value is not None and not isinstance(value, str):
        value = str(value)
    value = (value or "").strip()
    if not marker or not value:
        return None
    if marker.lower() in ("marker", "test", "test name", "component"):
        return None
    return RawResult(
        marker=marker,
        value=value,
        unit=(obj.get("unit") or None),
        ref_range=(obj.get("ref_range") or obj.get("reference_range") or None),
        flag=(obj.get("flag") or None),
        collected_date=(obj.get("collected_date") or None),
        confidence=0.9,
        source="llm",
    )


def extract_with_llm(text: str, backend: LLMBackend) -> ExtractionOutput:
    out = ExtractionOutput(llm_used=True)
    seen: set[tuple] = set()
    for chunk in _chunk_text(text):
        try:
            reply = backend.chat(_SYSTEM, chunk, json_mode=True)
            data = extract_json(reply)
        except (LLMUnavailable, ValueError):
            # Retry once with a repair nudge before giving up on this chunk.
            try:
                reply = backend.chat(_SYSTEM, chunk + "\n\nReturn ONLY valid JSON.", json_mode=True)
                data = extract_json(reply)
            except Exception:
                continue
        if isinstance(data, list):
            data = {"results": data}
        if not isinstance(data, dict):
            continue
        out.source_lab = out.source_lab or (data.get("source_lab") or None)
        out.collected_date = out.collected_date or (data.get("collected_date") or None)
        out.reported_date = out.reported_date or (data.get("reported_date") or None)
        for obj in data.get("results", []) or []:
            if not isinstance(obj, dict):
                continue
            rr = _validate_result(obj)
            if rr is None:
                continue
            key = (rr.marker.lower(), rr.value, rr.collected_date)
            if key in seen:
                continue
            seen.add(key)
            out.results.append(rr)
    return out


# --- Deterministic fallback --------------------------------------------------

# Matches lines like:
#   Glucose            98      mg/dL     65-99
#   HDL Cholesterol    52  H   mg/dL     >40
#   TSH                2.10    uIU/mL    0.450-4.500
_LINE_RE = re.compile(
    r"^(?P<marker>[A-Za-z][A-Za-z0-9()/%,.\-'\s]{1,45}?)\s{2,}"
    r"(?P<value>(?:<=|>=|<|>)?\s*-?\d[\d,.]*|Negative|Positive|Reactive|Non-Reactive|Detected|Not Detected|None Seen|Normal|Abnormal)"
    r"(?:\s+(?P<flag>H|L|A|HH|LL))?"
    r"(?:\s{2,}(?P<unit>[A-Za-z%µ/^0-9.\-]+(?:/[A-Za-z0-9.\-^]+)?))?"
    r"(?:\s{2,}(?P<ref>[<>]?=?\s*-?\d[\d,.]*(?:\s*[-–]\s*-?\d[\d,.]*)?|[<>]=?\s*\d[\d,.]*))?"
    r"\s*$",
    re.IGNORECASE,
)

_COLLECTED_RE = re.compile(
    r"\b(?:Collected|Collection(?:\s+Date)?|Date\s+Collected|Drawn|Specimen\s+Date)\b\s*[:#]?\s*"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)
_REPORTED_RE = re.compile(
    r"\b(?:Reported|Report(?:ed)?\s+Date|Date\s+Reported|Resulted|Result\s+Date)\b\s*[:#]?\s*"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)

_SKIP_MARKERS = re.compile(
    r"^(page|patient|name|dob|age|sex|gender|specimen|collected|reported|received|"
    r"ordering|physician|provider|account|fasting|final|status|comment|note|reference|"
    r"result|units|flag|test)\b",
    re.IGNORECASE,
)


def extract_with_regex(text: str) -> ExtractionOutput:
    out = ExtractionOutput(llm_used=False)
    cm = _COLLECTED_RE.search(text)
    if cm:
        out.collected_date = cm.group(1)
    rm = _REPORTED_RE.search(text)
    if rm:
        out.reported_date = rm.group(1)
    seen: set[tuple] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        m = _LINE_RE.match(line.rstrip())
        if not m:
            continue
        marker = m.group("marker").strip()
        if _SKIP_MARKERS.match(marker) or len(marker) < 2:
            continue
        value = m.group("value").strip()
        key = (marker.lower(), value)
        if key in seen:
            continue
        seen.add(key)
        out.results.append(
            RawResult(
                marker=marker,
                value=value,
                unit=(m.group("unit") or None),
                ref_range=(m.group("ref") or None),
                flag=(m.group("flag") or None),
                confidence=0.5,
                source="regex",
            )
        )
    return out


def extract_results(text: str, backend: LLMBackend | None) -> ExtractionOutput:
    """LLM-first with regex fallback/supplement.

    If the LLM yields nothing (down or unhelpful), fall back to regex and mark
    everything low-confidence. If the LLM worked, still run regex to catch rows
    it may have missed, merging any markers not already seen.
    """
    llm_out = ExtractionOutput()
    if backend is not None:
        try:
            llm_out = extract_with_llm(text, backend)
        except LLMUnavailable:
            llm_out = ExtractionOutput()

    if llm_out.results:
        # Supplement with regex ONLY for markers the LLM didn't report at all.
        # Keying on marker name (not value) avoids adding a conflicting row when
        # the LLM's transcribed value differs from the raw text layout.
        regex_out = extract_with_regex(text)
        have_markers = {r.marker.lower() for r in llm_out.results}
        for rr in regex_out.results:
            if rr.marker.lower() not in have_markers:
                llm_out.results.append(rr)
                have_markers.add(rr.marker.lower())
        # Backfill report dates if the LLM missed them.
        llm_out.collected_date = llm_out.collected_date or regex_out.collected_date
        llm_out.reported_date = llm_out.reported_date or regex_out.reported_date
        return llm_out

    # No LLM results — pure regex fallback.
    regex_out = extract_with_regex(text)
    return regex_out
