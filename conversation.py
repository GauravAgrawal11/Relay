"""
conversation.py: Conversational turn manager backed by SQLite in WAL mode.
Maintains session history across turns, resolves pronouns and follow-up references
via deterministic rules, logs resolved queries, integrates in-memory TTL caching,
and enforces single-turn cache-off isolation in evaluation/loadgen mode.
"""

import datetime
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from cache import TTLCache, make_cache_key
from config import DB_PATH
from pipeline import PipelineResult, RelayPipeline


def init_db(db_path: Path = DB_PATH) -> None:
    """Initialize SQLite tables and enable WAL (Write-Ahead Logging) mode."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                resolved_query TEXT
            );
        """)
        conn.commit()


def resolve_followup_query(
    current_query: str,
    recent_history: List[Dict[str, str]],
) -> Tuple[str, str]:
    """
    Resolve pronouns or brief follow-up queries using prior turns.
    Returns (resolved_query, resolution_status): 'RESOLVED', 'UNCHANGED', or 'AMBIGUOUS'.
    """
    q_lower = current_query.strip().lower()
    pronouns = {"it", "its", "they", "them", "their", "this", "that", "these", "those"}
    words = set(re.findall(r"\b\w+\b", q_lower))

    has_pronoun = bool(words & pronouns)
    is_short = len(q_lower.split()) <= 4

    if not has_pronoun and not is_short:
        return current_query, "UNCHANGED"

    if not recent_history:
        if has_pronoun and is_short:
            return current_query, "AMBIGUOUS"
        return current_query, "UNCHANGED"

    # Inspect last turn
    last_user_turn = next((m["content"] for m in reversed(recent_history) if m["role"] == "user"), "")
    last_assistant_turn = next((m["content"] for m in reversed(recent_history) if m["role"] == "assistant"), "")
    combined_prev = f"{last_user_turn} {last_assistant_turn}".lower()

    # Detect active topic
    topic = None
    for kw in ["scaled dot-product", "mimi", "helium", "h-net", "bpe", "dynamic chunking", "bleu", "transformer"]:
        if kw in combined_prev:
            topic = kw
            break

    if topic:
        resolved = f"{current_query} (context: {topic})"
        return resolved, "RESOLVED"
    elif has_pronoun and is_short:
        return current_query, "AMBIGUOUS"

    return current_query, "UNCHANGED"


class ConversationalRelay:
    """Multi-turn conversation coordinator with SQLite history and TTL caching."""

    def __init__(
        self,
        pipeline: RelayPipeline,
        db_path: Path = DB_PATH,
        cache_enabled: bool = False,
    ):
        self.pipeline = pipeline
        self.db_path = db_path
        self.cache = TTLCache(enabled=cache_enabled)
        init_db(self.db_path)

    def _get_recent_messages(self, session_id: str, limit: int = 4) -> List[Dict[str, str]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
            rows = cursor.fetchall()
            return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def _save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        resolved_query: Optional[str] = None,
    ) -> None:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, created_at) VALUES (?, ?)",
                (session_id, now_iso),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp, resolved_query) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, now_iso, resolved_query),
            )
            conn.commit()

    async def chat(
        self,
        user_message: str,
        session_id: str = "default_session",
        scenario: str = "interactive",
    ) -> Dict[str, Any]:
        recent = self._get_recent_messages(session_id)
        resolved_query, status = resolve_followup_query(user_message, recent)

        if status == "AMBIGUOUS":
            clarification = "Could you please clarify which paper or concept you are referring to?"
            self._save_message(session_id, "user", user_message, resolved_query)
            self._save_message(session_id, "assistant", clarification)
            return {
                "answer": clarification,
                "resolved_query": resolved_query,
                "cached": False,
                "status": "ambiguous_clarification",
            }

        # Check answer cache
        cache_key = make_cache_key(
            normalized_query=resolved_query,
            retrieval_setting=self.pipeline.retrieval_setting,
            model_name=self.pipeline.model_name,
        )
        cached_val = self.cache.get(cache_key)
        if cached_val is not None:
            self._save_message(session_id, "user", user_message, resolved_query)
            self._save_message(session_id, "assistant", cached_val["answer"])
            return {**cached_val, "cached": True}

        # Execute through pipeline
        result: PipelineResult = await self.pipeline.answer(
            question=resolved_query,
            scenario=scenario,
        )

        # Honest UI: only supply retrieved provenance tags when citations are valid
        retrieved_tags = [p.tag for p in result.retrieved_passages] if result.citations_valid else []

        response_dict = {
            "answer": result.answer,
            "status": result.status,
            "resolved_query": resolved_query,
            "cached": False,
            "timing_first_token_ms": result.timing.first_token_ms,
            "timing_total_ms": result.timing.total_ms,
            "citations_valid": result.citations_valid,
            "retrieved_tags": retrieved_tags,
        }

        # Cache successes only
        if result.status == "ok" and result.citations_valid:
            self.cache.set(cache_key, response_dict)

        # Save to SQLite
        self._save_message(session_id, "user", user_message, resolved_query)
        self._save_message(session_id, "assistant", result.answer, resolved_query)

        return response_dict

