"""
tests/test_chunking.py: Unit tests for chunking.py module.
Validates chunk construction, section detection, citation tag formatting,
and multi-setting configuration properties.
"""

import pytest
from chunking import (
    ChunkConfig,
    Passage,
    SETTING_A,
    SETTING_B,
    SETTING_C,
    RETRIEVAL_SETTINGS,
    detect_section_in_block,
    make_citation_tag,
    build_passages,
)
from config import TARGET_PAPERS


def test_chunk_configs():
    """Verify that three distinct comparison settings are available."""
    assert len(RETRIEVAL_SETTINGS) >= 3
    assert SETTING_A.chunk_size_words < SETTING_B.chunk_size_words < SETTING_C.chunk_size_words
    assert SETTING_A.overlap_words < SETTING_B.overlap_words < SETTING_C.overlap_words


def test_citation_tag_formatting():
    """Verify citation tag construction with section and with fallback."""
    # With section
    tag_with_sec = make_citation_tag("attention", "v7", 4, "s3.2.1")
    assert tag_with_sec == "[attention v7 s3.2.1 p.4]"

    # Fallback without section
    tag_fallback = make_citation_tag("attention", "v7", 4, None)
    assert tag_fallback == "[attention v7 p.4]"


def test_section_detection():
    """Verify heading recognition and filtering of false positives."""
    # True headings
    sec_id, sec_title = detect_section_in_block("3.2.1 Scaled Dot-Product Attention\nSome formula...")
    assert sec_id == "s3.2.1"
    assert sec_title == "Scaled Dot-Product Attention"

    # Unnumbered sections like Abstract return None so citation falls back to clean page-only tag (e.g. [paper v1 p.3])
    sec_id2, sec_title2 = detect_section_in_block("Abstract\nWe present...")
    assert sec_id2 is None
    assert sec_title2 == "Abstract"

    sec_id3, sec_title3 = detect_section_in_block("1 Introduction\nIn recent years...")
    assert sec_id3 == "s1"
    assert sec_title3 == "Introduction"

    # False positives: measurement units should not be detected as sections
    sec_id_fp, _ = detect_section_in_block("12.5 Hz frame rate")
    assert sec_id_fp is None


def test_build_passages_all_settings():
    """Verify passage generation across all target papers for all 3 settings."""
    for setting_name, config in RETRIEVAL_SETTINGS.items():
        passages = build_passages(config)
        assert len(passages) > 50, f"Expected > 50 passages for {setting_name}"

        # Ensure all three papers are represented
        paper_ids_in_passages = {p.paper_id for p in passages}
        assert paper_ids_in_passages == set(TARGET_PAPERS.keys())

        # Check passage schema validity
        for p in passages[:10]:
            assert p.passage_id.startswith(p.short_tag)
            assert p.page >= 1
            assert p.word_count > 0
            assert len(p.text) > 0
            assert p.tag.startswith(f"[{p.short_tag} {p.version}")
            assert p.tag.endswith(f"p.{p.page}]")

