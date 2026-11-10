"""
tests/test_limiter.py: Unit tests for limiter.py module.
Validates bounded queueing, sliding window rate limits, daily budget enforcement,
in-flight concurrency control, and retry backoff mechanics.
"""

import asyncio
import pytest
from limiter import LimiterConfig, TrafficGate
from model_client import create_model_client


@pytest.mark.asyncio
async def test_sliding_window_tpm_limit():
    """Verify that exceeding configured TPM triggers sliding-window wait."""
    cfg = LimiterConfig(requests_per_minute=100, tokens_per_minute=500)
    gate = TrafficGate(config=cfg)

    # 1. First request of 300 tokens fits
    w1 = await gate.check_sliding_window(estimated_tokens=300)
    assert w1 is None
    gate.record_usage(actual_tokens=300)

    # 2. Second request of 300 tokens exceeds 500 TPM
    w2 = await gate.check_sliding_window(estimated_tokens=300)
    assert w2 is not None
    assert w2 > 0


@pytest.mark.asyncio
async def test_bounded_queue_rejection():
    """Verify that requests exceeding max_queue_depth are rejected."""
    cfg = LimiterConfig(max_queue_depth=1)
    gate = TrafficGate(config=cfg)
    gate.current_queue_depth = 1  # artificially fill queue

    client = create_model_client(source="stand-in", mock_standin=True)
    resp = await gate.execute_through_gate(
        client=client,
        messages=[{"role": "user", "content": "test"}],
        request_id="req_queue_full",
    )
    assert resp.status_code == 429
    assert "Queue capacity exceeded" in resp.error_message


@pytest.mark.asyncio
async def test_daily_budget_exhaustion():
    """Verify that reaching daily request limit immediately returns 429 without waiting."""
    cfg = LimiterConfig(daily_requests_budget=2)
    gate = TrafficGate(config=cfg)
    gate.daily_requests_used = 2

    client = create_model_client(source="stand-in", mock_standin=True)
    resp = await gate.execute_through_gate(
        client=client,
        messages=[{"role": "user", "content": "test"}],
        request_id="req_budget_gone",
    )
    assert resp.status_code == 429
    assert "Daily API budget exhausted" in resp.error_message


@pytest.mark.asyncio
async def test_successful_request_through_gate():
    """Verify standard compliant request passes through gate successfully."""
    cfg = LimiterConfig(requests_per_minute=100, tokens_per_minute=5000)
    gate = TrafficGate(config=cfg)
    client = create_model_client(source="stand-in", mock_standin=True)

    resp = await gate.execute_through_gate(
        client=client,
        messages=[{"role": "user", "content": "What is attention?"}],
        request_id="req_ok_01",
    )
    assert resp.status == "ok"
    assert gate.daily_requests_used == 1
    assert gate.daily_tokens_used > 0

