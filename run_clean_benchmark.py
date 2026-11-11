"""
run_clean_benchmark.py: Cleans previous results, executes the complete, properly sized
benchmark suite (5 stand-in load scenarios + 16 real Groq evaluation questions),
and generates the authoritative results.csv and events.jsonl.
"""

import asyncio
from pathlib import Path
import shutil
import sys
import time

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import RESULTS_CSV_PATH, EVENT_LOG_PATH
from eval_loader import load_eval_questions
from limiter import LimiterConfig, TrafficGate
from loadgen.scenarios import (
    run_steady_scenario,
    run_burst_scenario,
    run_sustained_overload_scenario,
    run_limit_halved_scenario,
    run_daily_budget_exhausted_scenario,
)
from pipeline import RelayPipeline


async def main() -> None:
    print("=" * 80)
    print("STARTING AUTHORITATIVE BENCHMARK RUN")
    print("=" * 80)

    # 1. Archive previous results if they exist
    if RESULTS_CSV_PATH.exists():
        archive_path = BASE_DIR / "results_archive.csv"
        shutil.copy(RESULTS_CSV_PATH, archive_path)
        RESULTS_CSV_PATH.unlink()
        print(f"Archived previous results.csv -> {archive_path}")

    if EVENT_LOG_PATH.exists():
        EVENT_LOG_PATH.unlink()
        print("Cleared events.jsonl")

    questions = load_eval_questions()

    # 2. Run the 5 Benchmark Load Scenarios against the Stand-in
    print("\n--- PHASE 1: STAND-IN LOAD SCENARIOS (70 requests total) ---")
    await run_steady_scenario(questions, source="stand-in", count=16)
    await run_burst_scenario(questions, source="stand-in", burst_size=16)
    await run_sustained_overload_scenario(questions, source="stand-in", total_requests=20)
    await run_limit_halved_scenario(questions, source="stand-in", count=12)
    await run_daily_budget_exhausted_scenario(questions, source="stand-in", count=6)

    # 3. Run the 16 Real Evaluation Questions against Real Groq
    print("\n--- PHASE 2: REAL GROQ EVALUATION (16 questions, paced under 8k TPM) ---")
    gate = TrafficGate(LimiterConfig(
        requests_per_minute=1000,
        tokens_per_minute=8000,
        default_timeout_seconds=90.0,
        in_flight_cap=1,
    ))
    pipe = RelayPipeline(source="real", retrieval_setting="setting_b", gate=gate)

    for i, q in enumerate(questions, start=1):
        print(f"[{i:02d}/16] Q: {q.question[:65]}...")
        res = await pipe.answer(
            question=q.question,
            scenario="real_eval",
            request_id=f"real_eval_{q.question_id}",
            eval_question=q,
        )
        print(f"      Status: {res.status} | Grade: {res.answer_grade} | In: {res.usage.prompt_tokens} | Out: {res.usage.completion_tokens} | TTFT: {res.timing.first_token_ms:.1f}ms")
        await asyncio.sleep(2.0)

    print("\n" + "=" * 80)
    print("AUTHORITATIVE BENCHMARK RUN COMPLETED!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())

