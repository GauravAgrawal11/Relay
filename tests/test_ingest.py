"""
tests/test_ingest.py: Unit tests for ingest.py module.
Validates downloaded PDFs, SHA-256 digests, arXiv stamps,
and pages.jsonl extracted structure.
"""

import json
from pathlib import Path
import pytest
from config import (
    RAW_PAPERS_DIR,
    MANIFEST_PATH,
    PAGES_JSONL_PATH,
    TARGET_PAPERS,
)
from ingest import compute_sha256_and_size


def test_manifest_exists_and_valid():
    """Verify manifest.json is present, valid JSON, and has all 3 target papers."""
    assert MANIFEST_PATH.exists(), "manifest.json does not exist. Run ingest.py first."
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert "papers" in manifest
    assert len(manifest["papers"]) == 3
    for full_id, paper in TARGET_PAPERS.items():
        assert full_id in manifest["papers"]
        entry = manifest["papers"][full_id]
        assert entry["arxiv_stamp_verified"] is True
        assert len(entry["sha256"]) == 64
        assert entry["size_bytes"] > 0
        assert entry["page_count"] > 0


def test_raw_pdfs_match_manifest():
    """Verify raw PDF files exist on disk and SHA-256 matches manifest."""
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    for full_id, paper in TARGET_PAPERS.items():
        pdf_path = RAW_PAPERS_DIR / paper.filename
        assert pdf_path.exists(), f"PDF missing: {pdf_path}"
        sha256_hash, size_bytes = compute_sha256_and_size(pdf_path)
        assert sha256_hash == manifest["papers"][full_id]["sha256"]
        assert size_bytes == manifest["papers"][full_id]["size_bytes"]


def test_pages_jsonl_completeness():
    """Verify pages.jsonl contains all pages matching manifest page counts."""
    assert PAGES_JSONL_PATH.exists(), "pages.jsonl does not exist."
    records = []
    with open(PAGES_JSONL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    expected_total_pages = manifest["total_pages_all_papers"]
    assert len(records) == expected_total_pages

    # Check each paper's pages are sequentially indexed 1..N
    for full_id in TARGET_PAPERS.keys():
        paper_pages = [r for r in records if r["paper_id"] == full_id]
        assert len(paper_pages) == manifest["papers"][full_id]["page_count"]
        page_numbers = [r["page_number"] for r in paper_pages]
        assert page_numbers == list(range(1, len(paper_pages) + 1))
        # Ensure text is non-empty
        assert any(len(r["text"]) > 100 for r in paper_pages)

