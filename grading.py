"""
grading.py: Automated answer grading against benchmark evaluation criteria.
Assigns one of four strict assignment labels:
'correct', 'wrong', 'refused_correctly', 'refused_wrongly'
based on gold factual requirements, citation presence, and refusal necessity.
"""

import re
from typing import Any, Dict, List, Optional
from eval_loader import EvalQuestion


def is_refusal_answer(answer: str) -> bool:
    """Detect whether an answer constitutes an evidence or scope refusal."""
    ans_lower = answer.lower()
    refusal_keywords = [
        "refuse", "cannot answer", "does not contain", "do not mention",
        "not covered", "don't establish", "do not establish", "does not establish",
        "outside the scope", "does not discuss", "only covers",
        "not available in the provided", "papers do not say", "papers do not establish",
    ]
    return any(kw in ans_lower for kw in refusal_keywords)


def grade_answer(
    answer: str,
    question: EvalQuestion,
    citations_valid: bool = True,
    status: str = "ok",
) -> str:
    """
    Grade a generated pipeline answer against the benchmark question:
    Returns one of: 'correct', 'wrong', 'refused_correctly', 'refused_wrongly'
    """
    refused = is_refusal_answer(answer) or status in ("rate_limited", "timeout")

    # 1. Unanswerable question evaluations
    if question.must_refuse:
        if refused:
            return "refused_correctly"
        else:
            return "wrong"  # Hallucinated an answer when refusal was required

    # 2. Answerable question evaluations
    if refused:
        return "refused_wrongly"  # Refused when gold evidence existed

    if not citations_valid:
        return "wrong"

    # Verify key factual presence
    ans_lower = answer.lower()
    matched_facts = 0
    total_facts = len(question.key_facts)

    if total_facts == 0:
        return "correct" if len(answer.split()) > 10 else "wrong"

    for fact in question.key_facts:
        fact_tokens = [w.lower() for w in re.findall(r"\b[\w\.\^/]+\b", fact)]
        # Check if significant portion of fact tokens appear
        if any(t in ans_lower for t in fact_tokens if len(t) > 2):
            matched_facts += 1

    fact_score = matched_facts / total_facts
    if fact_score >= 0.5:
        return "correct"
    else:
        return "wrong"

