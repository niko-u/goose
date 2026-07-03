from labvault.anonymize import REDACTION, anonymize, regex_scrub


def test_regex_scrub_removes_identifiers():
    text = (
        "Patient Name: DOE, JOHN A\n"
        "DOB: 03/14/1980 Age: 45\n"
        "MRN: 88213947  Accession: QD-2023-556677\n"
        "Phone: (415) 555-0199  john.doe@example.com\n"
        "123 Main Street, Springfield\n"
        "Collected: 01/15/2024\n"
        "Glucose 105 mg/dL 65-99\n"
    )
    scrubbed, count = regex_scrub(text)
    assert count > 0
    # Identifiers gone
    assert "88213947" not in scrubbed
    assert "555-0199" not in scrubbed
    assert "john.doe@example.com" not in scrubbed
    assert "03/14/1980" not in scrubbed
    assert "JOHN" not in scrubbed
    assert "Main Street" not in scrubbed
    assert REDACTION in scrubbed
    # Clinical data preserved
    assert "Glucose 105 mg/dL 65-99" in scrubbed
    assert "01/15/2024" in scrubbed  # collection date kept


def test_collection_date_preserved():
    text = "Collected: 02/20/2024\nReported: 02/22/2024\nGlucose 90 mg/dL"
    scrubbed, _ = regex_scrub(text)
    assert "02/20/2024" in scrubbed
    assert "02/22/2024" in scrubbed


def test_anonymize_without_llm_still_scrubs():
    text = "MRN: 12345678\nGlucose 100 mg/dL"
    result = anonymize(text, backend=None, use_llm=False)
    assert "12345678" not in result.text
    assert result.llm_used is False
    assert result.redaction_count >= 1
