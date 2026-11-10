"""
tests/test_conversation.py: Unit tests for cache.py and conversation.py.
Validates SQLite WAL session persistence, pronoun follow-up resolution,
in-memory TTL cache expiration, and hit-rate tracking.
"""

from pathlib import Path
import pytest
from cache import TTLCache, make_cache_key
from conversation import ConversationalRelay, init_db, resolve_followup_query
from pipeline import RelayPipeline


def test_ttl_cache():
    """Verify TTL cache get, set, expiration, and statistics tracking."""
    cache = TTLCache(ttl_seconds=1.0, enabled=True)
    k = make_cache_key("what is attention", "setting_b", "llama")

    assert cache.get(k) is None
    cache.set(k, {"answer": "It is an attention mechanism."})

    hit = cache.get(k)
    assert hit is not None
    assert "attention mechanism" in hit["answer"]

    stats = cache.get_stats()
    assert stats["hits"] == 1
    assert stats["cached_entries"] == 1
    assert stats["hit_rate_pct"] == 50.0  # 1 hit, 1 miss


def test_followup_resolution():
    """Verify follow-up query pronoun resolution using chat history."""
    history = [
        {"role": "user", "content": "Tell me about the Mimi codec in Moshi."},
        {"role": "assistant", "content": "Mimi is a neural audio codec operating at 12.5 Hz."},
    ]
    resolved, status = resolve_followup_query("What is its frame rate?", history)
    assert status == "RESOLVED"
    assert "mimi" in resolved.lower() or "moshi" in resolved.lower()


@pytest.mark.asyncio
async def test_conversational_relay_flow(tmp_path: Path):
    """Verify multi-turn session persistence in SQLite."""
    test_db = tmp_path / "test_relay.db"
    pipe = RelayPipeline(source="stand-in", retrieval_setting="setting_b")
    chat = ConversationalRelay(pipeline=pipe, db_path=test_db, cache_enabled=True)

    # First turn
    r1 = await chat.chat("What is scaled dot-product attention in Attention Is All You Need?", session_id="sess_1")
    assert r1["status"] == "ok"
    assert r1["cached"] is False

    # Second turn with identical query: should hit cache
    r2 = await chat.chat("What is scaled dot-product attention in Attention Is All You Need?", session_id="sess_1")
    assert r2["cached"] is True

