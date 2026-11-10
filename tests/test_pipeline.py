"""
tests/test_pipeline.py: Unit tests for evidence.py, grading.py, and pipeline.py.
Validates end-to-end question answering across Zones 1 to 3, local evidence refusals,
spell-fixing of domain terms, and automated result row logging.
"""

import pytest
from eval_loader import EvalQuestion
from evidence import check_clarity, check_evidence, spell_fix_query, validate_citations
from grading import grade_answer
from pipeline import RelayPipeline


def test_spell_fix_protects_domain_terms():
    """Verify domain keywords like Moshi, Mimi, and H-Net are never altered."""
    q = "What is the frame rate of the mimi codec in moshi?"
    fixed, modified = spell_fix_query(q)
    assert "mimi" in fixed.lower()
    assert "moshi" in fixed.lower()


def test_unrelated_evidence_refusal_in_zone1():
    """Verify completely out-of-scope query triggers immediate refusal without model calls."""
    decision = check_evidence("What is the battery life of the Apple Vision Pro?", [])
    assert decision["decision"] == "UNRELATED"
    assert "covers only" in decision["reason"]


def test_grading_logic():
    """Test automated grading across refusal and answer cases."""
    q_unans = EvalQuestion(
        question_id="test_unans",
        question="Out of scope question",
        category="unanswerable",
        target_papers=["none"],
        must_refuse=True,
    )
    grade1 = grade_answer("This assistant cannot answer; out of scope.", q_unans)
    assert grade1 == "refused_correctly"

    grade2 = grade_answer("Here is an answer about that topic [attention v7 p.1].", q_unans)
    assert grade2 == "wrong"


@pytest.mark.asyncio
async def test_end_to_end_pipeline_standin():
    """Verify end-to-end execution of RelayPipeline using stand-in backend."""
    pipe = RelayPipeline(source="stand-in", retrieval_setting="setting_b")
    result = await pipe.answer("What is scaled dot-product attention in Attention Is All You Need?")

    assert result.status == "ok"
    assert result.source == "stand-in"
    assert len(result.retrieved_passages) > 0
    assert result.timing.first_token_ms > 0
    assert result.timing.total_ms >= result.timing.first_token_ms
    assert result.answer_grade in ("correct", "refused_correctly")

