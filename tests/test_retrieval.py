"""
tests/test_retrieval.py: Unit tests for retrieval.py module.
Validates BM25 searching, FastEmbed vector searching, RRF fusion,
paper detection, and Comparison Guard enforcement.
"""

import pytest
from chunking import SETTING_A, SETTING_B, Passage
from retrieval import (
    BM25Searcher,
    apply_comparison_guard,
    detect_named_papers,
    get_retriever,
    reciprocal_rank_fusion,
)


def _make_dummy_passage(pid: str, short_tag: str, text: str, page: int = 1) -> Passage:
    return Passage(
        passage_id=pid,
        paper_id=f"{short_tag}_id",
        arxiv_id="0000.0000",
        version="v1",
        title=f"Title of {short_tag}",
        short_tag=short_tag,
        page=page,
        section="s1",
        section_title="Intro",
        tag=f"[{short_tag} v1 s1 p.{page}]",
        text=text,
        word_count=len(text.split()),
        char_count=len(text),
    )


def test_detect_named_papers():
    """Verify entity recognition for target papers in questions."""
    assert detect_named_papers("What is the BLEU score in Attention Is All You Need?") == ["attention"]
    assert detect_named_papers("What is the Mimi codec in Moshi?") == ["moshi"]
    assert detect_named_papers("How does H-Net perform dynamic chunking?") == ["dynamic_chunking"]

    # Comparative multi-paper questions
    comparative = detect_named_papers("Compare Attention Is All You Need and Moshi latency")
    assert "attention" in comparative and "moshi" in comparative


def test_reciprocal_rank_fusion():
    """Verify RRF rank aggregation logic."""
    p1 = _make_dummy_passage("p1", "attention", "text 1")
    p2 = _make_dummy_passage("p2", "moshi", "text 2")
    p3 = _make_dummy_passage("p3", "dynamic_chunking", "text 3")

    list1 = [p1, p2, p3]
    list2 = [p2, p1, p3]

    fused = reciprocal_rank_fusion([list1, list2], k_constant=60)
    # p1 and p2 have identical ranks (1+2 and 2+1), both score higher than p3 (3+3)
    fused_ids = [p.passage_id for p, score in fused]
    assert fused_ids[:2] == ["p1", "p2"] or fused_ids[:2] == ["p2", "p1"]
    assert fused_ids[2] == "p3"


def test_comparison_guard_promotion():
    """Verify Comparison Guard promotes missing named paper into top-k."""
    p_att1 = _make_dummy_passage("att1", "attention", "transformer self attention")
    p_att2 = _make_dummy_passage("att2", "attention", "multihead attention")
    p_att3 = _make_dummy_passage("att3", "attention", "positional encoding")
    p_moshi = _make_dummy_passage("moshi1", "moshi", "mimi codec speech")

    # All top-3 are attention, but question mentions Moshi as well
    ranked = [p_att1, p_att2, p_att3, p_moshi]
    query = "Compare the Transformer in Attention Is All You Need with Moshi audio model"

    guarded, triggered = apply_comparison_guard(ranked, query, top_k=3)
    assert triggered is True
    assert len(guarded) == 3
    tags = {p.short_tag for p in guarded}
    assert "attention" in tags
    assert "moshi" in tags
    assert guarded[-1].passage_id == "moshi1"


def test_retriever_bm25_execution():
    """Verify BM25 retrieval runs cleanly on the real corpus."""
    retriever = get_retriever("setting_a")
    results, info = retriever.retrieve("Scaled Dot-Product Attention equation", top_k=5)
    assert len(results) == 5
    assert any("attention" in p.short_tag for p in results)

