"""Unit tests for token_monitor.ledger (TDD RED-GREEN)."""

import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from token_monitor.config import MonitorConfig
from token_monitor.ledger import TokenLedger


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="test_token_monitor_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def test_session_isolation(temp_dir):
    """A100 -> B200 -> A50 must yield A=150, B=200, all_time=350."""
    state_file = temp_dir / "state.json"
    cfg = MonitorConfig(state_file=str(state_file), scope="session")
    ledger = TokenLedger(cfg)

    # 1. A + 100
    ledger.record_usage(session_id="session-A", input_tokens=80, output_tokens=20)
    # 2. B + 200
    ledger.record_usage(session_id="session-B", input_tokens=150, output_tokens=50)
    # 3. A + 50
    ledger.record_usage(session_id="session-A", input_tokens=40, output_tokens=10)

    stats_a = ledger.get_session_stats("session-A")
    assert stats_a.input_tokens == 120
    assert stats_a.output_tokens == 30
    assert stats_a.total_tokens == 150
    assert stats_a.request_count == 2

    stats_b = ledger.get_session_stats("session-B")
    assert stats_b.input_tokens == 150
    assert stats_b.output_tokens == 50
    assert stats_b.total_tokens == 200
    assert stats_b.request_count == 1

    stats_all = ledger.get_all_time_stats()
    assert stats_all.total_tokens == 350
    assert stats_all.request_count == 3


def test_concurrent_threads_writing(temp_dir):
    """Concurrent writes must not lose counts thanks to transaction locking."""
    state_file = temp_dir / "state.json"
    cfg = MonitorConfig(state_file=str(state_file), scope="session")
    ledger = TokenLedger(cfg)

    threads = []
    num_threads = 10
    tokens_per_thread = 20

    def worker():
        for _ in range(5):
            ledger.record_usage(
                session_id="shared-session",
                input_tokens=tokens_per_thread // 2,
                output_tokens=tokens_per_thread // 2,
            )

    for _ in range(num_threads):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    stats = ledger.get_session_stats("shared-session")
    expected_total = num_threads * 5 * tokens_per_thread
    assert stats.total_tokens == expected_total
    assert stats.request_count == num_threads * 5


def test_daily_rollover(temp_dir):
    """Daily stats should reset on new date, but sessions and all_time persist."""
    state_file = temp_dir / "state.json"
    cfg = MonitorConfig(state_file=str(state_file), scope="daily")
    ledger = TokenLedger(cfg)

    # Record under fake date
    ledger.record_usage(
        session_id="s1",
        input_tokens=100,
        output_tokens=50,
        current_date="2026-10-17",
    )
    daily_stats = ledger.get_daily_stats(current_date="2026-10-17")
    assert daily_stats.total_tokens == 150

    # Record under today's date
    ledger.record_usage(
        session_id="s1",
        input_tokens=20,
        output_tokens=10,
        current_date="2026-10-18",
    )
    daily_today = ledger.get_daily_stats()
    assert daily_today.total_tokens == 30  # Rolled over!
    assert daily_today.request_count == 1

    all_time = ledger.get_all_time_stats()
    assert all_time.total_tokens == 180  # All-time keeps both days!


def test_warned_levels_and_freeze(temp_dir):
    """Test tracking warned threshold levels and freeze state."""
    state_file = temp_dir / "state.json"
    cfg = MonitorConfig(state_file=str(state_file), scope="session")
    ledger = TokenLedger(cfg)

    # Initial state
    assert not ledger.is_warned("s1", 0.5)
    ledger.mark_warned("s1", 0.5)
    assert ledger.is_warned("s1", 0.5)
    assert not ledger.is_warned("s1", 0.8)

    # Freeze state
    assert not ledger.is_frozen("s1")
    ledger.set_frozen("s1", True, reason="limit exceeded")
    assert ledger.is_frozen("s1")
    assert ledger.get_freeze_reason("s1") == "limit exceeded"

    # Resetting freeze
    ledger.set_frozen("s1", False)
    assert not ledger.is_frozen("s1")
