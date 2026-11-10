"""
compare_distributions.py: Verifies stand-in model fidelity against real Groq measurements.
Executes test completions against the stand-in server, calculates latency percentiles
(TTFT p50/p90, total latency p50/p90), and prints a side-by-side comparison with the
calibrated numbers recorded in probes/raw_probe_output.json.
"""

import asyncio
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Dict, List
import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import PROBES_DIR
from standin.server import app


async def run_standin_distribution_check(num_samples: int = 5) -> Dict[str, Any]:
    """Execute sample requests against the standin app directly via ASGI client."""
    probe_file = PROBES_DIR / "raw_probe_output.json"
    if not probe_file.exists():
        raise FileNotFoundError(f"Real probe data not found at {probe_file}. Run probes first.")

    with open(probe_file, "r", encoding="utf-8") as f:
        real_data = json.load(f)

    real_lat = real_data["phase2_latency"]
    real_ttft_p50 = real_lat["ttft_p50_ms"]
    real_ttft_p90 = real_lat["ttft_p90_ms"]
    real_total_p50 = real_lat["total_p50_ms"]
    real_total_p90 = real_lat["total_p90_ms"]

    ttft_samples: List[float] = []
    total_samples: List[float] = []

    # Use ASGI transport directly for fast, self-contained in-process testing
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for idx in range(num_samples):
            payload = {
                "model": "openai/gpt-oss-120b",
                "messages": [{"role": "user", "content": f"Test question {idx}: explain attention."}],
                "stream": True,
            }
            t_sent = time.time()
            t_first_token = 0.0
            async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
                if resp.status_code == 200:
                    async for line in resp.aiter_lines():
                        if line.startswith("data:") and "[DONE]" not in line and t_first_token == 0.0:
                            t_first_token = time.time()
                t_done = time.time()

            if t_first_token > 0.0:
                ttft_samples.append((t_first_token - t_sent) * 1000.0)
                total_samples.append((t_done - t_sent) * 1000.0)

    standin_ttft_p50 = statistics.median(ttft_samples) if ttft_samples else 0.0
    standin_ttft_p90 = sorted(ttft_samples)[int(len(ttft_samples) * 0.9)] if ttft_samples else 0.0
    standin_total_p50 = statistics.median(total_samples) if total_samples else 0.0
    standin_total_p90 = sorted(total_samples)[int(len(total_samples) * 0.9)] if total_samples else 0.0

    print("=" * 75)
    print("DISTRIBUTION FIDELITY COMPARISON: REAL GROQ vs. STAND-IN SERVICE")
    print("=" * 75)
    print(f"{'Metric':<25} | {'Real Groq (Probed)':<20} | {'Stand-in Service':<20}")
    print("-" * 75)
    print(f"{'TTFT p50 (ms)':<25} | {real_ttft_p50:<20.1f} | {standin_ttft_p50:<20.1f}")
    print(f"{'TTFT p90 (ms)':<25} | {real_ttft_p90:<20.1f} | {standin_ttft_p90:<20.1f}")
    print(f"{'Total Latency p50 (ms)':<25} | {real_total_p50:<20.1f} | {standin_total_p50:<20.1f}")
    print(f"{'Total Latency p90 (ms)':<25} | {real_total_p90:<20.1f} | {standin_total_p90:<20.1f}")
    print("=" * 75)

    return {
        "real": {
            "ttft_p50": real_ttft_p50,
            "ttft_p90": real_ttft_p90,
            "total_p50": real_total_p50,
            "total_p90": real_total_p90,
        },
        "standin": {
            "ttft_p50": round(standin_ttft_p50, 1),
            "ttft_p90": round(standin_ttft_p90, 1),
            "total_p50": round(standin_total_p50, 1),
            "total_p90": round(standin_total_p90, 1),
        },
    }


if __name__ == "__main__":
    asyncio.run(run_standin_distribution_check(num_samples=5))

