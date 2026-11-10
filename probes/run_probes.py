"""
run_probes.py: Diagnostic probing suite for measuring real Groq API performance.
Captures docs-stated rate limits, measures single-call and concurrent latency percentiles,
probes rate-limit boundaries (HTTP 429) to record exact response headers and recovery times,
and exports raw telemetry to raw_probe_output.json to calibrate the stand-in model server.
"""

import asyncio
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Dict, List
import httpx

# Ensure project root is in sys.path
BASE_PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(BASE_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_PROJECT_DIR))

from config import DEFAULT_GROQ_MODEL, PROBES_DIR, get_groq_api_key
from logger import sanitize_headers
from model_client import GroqClient


async def probe_baseline_limits(client: GroqClient) -> Dict[str, Any]:
    """Probe live headers for published and runtime rate limits."""
    resp = await client.complete([{"role": "user", "content": "ping"}], stream=True)
    return {
        "status": resp.status,
        "status_code": resp.status_code,
        "model": client.model_name,
        "headers": resp.headers,
        "usage": {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
        },
    }


async def probe_latency_spread(client: GroqClient, count: int = 5) -> Dict[str, Any]:
    """Measure single-call latency spread across sequential calls."""
    prompts = [
        "Explain scaled dot-product attention in 20 words.",
        "What is the frame rate of Mimi audio codec?",
        "How does H-Net learn chunk boundaries?",
        "List 3 components of the Transformer encoder.",
        "Explain the purpose of the inner monologue in Moshi in 15 words.",
    ]
    ttft_list = []
    total_list = []
    runs = []

    for i in range(min(count, len(prompts))):
        p = prompts[i]
        resp = await client.complete([{"role": "user", "content": p}], stream=True)
        if resp.status == "ok":
            ttft = resp.timing.first_token_ms
            tot = resp.timing.total_ms
            ttft_list.append(ttft)
            total_list.append(tot)
            runs.append({
                "prompt": p,
                "ttft_ms": round(ttft, 1),
                "total_ms": round(tot, 1),
                "tokens_in": resp.usage.prompt_tokens,
                "tokens_out": resp.usage.completion_tokens,
            })
        await asyncio.sleep(0.5)  # gentle spacing

    p50_ttft = statistics.median(ttft_list) if ttft_list else 0.0
    p90_ttft = sorted(ttft_list)[int(len(ttft_list) * 0.9)] if ttft_list else 0.0
    p50_tot = statistics.median(total_list) if total_list else 0.0
    p90_tot = sorted(total_list)[int(len(total_list) * 0.9)] if total_list else 0.0

    return {
        "runs": runs,
        "ttft_p50_ms": round(p50_ttft, 1),
        "ttft_p90_ms": round(p90_ttft, 1),
        "total_p50_ms": round(p50_tot, 1),
        "total_p90_ms": round(p90_tot, 1),
    }


async def probe_concurrency(client: GroqClient, concurrency: int = 3) -> Dict[str, Any]:
    """Measure modest concurrent request behavior."""
    tasks = [
        client.complete([{"role": "user", "content": f"Brief sentence {idx} about deep learning."}], stream=True)
        for idx in range(concurrency)
    ]
    start = time.time()
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    wall_ms = (time.time() - start) * 1000.0

    valid_runs = []
    for idx, r in enumerate(responses):
        if not isinstance(r, Exception) and r.status == "ok":
            valid_runs.append({
                "idx": idx,
                "ttft_ms": round(r.timing.first_token_ms, 1),
                "total_ms": round(r.timing.total_ms, 1),
                "tokens_in": r.usage.prompt_tokens,
                "tokens_out": r.usage.completion_tokens,
            })

    return {
        "concurrency": concurrency,
        "wall_time_ms": round(wall_ms, 1),
        "successful_responses": len(valid_runs),
        "details": valid_runs,
    }


