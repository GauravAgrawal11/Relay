"""
eval_real_questions.py: Runs the 16 evaluation questions from eval/questions.json
through the REAL Groq API (openai/gpt-oss-120b) to establish verified,
non-synthetic answer quality, citation validity, tokens per answer, and latency.
Spaced to respect the 8,000 TPM free tier rate limit.
"""

import asyncio
from pathlib import Path
import sys
import time

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from eval_loader import load_eval_questions
from pipeline import RelayPipeline


async def run_real_evaluation() -> None:
    questions = load_eval_questions()
    print("=" * 80)
    print(f"EVALUATING REAL GROQ MODEL ON {len(questions)} TEST QUESTIONS")
    print("=" * 80)

    pipe = RelayPipeline(source="real", retrieval_setting="setting_b")

    for i, q in enumerate(questions, start=1):
        print(f"\n[{i}/{len(questions)}] Q: {q.question[:65]}...")
        t0 = time.time()
        res = await pipe.answer(
            question=q.question,
            scenario="real_eval",
            request_id=f"real_q{i:02d}",
            eval_question=q,
        )
        elapsed = time.time() - t0
        print(f"  Status: {res.status} | Grade: {res.answer_grade} | Citations Valid: {res.citations_valid}")
        print(f"  TTFT: {res.timing.first_token_ms:.1f}ms | Total: {res.timing.total_ms:.1f}ms | Tokens: in={res.usage.prompt_tokens}, out={res.usage.completion_tokens}")
        print(f"  Answer excerpt: {res.answer[:90]}...")

        # Pace calls to remain under Groq's 8,000 TPM limit
        await asyncio.sleep(4.0)

    print("\n" + "=" * 80)
    print("REAL EVALUATION COMPLETED! All rows logged to results.csv.")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_real_evaluation())

