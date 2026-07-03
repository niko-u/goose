"""Shared fixtures: synthetic lab PDFs and a mock LLM backend.

No real medical data is ever used. PDFs are generated at test time with
PyMuPDF so the repo stays clean.
"""

from __future__ import annotations

import os
from pathlib import Path

import fitz
import pytest

from labvault.config import Settings
from labvault.llm import LLMBackend


SYNTHETIC_REPORT_TEXT = """\
QUEST DIAGNOSTICS
Patient Name: DOE, JOHN A
DOB: 03/14/1980   Age: 45   Sex: M
MRN: 88213947   Accession: QD-2023-556677
Phone: (415) 555-0199   john.doe@example.com
123 Main Street, Springfield

Collected: 01/15/2024    Reported: 01/17/2024

TEST                     RESULT      FLAG    UNITS       REFERENCE RANGE
Glucose                  105         H       mg/dL       65-99
Hemoglobin A1c           5.9                 %           4.8-5.6
Total Cholesterol        212         H       mg/dL       125-200
HDL Cholesterol          38          L       mg/dL       >40
LDL Cholesterol          145         H       mg/dL       0-99
Triglycerides            180                 mg/dL       0-149
TSH                      2.10                uIU/mL      0.450-4.500
Vitamin D, 25-Hydroxy    22          L       ng/mL       30-100
Testosterone, Total      620                 ng/dL       250-1100
"""


def make_pdf(path: Path, text: str) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((36, 40), text, fontsize=9, fontname="courier")
    doc.save(str(path))
    doc.close()
    return path


# LLM's structured extraction of the synthetic report.
MOCK_EXTRACTION = {
    "source_lab": "Quest Diagnostics",
    "collected_date": "01/15/2024",
    "reported_date": "01/17/2024",
    "results": [
        {"marker": "Glucose", "value": "105", "unit": "mg/dL", "ref_range": "65-99", "flag": "H"},
        {"marker": "Hemoglobin A1c", "value": "5.9", "unit": "%", "ref_range": "4.8-5.6", "flag": None},
        {"marker": "Total Cholesterol", "value": "212", "unit": "mg/dL", "ref_range": "125-200", "flag": "H"},
        {"marker": "HDL Cholesterol", "value": "38", "unit": "mg/dL", "ref_range": ">40", "flag": "L"},
        {"marker": "LDL Cholesterol", "value": "145", "unit": "mg/dL", "ref_range": "0-99", "flag": "H"},
        {"marker": "Triglycerides", "value": "180", "unit": "mg/dL", "ref_range": "0-149", "flag": None},
        {"marker": "TSH", "value": "2.10", "unit": "uIU/mL", "ref_range": "0.450-4.500", "flag": None},
        {"marker": "Vitamin D, 25-Hydroxy", "value": "22", "unit": "ng/mL", "ref_range": "30-100", "flag": "L"},
        {"marker": "Testosterone, Total", "value": "620", "unit": "ng/dL", "ref_range": "250-1100", "flag": None},
    ],
}


class MockLLM(LLMBackend):
    """Returns canned extraction JSON and echoes text for redaction."""

    def __init__(self, extraction: dict | None = None):
        import json
        self.extraction_json = json.dumps(extraction or MOCK_EXTRACTION)

    def chat(self, system: str, user: str, *, json_mode: bool = False) -> str:
        if "de-identification" in system:
            # Simulate the LLM redaction pass: just return input unchanged
            # (the regex pass already handled the identifiers).
            return user
        if "extract laboratory test results" in system:
            return self.extraction_json
        return "OK"

    def available(self) -> bool:
        return True


@pytest.fixture
def settings(tmp_path) -> Settings:
    os.environ["LABVAULT_DATA_DIR"] = str(tmp_path)
    os.environ["LABVAULT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["LABVAULT_LLM_BACKEND"] = "none"
    s = Settings()
    s.ensure_dirs()
    return s


@pytest.fixture
def mock_llm() -> MockLLM:
    return MockLLM()


@pytest.fixture
def synthetic_pdf(tmp_path) -> Path:
    return make_pdf(tmp_path / "synthetic_report.pdf", SYNTHETIC_REPORT_TEXT)