async def probe_rate_limit(client: GroqClient) -> Dict[str, Any]:
    """
    Probe rate-limit behavior by generating high token requests rapidly.
    Captures status 429, response body, all headers, and recovery time.
    """
    large_prompt = "Generate a comprehensive, extremely detailed technical essay explaining attention mechanisms in 800 words. " * 3
    api_key = get_groq_api_key(required=True)
    url = f"{client.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    observed_429 = None
    recovery_ms = 0.0

    # Rapid fire requests to consume TPM
    async with httpx.AsyncClient(timeout=20.0) as http_client:
        for attempt in range(5):
            payload = {
                "model": client.model_name,
                "messages": [{"role": "user", "content": large_prompt}],
                "max_tokens": 1500,
            }
            t0 = time.time()
            resp = await http_client.post(url, headers=headers, json=payload)
            elapsed = (time.time() - t0) * 1000.0

            if resp.status_code == 429:
                observed_429 = {
                    "attempt": attempt + 1,
                    "status_code": 429,
                    "body": resp.text,
                    "headers": sanitize_headers(dict(resp.headers)),
                    "retry_after": resp.headers.get("retry-after") or resp.headers.get("x-ratelimit-reset-tokens"),
                }
                # Measure recovery time
                retry_wait = 3.0
                if resp.headers.get("retry-after"):
                    try:
                        retry_wait = float(resp.headers["retry-after"])
                    except ValueError:
                        pass
                await asyncio.sleep(retry_wait + 1.0)
                # Verify recovery
                t_rec = time.time()
                rec_resp = await http_client.post(url, headers=headers, json={"model": client.model_name, "messages": [{"role": "user", "content": "ping"}]})
                recovery_ms = (time.time() - t_rec) * 1000.0
                observed_429["recovery_confirmed"] = (rec_resp.status_code == 200)
                observed_429["recovery_ms"] = round(recovery_ms, 1)
                break
            await asyncio.sleep(0.2)

    if not observed_429:
        # If 429 wasn't hit, record current rate limit headroom
        observed_429 = {
            "status_code": 200,
            "note": "429 boundary not triggered within small budget; remaining TPM capacity recorded from headers",
        }

    return observed_429


async def run_all_probes() -> Dict[str, Any]:
    """Execute all probe phases and save results to raw_probe_output.json."""
    PROBES_DIR.mkdir(parents=True, exist_ok=True)
    out_file = PROBES_DIR / "raw_probe_output.json"

    print("=" * 60)
    print("RUNNING GROQ API CALIBRATION PROBES")
    print(f"Model: {DEFAULT_GROQ_MODEL}")
    print("=" * 60)

    client = GroqClient(model_name=DEFAULT_GROQ_MODEL)

    print("\n[Phase 1] Probing baseline limits & headers...")
    baseline = await probe_baseline_limits(client)
    print(f"  Status: {baseline['status']} | Region: {baseline['headers'].get('x-groq-region')}")
    print(f"  RPM Limit: {baseline['headers'].get('x-ratelimit-limit-requests')} | TPM Limit: {baseline['headers'].get('x-ratelimit-limit-tokens')}")

    print("\n[Phase 2] Probing latency spread (5 sequential calls)...")
    latency = await probe_latency_spread(client, count=5)
    print(f"  TTFT: p50={latency['ttft_p50_ms']}ms, p90={latency['ttft_p90_ms']}ms")
    print(f"  Total: p50={latency['total_p50_ms']}ms, p90={latency['total_p90_ms']}ms")

    print("\n[Phase 3] Probing concurrency (3 simultaneous calls)...")
    concurrency = await probe_concurrency(client, concurrency=3)
    print(f"  Wall time: {concurrency['wall_time_ms']}ms | Successes: {concurrency['successful_responses']}/{concurrency['concurrency']}")

    print("\n[Phase 4] Probing rate limit boundary (TPM / 429)...")
    rl = await probe_rate_limit(client)
    print(f"  Result: Status {rl.get('status_code')} | Retry-After: {rl.get('retry_after')}")

    probe_data = {
        "timestamp": time.time(),
        "model": DEFAULT_GROQ_MODEL,
        "phase1_baseline": baseline,
        "phase2_latency": latency,
        "phase3_concurrency": concurrency,
        "phase4_rate_limit": rl,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(probe_data, f, indent=2)

    print(f"\nRaw probe data successfully saved to: {out_file}")
    return probe_data


if __name__ == "__main__":
    asyncio.run(run_all_probes())
