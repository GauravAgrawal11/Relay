"""
tests/test_citations_and_formatting.py: Unit and regression tests covering:
1. Question with one strong supporting passage.
2. Question with multiple supporting passages.
3. Question with several retrieved but irrelevant passages.
4. Question with insufficient evidence.
5. Answer containing Markdown bold formatting.
6. Answer with an invalid or mismatched citation.
"""

from chunking import Passage
from evidence import (
    check_evidence,
    extract_cited_tags,
    filter_supporting_passages,
    format_pretty_citation,
    validate_citations,
)


def _make_passage(pid: str, short_tag: str, ver: str, sec: str, page: int, text: str) -> Passage:
    sec_tag = f" {sec}" if sec else ""
    return Passage(
        passage_id=pid,
        paper_id=f"paper_{short_tag}",
        arxiv_id=f"2410.{pid}",
        version=ver,
        title=f"Title {short_tag}",
        short_tag=short_tag,
        page=page,
        section=sec,
        section_title=f"Section {sec}",
        tag=f"[{short_tag} {ver}{sec_tag} p.{page}]",
        text=text,
        word_count=len(text.split()),
        char_count=len(text),
    )


def test_one_strong_supporting_passage():
    """Verify answer isolates single strong supporting passage from multiple candidates."""
    p_irr1 = _make_passage("p1", "moshi", "v1", "s5.2", 24, "audio tokenization score 0 to 100")
    p_sup = _make_passage("p2", "moshi", "v1", "s3.3.1", 11, "Mimi outputs a first latent timestep at 12.5Hz representing 80ms of audio.")
    p_irr2 = _make_passage("p3", "moshi", "v1", "s6.3", 37, "system voice consistency and deduplicated fine-tuned temp")

    passages = [p_irr1, p_sup, p_irr2]
    answer = "Mimi processes audio at a 12.5 Hz frame rate (one latent timestep every 80 ms) [moshi v1 s3.3.1 p.11]."

    supporting = filter_supporting_passages(answer, passages)
    assert len(supporting) == 1
    assert supporting[0].passage_id == "p2"
    assert format_pretty_citation(supporting[0]) == "[Moshi v1 · Section 3.3.1 · Page 11]"


def test_multiple_supporting_passages():
    """Verify answer citing multiple sections correctly identifies all supporting passages."""
    p1 = _make_passage("p1", "attention", "v7", "s3.2.1", 4, "Scaled dot-product attention scales by 1/sqrt(d_k).")
    p2 = _make_passage("p2", "attention", "v7", "s3.2.2", 5, "Multi-head attention projects queries, keys, and values h times.")
    p3 = _make_passage("p3", "moshi", "v1", "s1.0", 2, "Moshi conversational audio model.")

    passages = [p1, p2, p3]
    answer = (
        "The model uses scaled dot-product attention [attention v7 s3.2.1 p.4] "
        "and combines them via multi-head attention [attention v7 s3.2.2 p.5]."
    )

    supporting = filter_supporting_passages(answer, passages)
    assert len(supporting) == 2
    supporting_tags = {p.tag.lower() for p in supporting}
    assert "[attention v7 s3.2.1 p.4]" in supporting_tags
    assert "[attention v7 s3.2.2 p.5]" in supporting_tags


def test_retrieved_but_irrelevant_passages():
    """Verify retrieved passages that do not support the factual claim are excluded from supporting citations."""
    p_target = _make_passage("p1", "moshi", "v1", "s3.3.1", 11, "Operating at 12.5 Hz with 80 ms latency.")
    p_irr1 = _make_passage("p2", "moshi", "v1", "s3.2.2", 7, "Filtering Wikipedia data sources.")
    p_irr2 = _make_passage("p3", "moshi", "v1", "s6.3", 37, "Regurgitation rate in single stream.")

    passages = [p_irr1, p_irr2, p_target]
    answer = "Mimi operates at 12.5 Hz [moshi v1 s3.3.1 p.11]."

    supporting = filter_supporting_passages(answer, passages)
    assert len(supporting) == 1
    assert supporting[0].passage_id == "p1"
    # p_irr1 and p_irr2 should not be in supporting citations
    assert p_irr1 not in supporting
    assert p_irr2 not in supporting


def test_insufficient_evidence_triggers_refusal():
    """Verify query with insufficient lexical evidence refuses instead of hallucinating."""
    p1 = _make_passage("p1", "attention", "v7", "s3.1", 3, "Attention encoder and decoder stacks.")
    ev = check_evidence("What is the battery capacity of the Apple Vision Pro?", [p1])
    assert ev["decision"] == "UNRELATED"
    assert "covers only" in ev["reason"]


def test_answer_containing_markdown_bold():
    """Verify answer containing Markdown bold asterisks is preserved and cleanly cited."""
    p = _make_passage("p1", "moshi", "v1", "s3.3.1", 11, "Mimi operates at 12.5 Hz frame rate.")
    answer = "Mimi operates at a **12.5 Hz frame rate** [moshi v1 s3.3.1 p.11]."

    # Must preserve asterisks for marked.js frontend parsing
    assert "**12.5 Hz frame rate**" in answer
    tags = extract_cited_tags(answer)
    assert tags == ["[moshi v1 s3.3.1 p.11]"]

    valid, errors = validate_citations(answer, [p])
    assert valid is True
    assert len(errors) == 0


def test_invalid_or_mismatched_citation():
    """Verify validator catches fabricated citations or wrong page numbers."""
    p_real = _make_passage("p1", "moshi", "v1", "s3.3.1", 11, "Mimi operates at 12.5 Hz.")

    # Model cited non-existent page 99 or non-existent section s9.9
    bad_answer = "Mimi operates at 12.5 Hz [moshi v1 s9.9 p.99]."
    valid, errors = validate_citations(bad_answer, [p_real])
    assert valid is False
    assert len(errors) > 0
    assert "was not found in retrieved passages" in errors[0]

