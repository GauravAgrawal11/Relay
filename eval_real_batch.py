"""
eval_real_batch.py: Evaluates all 16 benchmark questions against the REAL Groq API.
Dynamically tracks sliding window TPM to stay strictly within Groq's 8,000 TPM ceiling
without triggering rate limit timeouts.
"""

import asyncio
from pathlib import Path
import sys
import time

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from eval_loader import load_eval_questions
from limiter import LimiterConfig, TrafficGate
from pipeline import RelayPipeline


async def run_paced_evaluation() -> None:
    questions = load_eval_questions()
    print("=" * 80)
    print(f"STARTING PACED REAL EVALUATION ON {len(questions)} QUESTIONS")
    print("=" * 80)

    # Use a gate with an 80-second deadline so sliding window waits never time out
    gate = TrafficGate(LimiterConfig(
        requests_per_minute=1000,
        tokens_per_minute=8000,
        default_timeout_seconds=90.0,
        in_flight_cap=1,
    ))
    pipe = RelayPipeline(source="real", retrieval_setting="setting_b", gate=gate)

    for i, q in enumerate(questions, start=1):
        print(f"\n--- [{i}/{len(questions)}] Processing {q.question_id}: {q.question[:60]}... ---")
        t0 = time.time()
        res = await pipe.answer(
            question=q.question,
            scenario="real_eval",
            request_id=f"real_eval_{q.question_id}",
            eval_question=q,
        )
        elapsed = time.time() - t0
        print(f"  Result: {res.status} | Grade: {res.answer_grade} | Citations Valid: {res.citations_valid}")
        print(f"  TTFT: {res.timing.first_token_ms:.1f}ms | Total: {res.timing.total_ms:.1f}ms | In: {res.usage.prompt_tokens} | Out: {res.usage.completion_tokens}")
        print(f"  Answer: {res.answer[:120]}...")

        # Small 2s pause
        await asyncio.sleep(2.0)

    print("\n" + "=" * 80)
    print("PACED REAL EVALUATION FINISHED!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_paced_evaluation())

