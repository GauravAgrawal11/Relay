"""
tests/test_eval_loader.py: Unit tests for eval_loader.py module.
Validates loading and schema conformance of eval/questions.json across
answerable, unanswerable, and naive-retriever trap categories.
"""

from eval_loader import (
    EvalQuestion,
    GoldPassage,
    load_eval_questions,
    validate_eval_questions,
)


def test_load_and_validate_questions():
    """Verify loading of evaluation questions and schema compliance."""
    questions = load_eval_questions()
    assert len(questions) >= 15

    ok, errors = validate_eval_questions(questions)
    assert ok, f"Validation errors: {errors}"


def test_categories_represented():
    """Ensure all required categories are present in the benchmark suite."""
    questions = load_eval_questions()
    categories = {q.category for q in questions}

    assert "answerable" in categories
    assert "unanswerable" in categories
    assert any("trap" in c for c in categories)


def test_unanswerable_refusal_flag():
    """Ensure every unanswerable question has must_refuse == True."""
    questions = load_eval_questions()
    unanswerable = [q for q in questions if q.category == "unanswerable"]
    assert len(unanswerable) >= 3
    for q in unanswerable:
        assert q.must_refuse is True


def test_gold_passage_matching():
    """Test GoldPassage matching logic."""
    gp = GoldPassage(short_tag="attention", page=4, section="s3.2.1")
    assert gp.matches_page("attention", 4)
    assert gp.matches_page("ATTENTION", 4)
    assert not gp.matches_page("attention", 5)
    assert not gp.matches_page("moshi", 4)

