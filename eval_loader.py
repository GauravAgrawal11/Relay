"""
eval_loader.py: Schema definitions and validation loader for eval/questions.json.
Enforces structural requirements for evaluation questions and gold passage provenance
across answerable, unanswerable, and naive-retriever trap categories. This forms the
ground-truth foundation for retrieval benchmarks and pipeline answer grading.
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import QUESTIONS_PATH, TARGET_PAPERS


@dataclass
class GoldPassage:
    """Ground truth passage citation and key factual excerpt."""
    short_tag: str
    page: int
    paper_id: Optional[str] = None
    section: Optional[str] = None
    quote: Optional[str] = None

    def matches_page(self, short_tag: str, page: int) -> bool:
        """Check if retrieved passage matches this gold passage by paper and page."""
        return self.short_tag.lower() == short_tag.lower() and self.page == page


@dataclass
class EvalQuestion:
    """Individual benchmark question with gold provenance and grading criteria."""
    question_id: str
    question: str
    category: str  # 'answerable', 'unanswerable', 'trap_distractor', 'trap_multi_paper'
    target_papers: List[str]
    gold_passages: List[GoldPassage] = field(default_factory=list)
    key_facts: List[str] = field(default_factory=list)
    must_refuse: bool = False
    expected_answer: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "category": self.category,
            "target_papers": self.target_papers,
            "must_refuse": self.must_refuse,
            "key_facts": self.key_facts,
            "expected_answer": self.expected_answer,
            "notes": self.notes,
            "gold_passages": [
                {
                    "short_tag": gp.short_tag,
                    "page": gp.page,
                    "paper_id": gp.paper_id,
                    "section": gp.section,
                    "quote": gp.quote,
                }
                for gp in self.gold_passages
            ],
        }


def parse_gold_passage(data: Dict[str, Any]) -> GoldPassage:
    """Parse a single gold passage dictionary into a GoldPassage dataclass."""
    short_tag = data["short_tag"].strip().lower()
    # Resolve paper_id if not explicitly provided
    paper_id = data.get("paper_id")
    if not paper_id:
        for full_id, p in TARGET_PAPERS.items():
            if p.short_tag == short_tag:
                paper_id = full_id
                break

    return GoldPassage(
        short_tag=short_tag,
        page=int(data["page"]),
        paper_id=paper_id,
        section=data.get("section"),
        quote=data.get("quote"),
    )


def parse_eval_question(data: Dict[str, Any]) -> EvalQuestion:
    """Parse a single evaluation question dictionary into an EvalQuestion dataclass."""
    gold_passages = [parse_gold_passage(gp) for gp in data.get("gold_passages", [])]
    return EvalQuestion(
        question_id=str(data["question_id"]),
        question=str(data["question"]).strip(),
        category=str(data.get("category", "answerable")),
        target_papers=[str(p).strip().lower() for p in data.get("target_papers", [])],
        gold_passages=gold_passages,
        key_facts=[str(f).strip() for f in data.get("key_facts", [])],
        must_refuse=bool(data.get("must_refuse", False)),
        expected_answer=data.get("expected_answer"),
        notes=data.get("notes"),
    )


def load_eval_questions(path: Path = QUESTIONS_PATH) -> List[EvalQuestion]:
    """Load and parse evaluation questions from the specified JSON file."""
    if not path.exists():
        raise FileNotFoundError(f"Evaluation questions file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected questions.json to contain a JSON list, got {type(data)}")

    return [parse_eval_question(item) for item in data]


def validate_eval_questions(questions: List[EvalQuestion]) -> Tuple[bool, List[str]]:
    """
    Validate that the question set meets all assignment requirements:
    1. At least 15 questions.
    2. Contains answerable, unanswerable, and naive-retriever trap questions.
    3. Unanswerable questions have must_refuse == True.
    4. Target papers refer to valid papers.
    5. Gold passage page numbers are within valid ranges.
    """
    errors: List[str] = []
    if len(questions) < 15:
        errors.append(f"Expected >= 15 questions, found {len(questions)}")

    valid_tags = {p.short_tag for p in TARGET_PAPERS.values()}

    categories = {q.category for q in questions}
    if "answerable" not in categories:
        errors.append("Missing 'answerable' category questions")
    if "unanswerable" not in categories:
        errors.append("Missing 'unanswerable' category questions")
    if not any("trap" in c for c in categories):
        errors.append("Missing naive-retriever trap questions ('trap_distractor' or 'trap_multi_paper')")

    for q in questions:
        # Check target paper tags
        for tag in q.target_papers:
            if tag not in valid_tags and tag != "none":
                errors.append(f"Question {q.question_id}: invalid target paper '{tag}'")

        # Unanswerable checks
        if q.category == "unanswerable" and not q.must_refuse:
            errors.append(f"Question {q.question_id}: unanswerable category must set must_refuse=True")

        # Answerable questions should have at least one gold passage
        if q.category == "answerable" and not q.gold_passages:
            errors.append(f"Question {q.question_id}: answerable question must have at least 1 gold passage")

        # Check gold passages
        for gp in q.gold_passages:
            if gp.short_tag not in valid_tags:
                errors.append(f"Question {q.question_id}: gold passage invalid paper tag '{gp.short_tag}'")
            if gp.page < 1:
                errors.append(f"Question {q.question_id}: gold passage page must be >= 1")

    return (len(errors) == 0), errors


if __name__ == "__main__":
    qs = load_eval_questions()
    ok, errs = validate_eval_questions(qs)
    print(f"Loaded {len(qs)} questions. Validation OK: {ok}")
    if errs:
        for e in errs:
            print("  Error:", e)
    else:
        cat_counts: Dict[str, int] = {}
        for q in qs:
            cat_counts[q.category] = cat_counts.get(q.category, 0) + 1
        print("Categories breakdown:", cat_counts)

