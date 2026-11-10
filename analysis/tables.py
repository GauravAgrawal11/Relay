"""
tables.py: Analysis engine recomputing Tables 1, 2, and 3 from results.csv and events.jsonl.
Generates scenario performance statistics, cost metrics based on published Groq prices,
and isolates the slowest request to dissect waiting, working, and retrying latencies.
Time reported in milliseconds and percentages to one decimal place per assignment spec.
"""

import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import EVENT_LOG_PATH, RESULTS_CSV_PATH
from logger import default_event_logger


# Published Groq pricing (cited from https://groq.com/pricing for LLaMA 70B class models):
# Prompt: $0.59 per 1,000,000 tokens ($0.00000059 / token)
# Completion: $0.79 per 1,000,000 tokens ($0.00000079 / token)
GROQ_PUBLISHED_PROMPT_PRICE_PER_M = 0.59
GROQ_PUBLISHED_COMPLETION_PRICE_PER_M = 0.79


def compute_table_1() -> pd.DataFrame:
    """Recompute Table 1: Scenario Performance & Reliability from results.csv and events.jsonl."""
    if not RESULTS_CSV_PATH.exists():
        raise FileNotFoundError(f"{RESULTS_CSV_PATH} not found. Run scenarios first.")

    df = pd.read_csv(RESULTS_CSV_PATH)
    events = default_event_logger.read_events()

    # Calculate peak queue depth per scenario from events
    scenario_by_req = dict(zip(df["request_id"], df["scenario"]))
    peak_queue_by_scenario: Dict[str, int] = {}
    for ev in events:
        req_id = ev.get("request_id")
        scen = scenario_by_req.get(req_id, "unknown")
        q_depth = ev.get("queue_depth", 0)
        peak_queue_by_scenario[scen] = max(peak_queue_by_scenario.get(scen, 0), q_depth)

    rows = []
    for scen, group in df.groupby("scenario"):
        total = len(group)
        ok_count = sum(group["status"] == "ok")
        rl_count = sum(group["status"] == "rate_limited")
        to_count = sum(group["status"] == "timeout")
        err_count = sum(group["status"] == "error")

        ttft_p50 = float(np.percentile(group["first_token_ms"], 50))
        ttft_p90 = float(np.percentile(group["first_token_ms"], 90))
        tot_p50 = float(np.percentile(group["total_ms"], 50))
        tot_p90 = float(np.percentile(group["total_ms"], 90))

        correct_pct = (sum(group["answer_grade"] == "correct") / total) * 100.0
        refused_corr_pct = (sum(group["answer_grade"] == "refused_correctly") / total) * 100.0
        wrong_pct = (sum(group["answer_grade"] == "wrong") / total) * 100.0
        refused_wrong_pct = (sum(group["answer_grade"] == "refused_wrongly") / total) * 100.0

        rows.append({
            "Scenario": scen,
            "Total Requests": total,
            "Success Rate (%)": round((ok_count / total) * 100.0, 1),
            "Rate Limited (%)": round((rl_count / total) * 100.0, 1),
            "Timeout (%)": round((to_count / total) * 100.0, 1),
            "Peak Queue": peak_queue_by_scenario.get(scen, 0),
            "TTFT p50 (ms)": round(ttft_p50, 1),
            "TTFT p90 (ms)": round(ttft_p90, 1),
            "Total p50 (ms)": round(tot_p50, 1),
            "Total p90 (ms)": round(tot_p90, 1),
            "Avg Tokens In": round(float(group["tokens_in"].mean()), 1),
            "Avg Tokens Out": round(float(group["tokens_out"].mean()), 1),
            "Correct (%)": round(correct_pct, 1),
            "Refused Correctly (%)": round(refused_corr_pct, 1),
            "Wrong (%)": round(wrong_pct, 1),
        })

    return pd.DataFrame(rows)


