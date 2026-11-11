"""
limiter.py: Zone 2 Traffic Gate enforcing proactive and reactive rate governance.
Implements bounded queueing, request deadline enforcement, dual sliding-window limits
(RPM and TPM with conservative max_tokens estimation), in-flight concurrency caps,
daily budget tracking, and exponential backoff with jitter honoring provider retry-after hints.
"""

import asyncio
from dataclasses import dataclass, field
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from logger import EventRecord, default_event_logger
from model_client import BaseModelClient, ModelResponse, ModelTiming, ModelUsage


@dataclass
class LimiterConfig:
    """Tunable limits and thresholds for the Zone 2 gate."""
    requests_per_minute: int = 1000
    tokens_per_minute: int = 8000
    in_flight_cap: int = 5
    daily_requests_budget: int = 14400
    daily_tokens_budget: int = 500000
    max_queue_depth: int = 20
    default_timeout_seconds: float = 25.0
    max_retries: int = 3
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 8.0


class TrafficGate:
    """
    Central traffic governor managing bounded queue, rate limits,
    daily quota, and retries.
    """

    def __init__(self, config: Optional[LimiterConfig] = None):
        self.config = config or LimiterConfig()
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self.config.in_flight_cap)
        self.request_times: List[float] = []
        self.token_records: List[Tuple[float, int]] = []
        self.daily_requests_used: int = 0
        self.daily_tokens_used: int = 0
        self.current_queue_depth: int = 0

    async def check_sliding_window(self, estimated_tokens: int) -> Optional[float]:
        """
        Check if request fits within the 60-second sliding window for RPM and TPM.
        Returns None if permitted; returns wait duration (seconds) if throttled.
        """
        now = time.time()
        cutoff = now - 60.0

        # Clean old entries
        self.request_times = [t for t in self.request_times if t > cutoff]
        self.token_records = [rec for rec in self.token_records if rec[0] > cutoff]

        current_rpm = len(self.request_times)
        current_tpm = sum(rec[1] for rec in self.token_records)

        if current_rpm >= self.config.requests_per_minute:
            oldest = self.request_times[0] if self.request_times else now
            return max(0.5, 60.0 - (now - oldest))

        if current_tpm + estimated_tokens > self.config.tokens_per_minute:
            oldest = self.token_records[0][0] if self.token_records else now
            return max(0.5, 60.0 - (now - oldest))

        return None

    def record_usage(self, actual_tokens: int) -> None:
        """Update daily counters and sliding window records with actual usage."""
        now = time.time()
        self.request_times.append(now)
        self.token_records.append((now, actual_tokens))
        self.daily_requests_used += 1
        self.daily_tokens_used += actual_tokens

    async def execute_through_gate(
        self,
        client: BaseModelClient,
        messages: List[Dict[str, str]],
        request_id: str,
        max_tokens: int = 512,
        stream: bool = True,
        deadline_seconds: Optional[float] = None,
        validate_before_stream: bool = False,
    ) -> ModelResponse:
        """
        Execute request through all Zone 2 gate checks:
        1. Queue depth check
        2. Daily budget check
        3. Request deadline check
        4. Proactive sliding window limit check
        5. In-flight concurrency semaphore acquire
        6. Client dispatch with exponential backoff & jitter on 429
        """
        start_time = time.time()
        deadline = start_time + (deadline_seconds or self.config.default_timeout_seconds)
        client_source = "real" if "groq" in client.__class__.__name__.lower() else "stand-in"

        # 1. Bounded Queue Check
        async with self._lock:
            if self.current_queue_depth >= self.config.max_queue_depth:
                return ModelResponse(
                    content="",
                    timing=ModelTiming(sent_time=start_time, done_time=time.time()),
                    usage=ModelUsage(),
                    status_code=429,
                    status="rate_limited",
                    error_message=f"Queue capacity exceeded ({self.config.max_queue_depth})",
                    source=client_source,
                )
            self.current_queue_depth += 1

        queue_wait_start = time.time()

        try:
            # 2. Daily Budget Check
            if (self.daily_requests_used >= self.config.daily_requests_budget or
                    self.daily_tokens_used >= self.config.daily_tokens_budget):
                return ModelResponse(
                    content="",
                    timing=ModelTiming(sent_time=start_time, done_time=time.time()),
                    usage=ModelUsage(),
                    status_code=429,
                    status="rate_limited",
                    error_message="Daily API budget exhausted",
                    source=client_source,
                )

            # Estimate tokens conservatively (input words + max_tokens)
            prompt_words = sum(len(m.get("content", "").split()) for m in messages)
            estimated_tokens = prompt_words + max_tokens

            # 3. Proactive Sliding Window Wait
            wait_needed = await self.check_sliding_window(estimated_tokens)
            if wait_needed is not None:
                if time.time() + wait_needed > deadline:
                    return ModelResponse(
                        content="",
                        timing=ModelTiming(sent_time=start_time, done_time=time.time()),
                        usage=ModelUsage(),
                        status_code=408,
                        status="timeout",
                        error_message="Rate limit wait would exceed request deadline",
                        source=client_source,
                    )
                await asyncio.sleep(wait_needed)

            # Log queue wait event
            queue_duration = (time.time() - queue_wait_start) * 1000.0
            default_event_logger.log(EventRecord(
                request_id=request_id,
                step="queue",
                start_time=queue_wait_start,
                end_time=time.time(),
                duration_ms=queue_duration,
                queue_depth=self.current_queue_depth,
            ))

            # 4. In-Flight Concurrency Control & Dispatch Loop
            async with self._semaphore:
                retries = 0
                backoff = self.config.initial_backoff_seconds

                while retries <= self.config.max_retries:
                    # Check deadline before attempt
                    now = time.time()
                    if now >= deadline:
                        return ModelResponse(
                            content="",
                            timing=ModelTiming(sent_time=start_time, done_time=now),
                            usage=ModelUsage(),
                            status_code=408,
                            status="timeout",
                            error_message="Request deadline expired during dispatch",
                            source=client_source,
                        )

                    remaining_time = deadline - now
                    # Call model client
                    resp = await client.complete(
                        messages=messages,
                        stream=stream,
                        max_tokens=max_tokens,
                        validate_before_stream=validate_before_stream,
                        request_id=request_id,
                    )

                    # Reactive handling
                    if resp.status == "ok":
                        # Record actual usage and reconcile
                        async with self._lock:
                            self.record_usage(resp.usage.total_tokens)
                        resp.retries = retries
                        return resp

                    if resp.status == "rate_limited" and retries < self.config.max_retries:
                        # Inspect provider retry-after header
                        hint = resp.headers.get("retry-after")
                        if hint:
                            try:
                                retry_wait = float(hint)
                            except ValueError:
                                retry_wait = backoff
                        else:
                            # Exponential backoff + full jitter
                            retry_wait = backoff * (1.0 + random.uniform(0.1, 0.5))

                        retry_wait = min(retry_wait, self.config.max_backoff_seconds)
                        if time.time() + retry_wait >= deadline:
                            return resp  # No time left to backoff

                        # Log retry event
                        retry_start = time.time()
                        await asyncio.sleep(retry_wait)
                        default_event_logger.log(EventRecord(
                            request_id=request_id,
                            step="retry",
                            start_time=retry_start,
                            end_time=time.time(),
                            duration_ms=(time.time() - retry_start) * 1000.0,
                            retry_count=retries + 1,
                            status="rate_limited",
                            error_message=resp.error_message,
                        ))

                        retries += 1
                        backoff *= 2.0
                        continue

                    # Unrecoverable error or retries exhausted
                    resp.retries = retries
                    return resp

        finally:
            async with self._lock:
                self.current_queue_depth = max(0, self.current_queue_depth - 1)

        return ModelResponse(
            content="",
            timing=ModelTiming(sent_time=start_time, done_time=time.time()),
            usage=ModelUsage(),
            status_code=500,
            status="error",
            error_message="Unexpected loop termination in gate",
            source=client_source,
        )


# Global default gate instance
default_gate = TrafficGate()
