"""
tests/test_config.py: Unit tests for config.py module.
Verifies target paper metadata, results schema definitions,
directory creation helpers, and GROQ_API_KEY environment lookup.
"""

import os
import pytest
from config import (
    BASE_DIR,
    DATA_DIR,
    RAW_PAPERS_DIR,
    EXTRACTED_DIR,
    RESULTS_CSV_COLUMNS,
    ALLOWED_SOURCES,
    ALLOWED_STATUSES,
    ALLOWED_ANSWER_GRADES,
    TARGET_PAPERS,
    ensure_directories,
    get_groq_api_key,
)


def test_target_papers_specification():
    """Verify that exactly 3 target papers are defined with expected versions and arXiv IDs."""
    assert len(TARGET_PAPERS) == 3
    expected_ids = {"1706.03762v7", "2410.00037v1", "2507.07955v2"}
    assert set(TARGET_PAPERS.keys()) == expected_ids

    # Attention Is All You Need
    attention = TARGET_PAPERS["1706.03762v7"]
    assert attention.arxiv_id == "1706.03762"
    assert attention.version == "v7"
    assert "Attention Is All You Need" in attention.title
    assert attention.short_tag == "attention"
    assert attention.filename == "1706.03762v7.pdf"
    assert attention.pdf_url == "https://arxiv.org/pdf/1706.03762v7.pdf"

    # Moshi
    moshi = TARGET_PAPERS["2410.00037v1"]
    assert moshi.arxiv_id == "2410.00037"
    assert moshi.version == "v1"
    assert "Moshi" in moshi.title
    assert moshi.short_tag == "moshi"
    assert moshi.filename == "2410.00037v1.pdf"
    assert moshi.pdf_url == "https://arxiv.org/pdf/2410.00037v1.pdf"

    # Dynamic Chunking
    dc = TARGET_PAPERS["2507.07955v2"]
    assert dc.arxiv_id == "2507.07955"
    assert dc.version == "v2"
    assert "Dynamic Chunking" in dc.title
    assert dc.short_tag == "dynamic_chunking"
    assert dc.filename == "2507.07955v2.pdf"
    assert dc.pdf_url == "https://arxiv.org/pdf/2507.07955v2.pdf"


def test_results_csv_columns():
    """Verify results.csv schema matches exact assignment specification."""
    expected_columns = [
        "request_id",
        "scenario",
        "source",
        "start_time",
        "first_token_ms",
        "total_ms",
        "status",
        "retries",
        "tokens_in",
        "tokens_out",
        "retrieval_setting",
        "answer_grade",
    ]
    assert RESULTS_CSV_COLUMNS == expected_columns
    assert set(ALLOWED_SOURCES) == {"real", "stand-in"}
    assert set(ALLOWED_STATUSES) == {"ok", "rate_limited", "timeout", "error"}
    assert set(ALLOWED_ANSWER_GRADES) == {"correct", "wrong", "refused_correctly", "refused_wrongly"}


def test_ensure_directories():
    """Ensure directory creation works without error."""
    ensure_directories()
    assert DATA_DIR.exists()
    assert RAW_PAPERS_DIR.exists()
    assert EXTRACTED_DIR.exists()


def test_get_groq_api_key_from_env(monkeypatch):
    """Verify API key is strictly read from environment variable."""
    # When missing
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert get_groq_api_key(required=False) is None
    with pytest.raises(ValueError, match="GROQ_API_KEY environment variable is not set"):
        get_groq_api_key(required=True)

    # When set
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key_dummy_value")
    assert get_groq_api_key() == "gsk_test_key_dummy_value"

