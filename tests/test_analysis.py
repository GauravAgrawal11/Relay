"""
tests/test_analysis.py: Unit tests for analysis/tables.py module.
Validates recomputation of Table 1, Table 2, and slowest request latency breakdown
from results.csv and events.jsonl.
"""

import pandas as pd
import pytest
from analysis.tables import (
    analyze_slowest_request,
    compute_table_1,
    compute_table_2,
)


def test_compute_table_1():
    """Verify Table 1 generation and column presence."""
    t1 = compute_table_1()
    assert isinstance(t1, pd.DataFrame)
    assert len(t1) >= 4
    expected_cols = ["Scenario", "Total Requests", "Success Rate (%)", "Peak Queue", "TTFT p50 (ms)", "Total p50 (ms)"]
    for col in expected_cols:
        assert col in t1.columns


def test_compute_table_2_cost():
    """Verify Table 2 cost computation and zero-cost free tier reporting."""
    t2 = compute_table_2()
    assert isinstance(t2, pd.DataFrame)
    metrics = dict(zip(t2["Metric"], t2["Value"]))
    assert metrics["Actual Cost on Groq Free Tier"] == "$0.00"
    assert "Estimated Cost per 1,000 Answers" in metrics


def test_analyze_slowest_request():
    """Verify latency dissection of slowest request."""
    slow = analyze_slowest_request()
    assert "request_id" in slow
    assert "waiting_ms" in slow
    assert "working_ms" in slow
    assert "retrying_ms" in slow
    assert "primary_delay_location" in slow

