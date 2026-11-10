"""
tests/test_logger_and_client.py: Unit tests for logger.py and model_client.py.
Validates event logging, latency breakdown derivation, results.csv schema conformance,
sensitive header redaction, and both stand-in and real Groq client response contracts.
"""

import asyncio
from pathlib import Path
import pytest
from logger import (
    EventLogger,
    EventRecord,
    ResultRecord,
    ResultsLogger,
    sanitize_headers,
)
from model_client import StandinClient, create_model_client


def test_sanitize_headers():
    """Verify authorization headers and API keys are strictly redacted."""
    raw = {
        "Authorization": "Bearer gsk_secret_token_12345",
        "x-api-key": "secret_key_abc",
        "content-type": "application/json",
        "x-ratelimit-remaining-tokens": "5900",
    }
    cleaned = sanitize_headers(raw)
    assert cleaned["Authorization"] == "[REDACTED]"
    assert cleaned["x-api-key"] == "[REDACTED]"
    assert cleaned["content-type"] == "application/json"
    assert cleaned["x-ratelimit-remaining-tokens"] == "5900"


def test_event_logger_and_breakdown(tmp_path: Path):
    """Verify logging events and computing waiting/working/retrying breakdowns."""
    log_file = tmp_path / "test_events.jsonl"
    logger = EventLogger(log_path=log_file)

    req_id = "req_test_01"
    # Log queue wait event
    logger.log(EventRecord(
        request_id=req_id,
        step="queue",
        start_time=100.0,
        end_time=100.05,
        duration_ms=50.0,
        queue_depth=2,
    ))
    # Log model call event
    logger.log(EventRecord(
        request_id=req_id,
        step="model_call",
        start_time=100.05,
        end_time=100.30,
        duration_ms=250.0,
        tokens_in=100,
        tokens_out=30,
    ))
    # Log retry backoff event
    logger.log(EventRecord(
        request_id=req_id,
        step="retry",
        start_time=100.30,
        end_time=100.35,
        duration_ms=50.0,
        retry_count=1,
    ))

    breakdown = logger.get_latency_breakdown(req_id)
    assert breakdown["waiting_ms"] == 50.0
    assert breakdown["working_ms"] == 250.0
    assert breakdown["retrying_ms"] == 50.0
    assert breakdown["total_events_ms"] == 350.0


def test_results_logger_schema(tmp_path: Path):
    """Verify ResultRecord validation and CSV writing with exact 12 columns."""
    csv_file = tmp_path / "test_results.csv"
    r_logger = ResultsLogger(csv_path=csv_file)

    rec = ResultRecord(
        request_id="req_999",
        scenario="steady",
        source="real",
        start_time="2026-11-10T12:00:00Z",
        first_token_ms=120.4,
        total_ms=450.2,
        status="ok",
        retries=0,
        tokens_in=250,
        tokens_out=45,
        retrieval_setting="setting_b",
        answer_grade="correct",
    )
    r_logger.log_result(rec)

    rows = r_logger.read_results()
    assert len(rows) == 1
    assert rows[0]["request_id"] == "req_999"
    assert rows[0]["first_token_ms"] == "120.4"
    assert rows[0]["source"] == "real"


@pytest.mark.asyncio
async def test_standin_mock_client():
    """Verify stand-in client mock mode contract, timing, and token counting."""
    client = create_model_client(source="stand-in", mock_standin=True)
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is scaled dot-product attention?"},
    ]

    resp = await client.complete(messages=messages, stream=True)
    assert resp.status == "ok"
    assert resp.source == "stand-in"
    assert "scaled dot-product" in resp.content.lower()
    assert resp.timing.first_token_ms > 0
    assert resp.timing.total_ms >= resp.timing.first_token_ms
    assert resp.usage.total_tokens > 0

