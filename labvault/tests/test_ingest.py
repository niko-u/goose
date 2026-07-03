from labvault import db
from labvault.ingest import ingest_pdf


def _conn(settings):
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    return conn


def test_full_ingest_with_mock_llm(settings, synthetic_pdf, mock_llm):
    conn = _conn(settings)
    result = ingest_pdf(synthetic_pdf, conn, settings, mock_llm)

    assert result.status == "ok"
    assert result.n_results == 9

    # PII must not be stored anywhere in redacted text.
    report = conn.execute("SELECT redacted_text FROM reports WHERE id = ?", (result.report_id,)).fetchone()
    stored = report["redacted_text"]
    for pii in ("88213947", "555-0199", "john.doe@example.com", "03/14/1980", "JOHN"):
        assert pii not in stored, f"PII leaked: {pii}"

    # Collection date parsed and applied.
    assert conn.execute("SELECT collected_date FROM reports").fetchone()[0] == "2024-01-15"

    # Glucose resolved to seeded marker with H flag.
    row = conn.execute(
        """SELECT r.value_num, r.flag, r.collected_at, m.canonical_name
           FROM results r JOIN markers m ON m.id = r.marker_id
           WHERE m.canonical_name = 'Glucose'"""
    ).fetchone()
    assert row["value_num"] == 105.0
    assert row["flag"] == "H"
    assert row["collected_at"] == "2024-01-15"


def test_alias_resolution(settings, synthetic_pdf, mock_llm):
    conn = _conn(settings)
    ingest_pdf(synthetic_pdf, conn, settings, mock_llm)
    # "HDL Cholesterol" and "Testosterone, Total" are aliases of seeded markers.
    hdl = conn.execute(
        "SELECT m.canonical_name FROM results r JOIN markers m ON m.id = r.marker_id WHERE r.value_num = 38"
    ).fetchone()
    assert hdl["canonical_name"] == "HDL Cholesterol"


def test_dedup_on_second_ingest(settings, synthetic_pdf, mock_llm):
    conn = _conn(settings)
    ingest_pdf(synthetic_pdf, conn, settings, mock_llm)
    second = ingest_pdf(synthetic_pdf, conn, settings, mock_llm)
    assert second.status == "skipped"
    assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1


def test_time_series_across_two_reports(settings, tmp_path, mock_llm):
    from tests.conftest import MOCK_EXTRACTION, make_pdf, SYNTHETIC_REPORT_TEXT
    import copy
    from labvault.llm import LLMBackend
    import json

    conn = _conn(settings)

    # First report: Jan.
    pdf1 = make_pdf(tmp_path / "jan.pdf", SYNTHETIC_REPORT_TEXT)
    ingest_pdf(pdf1, conn, settings, mock_llm)

    # Second report: different date + different glucose value.
    ext2 = copy.deepcopy(MOCK_EXTRACTION)
    ext2["collected_date"] = "04/20/2024"
    ext2["results"][0]["value"] = "92"
    ext2["results"][0]["flag"] = None

    class MockLLM2(LLMBackend):
        def chat(self, system, user, *, json_mode=False):
            if "de-identification" in system:
                return user
            if "extract laboratory" in system:
                return json.dumps(ext2)
            return "OK"

    pdf2 = make_pdf(tmp_path / "apr.pdf", SYNTHETIC_REPORT_TEXT.replace("01/15/2024", "04/20/2024"))
    ingest_pdf(pdf2, conn, settings, MockLLM2())

    marker_id = conn.execute("SELECT id FROM markers WHERE canonical_name = 'Glucose'").fetchone()[0]
    series = db.marker_series(conn, marker_id)
    assert len(series) == 2
    dates = sorted(r["collected_at"] for r in series)
    assert dates == ["2024-01-15", "2024-04-20"]


def test_unknown_marker_flagged_for_review(settings, tmp_path):
    import json
    from labvault.llm import LLMBackend
    from tests.conftest import make_pdf

    class WeirdLLM(LLMBackend):
        def chat(self, system, user, *, json_mode=False):
            if "de-identification" in system:
                return user
            if "extract laboratory" in system:
                return json.dumps({
                    "source_lab": "Acme", "collected_date": "03/01/2024", "reported_date": None,
                    "results": [{"marker": "Zorblaxinase", "value": "42", "unit": "U/L", "ref_range": "10-50", "flag": None}],
                })
            return "OK"

    conn = _conn(settings)
    pdf = make_pdf(tmp_path / "weird.pdf", "Acme Labs\nCollected: 03/01/2024\nZorblaxinase 42 U/L 10-50\n")
    result = ingest_pdf(pdf, conn, settings, WeirdLLM())
    review = conn.execute("SELECT needs_review FROM results").fetchone()
    assert review["needs_review"] == 1
    assert result.n_review == 1


def test_regex_only_fallback_flags_everything(settings, synthetic_pdf):
    conn = _conn(settings)
    # No backend -> regex fallback path.
    result = ingest_pdf(synthetic_pdf, conn, settings, backend=None, use_llm=False)
    assert result.n_results > 0
    # Everything from regex path is flagged for review.
    n_review = conn.execute("SELECT COUNT(*) FROM results WHERE needs_review = 1").fetchone()[0]
    assert n_review == result.n_results


def test_regex_only_extracts_collection_date(settings, synthetic_pdf):
    # Even without the LLM, the report's collection date should be parsed so the
    # time series lands on the right date (not the import date).
    conn = _conn(settings)
    ingest_pdf(synthetic_pdf, conn, settings, backend=None, use_llm=False)
    assert conn.execute("SELECT collected_date FROM reports").fetchone()[0] == "2024-01-15"
