"""Integration and migration test using real historical tokenwatch state."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from token_monitor.config import MonitorConfig
from token_monitor.ledger import TokenLedger

REAL_PROD_STATE = Path("/home/ent/.config/hermes/tokenwatch/state.json")


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="test_token_migration_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def test_real_historical_state_migration(temp_dir):
    """Test loading and incremental recording against real-world production data."""
    if not REAL_PROD_STATE.is_file():
        pytest.skip("Production state.json not present on host")

    # Copy real state into isolated test directory
    target_state = temp_dir / "migrated_state.json"
    shutil.copy(REAL_PROD_STATE, target_state)

    with open(target_state, "r", encoding="utf-8") as f:
        initial_raw = json.load(f)

    cfg = MonitorConfig(state_file=str(target_state), scope="session")
    ledger = TokenLedger(cfg)

    # 1. Assert historical values read correctly
    all_time = ledger.get_all_time_stats()
    assert all_time.total_tokens == initial_raw.get("all_time", {}).get("total_tokens", 0)
    assert all_time.request_count == initial_raw.get("all_time", {}).get("request_count", 0)

    today_str = initial_raw.get("day", "2026-10-18")
    daily = ledger.get_daily_stats(current_date=today_str)
    assert daily.total_tokens == initial_raw.get("daily", {}).get("total_tokens", 0)
    assert daily.request_count == initial_raw.get("daily", {}).get("request_count", 0)

    sessions = initial_raw.get("sessions", {})
    assert len(sessions) > 0
    test_session_id = next(iter(sessions.keys()))
    sess = ledger.get_session_stats(test_session_id)
    assert sess.total_tokens == sessions[test_session_id].get("total_tokens", 0)
    assert sess.request_count == sessions[test_session_id].get("request_count", 0)

    # 2. Increment usage on the historical session
    ledger.record_usage(
        session_id=test_session_id,
        input_tokens=1000,
        output_tokens=200,
        current_date=today_str,
    )

    # 3. Verify clean update
    updated_sess = ledger.get_session_stats(test_session_id)
    assert updated_sess.total_tokens == sessions[test_session_id].get("total_tokens", 0) + 1200
    assert updated_sess.request_count == sessions[test_session_id].get("request_count", 0) + 1

    updated_all = ledger.get_all_time_stats()
    assert updated_all.total_tokens == initial_raw.get("all_time", {}).get("total_tokens", 0) + 1200
    assert updated_all.request_count == initial_raw.get("all_time", {}).get("request_count", 0) + 1

    updated_daily = ledger.get_daily_stats(current_date=today_str)
    assert updated_daily.total_tokens == initial_raw.get("daily", {}).get("total_tokens", 0) + 1200
    assert updated_daily.request_count == initial_raw.get("daily", {}).get("request_count", 0) + 1

