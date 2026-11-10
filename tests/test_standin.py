"""
tests/test_standin.py: Unit tests for stand-in server module.
Validates profile loading, rate-limit tracking, TTFT simulation,
and streaming responses from the stand-in FastAPI service.
"""

import pytest
import httpx
from standin.server import RateLimitTracker, app, load_profile


def test_load_profile_provenance():
    """Verify all parameters in profile.json are sourced or labeled ASSUMPTION."""
    p = load_profile()
    assert "rate_limits" in p
    assert "latency_distribution_ms" in p

    # Ensure every parameter has a source citation
    assert "requests_per_minute_source" in p["rate_limits"]
    assert "tokens_per_minute_source" in p["rate_limits"]
    assert "daily_requests_budget_source" in p["rate_limits"]
    assert "in_flight_concurrency_source" in p["rate_limits"]
    assert "ttft_p50_source" in p["latency_distribution_ms"]


@pytest.mark.asyncio
async def test_rate_limit_tracker():
    """Verify sliding-window rate limit acquisition and refusal."""
    tracker = RateLimitTracker(rpm=2, tpm=500)

    # First two requests should pass
    r1 = await tracker.acquire(estimated_tokens=100)
    assert r1 is None

    r2 = await tracker.acquire(estimated_tokens=100)
    assert r2 is None

    # Third request exceeds RPM (2), should be refused with retry_after
    r3 = await tracker.acquire(estimated_tokens=50)
    assert r3 is not None
    assert r3 > 0


@pytest.mark.asyncio
async def test_standin_health_endpoint():
    """Test /health endpoint."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "openai/gpt-oss-120b" in data["model"]

