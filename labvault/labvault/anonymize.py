"""PII scrubbing.

Two passes:
  1. Deterministic regex pass — removes the identifiers that appear in a
     predictable shape (SSN, MRN/accession, phone, email, DOB, dates of
     collection are KEPT because we need them, patient/provider name headers).
  2. Optional local-LLM pass — asks Gemma to redact any remaining personal
     identifiers it recognizes in free text. Runs entirely on the local model.

Only redacted text is ever persisted. The regex pass alone is enough to be
safe for storage; the LLM pass is defense-in-depth for oddly formatted headers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .llm import LLMBackend, LLMUnavailable

REDACTION = "[REDACTED]"

# Order matters: more specific patterns first.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)")),
    # MRN / accession / specimen / patient ID: label followed by an alphanumeric id.
    ("id_labeled", re.compile(
        r"\b(MRN|Medical Record(?: (?:No|Number|#))?|Accession(?: (?:No|Number|#))?|"
        r"Specimen(?: (?:ID|No|Number|#))?|Patient(?: (?:ID|No|Number|#))|"
        r"Requisition(?: (?:No|Number|#))?|Order(?: (?:No|Number|#))?|Control(?: (?:No|Number|#))?|"
        r"Chart(?: (?:No|Number|#))?|Account(?: (?:No|Number|#))?)"
        r"\s*[:#]?\s*([A-Za-z0-9-]{4,})",
        re.IGNORECASE,
    )),
    # DOB / Date of Birth. Collection/report dates are handled separately and kept.
    ("dob", re.compile(
        r"\b(DOB|Date of Birth|Birth Date|Birthdate)\s*[:#]?\s*"
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}|"
        r"[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE,
    )),
    # Age: "Age: 47" or "47 Y" — mildly identifying; redact the number.
    ("age", re.compile(r"\b(Age)\s*[:#]?\s*(\d{1,3})\b", re.IGNORECASE)),
    # Name headers: "Patient Name: John Q Public", "Name: DOE, JANE A"
    ("name_labeled", re.compile(
        r"\b(Patient(?:\s+Name)?|Name|Ordering (?:Physician|Provider)|Physician|Provider|Referring)"
        r"\s*[:#]\s*([A-Z][A-Za-z'\-]+(?:,?\s+[A-Z][A-Za-z'\-.]*){0,3})",
    )),
    # Street address line.
    ("address", re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9.\s]{2,40}\s"
        r"(Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl|Terrace|Ter)\b\.?",
        re.IGNORECASE,
    )),
    # ZIP (US 5 or 5-4). Kept conservative to avoid nuking result values.
    ("zip", re.compile(r"\b\d{5}-\d{4}\b")),
]

# Labels that introduce dates we WANT to keep (collection/report dates).
_KEEP_DATE_LABELS = re.compile(
    r"\b(Collect|Collected|Collection|Drawn|Report(?:ed)?|Received|Result(?:ed)?|Specimen Date|Test Date)\b",
    re.IGNORECASE,
)


@dataclass
class AnonymizationResult:
    text: str
    redaction_count: int
    llm_used: bool


def regex_scrub(text: str) -> tuple[str, int]:
    count = 0

    def _sub_labeled(m: re.Match) -> str:
        nonlocal count
        count += 1
        # Keep the label, redact the value (group 2 or last group).
        return f"{m.group(1)}: {REDACTION}"

    def _sub_plain(m: re.Match) -> str:
        nonlocal count
        count += 1
        return REDACTION

    for name, pattern in _PATTERNS:
        if name in ("id_labeled", "dob", "age", "name_labeled"):
            text = pattern.sub(_sub_labeled, text)
        else:
            text = pattern.sub(_sub_plain, text)
    return text, count


def _llm_scrub(text: str, backend: LLMBackend) -> str:
    system = (
        "You are a medical data de-identification tool. You will be given text from a "
        "lab report. Return the SAME text verbatim, except replace any remaining personal "
        "identifiers with [REDACTED]. Redact: patient names, provider/doctor names, "
        "addresses, phone/fax numbers, email, medical record numbers, account/accession "
        "numbers, dates of birth, and ages. DO NOT redact: test/marker names, numeric "
        "results, units, reference ranges, and specimen collection or report dates. "
        "Do not summarize, reorder, or add commentary. Output only the redacted text."
    )
    return backend.chat(system, text)


def anonymize(text: str, backend: LLMBackend | None = None, *, use_llm: bool = True) -> AnonymizationResult:
    scrubbed, count = regex_scrub(text)
    llm_used = False
    if use_llm and backend is not None:
        try:
            # Chunk to keep prompts small for local models; ~6k chars per chunk.
            chunks = _chunk(scrubbed, 6000)
            out = []
            for ch in chunks:
                out.append(_llm_scrub(ch, backend))
            candidate = "\n".join(out)
            # Guard: only accept the LLM output if it didn't collapse the text
            # (small models sometimes truncate). Require >=60% of original length.
            if len(candidate) >= 0.6 * len(scrubbed):
                scrubbed = candidate
                llm_used = True
        except LLMUnavailable:
            pass
        except Exception:
            pass
    return AnonymizationResult(text=scrubbed, redaction_count=count, llm_used=llm_used)


def _chunk(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks, cur, cur_len = [], [], 0
    for line in text.splitlines(keepends=True):
        if cur_len + len(line) > size and cur:
            chunks.append("".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line)
    if cur:
        chunks.append("".join(cur))
    return chunks
