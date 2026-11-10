"""
logger.py: Structured event and results logging for The Relay.
Records step-level events (queue, model calls, retries, headers, usage) to events.jsonl
and generates strict 12-column results.csv rows. Provides breakdown utilities to derive
waiting, working, and retrying latencies per request without storing secret credentials.
"""

import csv
from dataclasses import asdict, dataclass
import datetime
import json
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional

from config import (
    ALLOWED_ANSWER_GRADES,
    ALLOWED_SOURCES,
    ALLOWED_STATUSES,
    EVENT_LOG_PATH,
    RESULTS_CSV_COLUMNS,
    RESULTS_CSV_PATH,
)

# Sanitize headers to ensure credentials never enter logs (Rule 3)
SENSITIVE_HEADER_KEYS = {"authorization", "x-api-key", "api-key", "groq-api-key"}


def sanitize_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Strip authentication tokens and sensitive headers before logging."""
    return {
        k: ("[REDACTED]" if k.lower() in SENSITIVE_HEADER_KEYS else v)
        for k, v in headers.items()
    }


@dataclass
class EventRecord:
    """Individual lifecycle event in events.jsonl."""
    request_id: str
    step: str  # 'queue', 'model_call', 'retrieval', 'evidence_check', 'retry'
    start_time: float
    end_time: float
    duration_ms: float
    retry_count: int = 0
    queue_depth: int = 0
    headers: Dict[str, str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    status: str = "ok"  # 'ok', 'rate_limited', 'timeout', 'error'
    error_message: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.headers:
            d["headers"] = sanitize_headers(self.headers)
        return d


@dataclass
class ResultRecord:
    """Row specification matching results.csv 12-column schema exactly."""
    request_id: str
    scenario: str
    source: str
    start_time: str
    first_token_ms: float
    total_ms: float
    status: str
    retries: int
    tokens_in: int
    tokens_out: int
    retrieval_setting: str
    answer_grade: str

    def validate(self) -> None:
        """Validate row field values against assignment constants."""
        if self.source not in ALLOWED_SOURCES:
            raise ValueError(f"Invalid source '{self.source}'. Allowed: {ALLOWED_SOURCES}")
        if self.status not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid status '{self.status}'. Allowed: {ALLOWED_STATUSES}")
        if self.answer_grade not in ALLOWED_ANSWER_GRADES:
            raise ValueError(f"Invalid answer_grade '{self.answer_grade}'. Allowed: {ALLOWED_ANSWER_GRADES}")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "request_id": self.request_id,
            "scenario": self.scenario,
            "source": self.source,
            "start_time": self.start_time,
            "first_token_ms": round(self.first_token_ms, 1),
            "total_ms": round(self.total_ms, 1),
            "status": self.status,
            "retries": self.retries,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "retrieval_setting": self.retrieval_setting,
            "answer_grade": self.answer_grade,
        }


class EventLogger:
    """Thread-safe event logger writing to events.jsonl."""

    def __init__(self, log_path: Path = EVENT_LOG_PATH):
        self.log_path = log_path
        self._lock = threading.Lock()

    def log(self, event: EventRecord) -> None:
        record_dict = event.to_dict()
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record_dict, ensure_ascii=False) + "\n")

    def read_events(self, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Read and filter events from the event log file."""
        if not self.log_path.exists():
            return []
        events = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    if request_id is None or item.get("request_id") == request_id:
                        events.append(item)
        return events

    def get_latency_breakdown(self, request_id: str) -> Dict[str, float]:
        """
        Derive waiting, working, and retrying durations (in ms) for a request.
        """
        events = self.read_events(request_id=request_id)
        breakdown = {
            "waiting_ms": 0.0,
            "working_ms": 0.0,
            "retrying_ms": 0.0,
            "total_events_ms": 0.0,
        }
        for ev in events:
            step = ev.get("step", "")
            duration = ev.get("duration_ms", 0.0)
            if step == "queue":
                breakdown["waiting_ms"] += duration
            elif step in ("retry", "backoff"):
                breakdown["retrying_ms"] += duration
            else:
                breakdown["working_ms"] += duration
            breakdown["total_events_ms"] += duration

        for k in breakdown:
            breakdown[k] = round(breakdown[k], 1)
        return breakdown


class ResultsLogger:
    """Thread-safe logger writing rows to results.csv."""

    def __init__(self, csv_path: Path = RESULTS_CSV_PATH):
        self.csv_path = csv_path
        self._lock = threading.Lock()
        self._ensure_header()

    def _ensure_header(self) -> None:
        with self._lock:
            if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
                with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(RESULTS_CSV_COLUMNS)

    def log_result(self, record: ResultRecord) -> None:
        row_dict = record.to_dict()
        with self._lock:
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=RESULTS_CSV_COLUMNS)
                writer.writerow(row_dict)

    def read_results(self) -> List[Dict[str, str]]:
        if not self.csv_path.exists():
            return []
        with open(self.csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)


# Global singleton loggers for easy access
default_event_logger = EventLogger()
default_results_logger = ResultsLogger()