def compute_table_2() -> pd.DataFrame:
    """Recompute Table 2: Cost Analysis from results.csv and published Groq pricing."""
    df = pd.read_csv(RESULTS_CSV_PATH)
    total_tokens_in = int(df["tokens_in"].sum())
    total_tokens_out = int(df["tokens_out"].sum())
    total_requests = len(df)

    avg_tokens_in = total_tokens_in / total_requests if total_requests > 0 else 0
    avg_tokens_out = total_tokens_out / total_requests if total_requests > 0 else 0

    # Published cost per 1,000 answers
    cost_prompt_1k = (avg_tokens_in * 1000 * GROQ_PUBLISHED_PROMPT_PRICE_PER_M) / 1_000_000
    cost_completion_1k = (avg_tokens_out * 1000 * GROQ_PUBLISHED_COMPLETION_PRICE_PER_M) / 1_000_000
    total_cost_1k = cost_prompt_1k + cost_completion_1k

    table2_data = [
        {"Metric": "Actual Cost on Groq Free Tier", "Value": "$0.00"},
        {"Metric": "Total Tokens In (Logged)", "Value": f"{total_tokens_in:,}"},
        {"Metric": "Total Tokens Out (Logged)", "Value": f"{total_tokens_out:,}"},
        {"Metric": "Avg Tokens In / Request", "Value": f"{avg_tokens_in:.1f}"},
        {"Metric": "Avg Tokens Out / Request", "Value": f"{avg_tokens_out:.1f}"},
        {"Metric": "Published Prompt Price (Groq LLaMA 70B)", "Value": "$0.59 / 1M tokens"},
        {"Metric": "Published Completion Price (Groq LLaMA 70B)", "Value": "$0.79 / 1M tokens"},
        {"Metric": "Estimated Cost per 1,000 Answers", "Value": f"${total_cost_1k:.4f}"},
    ]
    return pd.DataFrame(table2_data)


def analyze_slowest_request() -> Dict[str, Any]:
    """
    Select the slowest request in results.csv and derive waiting/working/retrying
    latencies from events.jsonl. Determine whether delay was before or after first token.
    """
    df = pd.read_csv(RESULTS_CSV_PATH)
    slowest_row = df.loc[df["total_ms"].idxmax()]
    req_id = slowest_row["request_id"]

    events = default_event_logger.read_events(request_id=req_id)
    breakdown = default_event_logger.get_latency_breakdown(request_id=req_id)

    ttft_ms = float(slowest_row["first_token_ms"])
    total_ms = float(slowest_row["total_ms"])
    delay_before_first_token = ttft_ms
    delay_after_first_token = max(0.0, total_ms - ttft_ms)

    primary_delay_location = (
        "BEFORE the first token (queue wait / model scheduling)"
        if delay_before_first_token >= delay_after_first_token
        else "AFTER the first token (token generation / streaming)"
    )

    return {
        "request_id": req_id,
        "scenario": slowest_row["scenario"],
        "source": slowest_row["source"],
        "status": slowest_row["status"],
        "total_ms": round(total_ms, 1),
        "ttft_ms": round(ttft_ms, 1),
        "waiting_ms": breakdown["waiting_ms"],
        "working_ms": breakdown["working_ms"],
        "retrying_ms": breakdown["retrying_ms"],
        "delay_before_first_token_ms": round(delay_before_first_token, 1),
        "delay_after_first_token_ms": round(delay_after_first_token, 1),
        "primary_delay_location": primary_delay_location,
        "event_count": len(events),
    }


def print_full_report() -> None:
    print("=" * 80)
    print("TABLE 1: SCENARIO PERFORMANCE & RELIABILITY (Computed from results.csv)")
    print("=" * 80)
    t1 = compute_table_1()
    print(t1.to_string(index=False))

    print("\n" + "=" * 80)
    print("TABLE 2: COST & USAGE ANALYSIS (Published Groq Pricing)")
    print("=" * 80)
    t2 = compute_table_2()
    print(t2.to_string(index=False))

    print("\n" + "=" * 80)
    print("SLOWEST REQUEST LATENCY DISSECTION")
    print("=" * 80)
    slow = analyze_slowest_request()
    for k, v in slow.items():
        print(f"  {k:<32}: {v}")
    print("=" * 80)


if __name__ == "__main__":
    print_full_report()

