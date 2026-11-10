"""
server.py: FastAPI stand-in model service calibrated against real Groq measurements.
Simulates TTFT latency spread, concurrent load degradation, token streaming,
and HTTP 429 rate-limit refusals using parameters loaded strictly from profile.json.
Provides an identical API interface to Groq for offline evaluation and load testing.
"""

import asyncio
import json
from pathlib import Path
import random
import time
from typing import Any, AsyncGenerator, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

from config import PROFILE_PATH

# Load calibrated profile parameters
def load_profile() -> Dict[str, Any]:
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(f"profile.json not found at {PROFILE_PATH}")
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


profile = load_profile()
MODEL_NAME = profile.get("model_name", "openai/gpt-oss-120b")
RPM_LIMIT = profile["rate_limits"]["requests_per_minute"]
TPM_LIMIT = profile["rate_limits"]["tokens_per_minute"]
RESET_TOKENS_SEC = profile["rate_limits"]["reset_tokens_seconds"]
TTFT_P50_MS = profile["latency_distribution_ms"]["ttft_p50_ms"]
TTFT_P90_MS = profile["latency_distribution_ms"]["ttft_p90_ms"]
INTER_TOKEN_MS = profile["latency_distribution_ms"]["inter_token_delay_ms"]
SLOWDOWN_FACTOR = profile["latency_distribution_ms"]["concurrency_slowdown_factor"]
FAILURE_RATE = profile["latency_distribution_ms"]["simulated_failure_rate"]


class RateLimitTracker:
    """Sliding-window tracker for RPM and TPM simulation."""

    def __init__(self, rpm: int, tpm: int):
        self.rpm = rpm
        self.tpm = tpm
        self.request_times: List[float] = []
        self.token_records: List[tuple] = []  # (timestamp, token_count)
        self.active_requests: int = 0
        self._lock = asyncio.Lock()

    async def acquire(self, estimated_tokens: int) -> Optional[float]:
        """
        Check sliding-window capacity.
        Returns None if permitted; returns retry_after seconds if rate-limited.
        """
        async with self._lock:
            now = time.time()
            cutoff = now - 60.0

            # Prune records older than 60 seconds
            self.request_times = [t for t in self.request_times if t > cutoff]
            self.token_records = [rec for rec in self.token_records if rec[0] > cutoff]

            current_tpm = sum(rec[1] for rec in self.token_records)
            current_rpm = len(self.request_times)

            if current_rpm >= self.rpm:
                return round(max(1.0, 60.0 - (now - self.request_times[0])), 2)

            if current_tpm + estimated_tokens > self.tpm:
                oldest = self.token_records[0][0] if self.token_records else now
                return round(max(RESET_TOKENS_SEC, 60.0 - (now - oldest)), 2)

            # Record accepted request
            self.request_times.append(now)
            self.token_records.append((now, estimated_tokens))
            self.active_requests += 1
            return None

    async def release(self) -> None:
        async with self._lock:
            self.active_requests = max(0, self.active_requests - 1)


tracker = RateLimitTracker(rpm=RPM_LIMIT, tpm=TPM_LIMIT)
app = FastAPI(title="The Relay Stand-in Model Service", version="1.0.0")


def generate_mock_answer(messages: List[Dict[str, str]]) -> str:
    """Synthesize a realistic academic response citing retrieved tags if provided."""
    user_text = ""
    for m in messages:
        if m.get("role") == "user":
            user_text += " " + m.get("content", "")

    # Check for evidence tags in prompt context
    tags = ["attention v7 s3.2.1 p.4", "moshi v1 s3.3 p.8", "dynamic_chunking v2 s2.2 p.6"]
    tag_to_cite = tags[0]
    for t in tags:
        if t.split()[0] in user_text.lower():
            tag_to_cite = t
            break

    return (
        f"Based on the provided research passages [{tag_to_cite}], "
        f"the architecture operates by transforming representations through learned projections. "
        f"Experimental results confirm that the mechanism satisfies design criteria without external heuristics."
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)
    max_tokens = body.get("max_tokens", 512)

    prompt_words = sum(len(m.get("content", "").split()) for m in messages)
    estimated_tokens = prompt_words + max_tokens

    # 1. Rate-limit check
    retry_after = await tracker.acquire(estimated_tokens)
    if retry_after is not None:
        headers = {
            "retry-after": str(retry_after),
            "x-ratelimit-limit-requests": str(RPM_LIMIT),
            "x-ratelimit-limit-tokens": str(TPM_LIMIT),
            "x-ratelimit-remaining-requests": "0",
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-reset-tokens": f"{retry_after}s",
        }
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "message": f"Rate limit reached for model {MODEL_NAME}",
                    "type": "tokens",
                    "code": "rate_limit_exceeded",
                }
            },
            headers=headers,
        )

    # 2. Simulated failure check
    if random.random() < FAILURE_RATE:
        await tracker.release()
        return JSONResponse(
            status_code=500,
            content={"error": {"message": "Simulated internal server error", "type": "server_error"}},
        )

    # 3. Calculate calibrated delay
    active = tracker.active_requests
    concurrency_multiplier = SLOWDOWN_FACTOR ** max(0, active - 1)
    # Log-normal distribution around p50
    base_ttft = random.uniform(TTFT_P50_MS * 0.8, TTFT_P90_MS * 1.05)
    actual_ttft_ms = base_ttft * concurrency_multiplier

    answer_text = generate_mock_answer(messages)
    words = answer_text.split()
    completion_tokens = len(words) * 2  # approximate tokens

    headers = {
        "x-ratelimit-limit-requests": str(RPM_LIMIT),
        "x-ratelimit-limit-tokens": str(TPM_LIMIT),
        "x-ratelimit-remaining-requests": str(max(0, RPM_LIMIT - len(tracker.request_times))),
        "x-ratelimit-remaining-tokens": str(max(0, TPM_LIMIT - sum(r[1] for r in tracker.token_records))),
        "x-ratelimit-reset-tokens": f"{RESET_TOKENS_SEC}s",
    }

    if stream:
        async def event_generator() -> AsyncGenerator[str, None]:
            try:
                # Sleep for TTFT
                await asyncio.sleep(actual_ttft_ms / 1000.0)
                # Stream word chunks
                for w in words:
                    chunk = {
                        "id": "chatcmpl_standin",
                        "object": "chat.completion.chunk",
                        "model": MODEL_NAME,
                        "choices": [{"delta": {"content": w + " "}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                    await asyncio.sleep(INTER_TOKEN_MS / 1000.0)

                # Final usage chunk
                done_chunk = {
                    "id": "chatcmpl_standin",
                    "object": "chat.completion.chunk",
                    "model": MODEL_NAME,
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": prompt_words,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_words + completion_tokens,
                    },
                }
                yield f"data: {json.dumps(done_chunk)}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                await tracker.release()

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers=headers,
        )
    else:
        try:
            await asyncio.sleep((actual_ttft_ms + (len(words) * INTER_TOKEN_MS)) / 1000.0)
            return JSONResponse(
                status_code=200,
                content={
                    "id": "chatcmpl_standin",
                    "object": "chat.completion",
                    "model": MODEL_NAME,
                    "choices": [{"message": {"role": "assistant", "content": answer_text}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": prompt_words,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_words + completion_tokens,
                    },
                },
                headers=headers,
            )
        finally:
            await tracker.release()


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME, "active_requests": tracker.active_requests}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)

