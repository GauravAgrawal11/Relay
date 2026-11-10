"""
scenarios.py: Workload generation suite executing the five benchmark evaluation shapes:
1) Steady traffic, 2) Sudden burst, 3) Sustained overload, 4) Upstream limit halved,
and 5) Daily budget exhaustion. Evaluates traffic gating, bounded queues, and retries,
logging every single request row directly into results.csv and events.jsonl.
"""

import asyncio
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from eval_loader import EvalQuestion, load_eval_questions
from limiter import LimiterConfig, TrafficGate
from pipeline import RelayPipeline


async def run_steady_scenario(
    questions: List[EvalQuestion],
    source: str = "stand-in",
    retrieval_setting: str = "setting_b",
    count: int = 6,
) -> List[Any]:
    """Steady scenario: requests spaced at regular, compliant intervals."""
    print("\n--- Running Scenario 1: STEADY ---")
    gate = TrafficGate(LimiterConfig(requests_per_minute=1000, tokens_per_minute=8000))
    pipe = RelayPipeline(source=source, retrieval_setting=retrieval_setting, gate=gate)
    results = []

    for i in range(count):
        q = questions[i % len(questions)]
        res = await pipe.answer(
            question=q.question,
            scenario="steady",
            request_id=f"steady_{i+1:02d}",
            eval_question=q,
        )
        results.append(res)
        await asyncio.sleep(0.3)

    return results


async def run_burst_scenario(
    questions: List[EvalQuestion],
    source: str = "stand-in",
    retrieval_setting: str = "setting_b",
    burst_size: int = 8,
) -> List[Any]:
    """Burst scenario: sudden flood of simultaneous requests."""
    print("\n--- Running Scenario 2: BURST ---")
    gate = TrafficGate(LimiterConfig(in_flight_cap=4, max_queue_depth=15))
    pipe = RelayPipeline(source=source, retrieval_setting=retrieval_setting, gate=gate)

    tasks = [
        pipe.answer(
            question=questions[i % len(questions)].question,
            scenario="burst",
            request_id=f"burst_{i+1:02d}",
            eval_question=questions[i % len(questions)],
        )
        for i in range(burst_size)
    ]
    results = await asyncio.gather(*tasks)
    return list(results)


async def run_sustained_overload_scenario(
    questions: List[EvalQuestion],
    source: str = "stand-in",
    retrieval_setting: str = "setting_b",
    total_requests: int = 12,
) -> List[Any]:
    """Sustained overload: high rate that exercises bounded queue and backoff."""
    print("\n--- Running Scenario 3: SUSTAINED OVERLOAD ---")
    gate = TrafficGate(LimiterConfig(in_flight_cap=3, max_queue_depth=5, default_timeout_seconds=10.0))
    pipe = RelayPipeline(source=source, retrieval_setting=retrieval_setting, gate=gate)

    tasks = [
        pipe.answer(
            question=questions[i % len(questions)].question,
            scenario="sustained_overload",
            request_id=f"overload_{i+1:02d}",
            eval_question=questions[i % len(questions)],
        )
        for i in range(total_requests)
    ]
    results = await asyncio.gather(*tasks)
    return list(results)


async def run_limit_halved_scenario(
    questions: List[EvalQuestion],
    source: str = "stand-in",
    retrieval_setting: str = "setting_b",
    count: int = 6,
) -> List[Any]:
    """Limit halved scenario: simulated 50% degraded upstream TPM/RPM limits."""
    print("\n--- Running Scenario 4: PER-MINUTE LIMIT HALVED ---")
    halved_gate = TrafficGate(LimiterConfig(requests_per_minute=500, tokens_per_minute=4000))
    pipe = RelayPipeline(source=source, retrieval_setting=retrieval_setting, gate=halved_gate)
    results = []

    for i in range(count):
        q = questions[i % len(questions)]
        res = await pipe.answer(
            question=q.question,
            scenario="limit_halved",
            request_id=f"halved_{i+1:02d}",
            eval_question=q,
        )
        results.append(res)
        await asyncio.sleep(0.2)

    return results


async def run_daily_budget_exhausted_scenario(
    questions: List[EvalQuestion],
    source: str = "stand-in",
    retrieval_setting: str = "setting_b",
    count: int = 4,
) -> List[Any]:
    """Daily budget exhausted scenario: zero quota remaining, immediate refusal."""
    print("\n--- Running Scenario 5: DAILY BUDGET EXHAUSTED ---")
    exhausted_gate = TrafficGate(LimiterConfig(daily_requests_budget=0))
    pipe = RelayPipeline(source=source, retrieval_setting=retrieval_setting, gate=exhausted_gate)
    results = []

    for i in range(count):
        q = questions[i % len(questions)]
        res = await pipe.answer(
            question=q.question,
            scenario="daily_budget_exhausted",
            request_id=f"exhausted_{i+1:02d}",
            eval_question=q,
        )
        results.append(res)

    return results


async def run_all_benchmark_scenarios(
    source: str = "stand-in",
    retrieval_setting: str = "setting_b",
) -> None:
    """Execute all 5 scenarios and report summary."""
    questions = load_eval_questions()

    print("=" * 75)
    print(f"EXECUTING ALL 5 BENCHMARK LOAD SCENARIOS (Source: {source})")
    print("=" * 75)

    await run_steady_scenario(questions, source=source, retrieval_setting=retrieval_setting)
    await run_burst_scenario(questions, source=source, retrieval_setting=retrieval_setting)
    await run_sustained_overload_scenario(questions, source=source, retrieval_setting=retrieval_setting)
    await run_limit_halved_scenario(questions, source=source, retrieval_setting=retrieval_setting)
    await run_daily_budget_exhausted_scenario(questions, source=source, retrieval_setting=retrieval_setting)

    print("\n" + "=" * 75)
    print("ALL 5 SCENARIOS COMPLETED! All rows logged to results.csv.")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(run_all_benchmark_scenarios(source="stand-in"))

