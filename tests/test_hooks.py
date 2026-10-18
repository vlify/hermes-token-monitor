"""Unit tests for token_monitor.hooks (lifecycle integration)."""

import shutil
import tempfile
from pathlib import Path

import pytest

from token_monitor.config import MonitorConfig
from token_monitor.hooks import TokenMonitorHooks


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="test_token_hooks_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def test_hooks_record_and_warn(temp_dir):
    state_file = temp_dir / "state.json"
    prom_file = temp_dir / "metrics.prom"
    cfg = MonitorConfig(
        state_file=str(state_file),
        prom_export_path=str(prom_file),
        limit_total_tokens=1000,
        warning_levels=[0.5, 0.8],
        scope="session",
    )
    hooks = TokenMonitorHooks(cfg)

    # 1. on_session_start
    hooks.on_session_start(session_id="sess-1")

    # 2. post_api_request below threshold
    hooks.on_post_api_request(
        session_id="sess-1",
        usage={"input_tokens": 300, "output_tokens": 100, "total_tokens": 400},
    )
    stats = hooks.ledger.get_session_stats("sess-1")
    assert stats.total_tokens == 400
    assert not hooks.ledger.is_warned("sess-1", 0.5)

    # 3. post_api_request crosses 50%
    hooks.on_post_api_request(
        session_id="sess-1",
        usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    )
    assert hooks.ledger.is_warned("sess-1", 0.5)
    assert not hooks.ledger.is_warned("sess-1", 0.8)

    # 4. Check Prometheus export file was written
    assert prom_file.exists()
    content = prom_file.read_text(encoding="utf-8")
    assert 'hermes_tokens_total{scope="session",session_id="sess-1",type="total"} 550' in content


def test_pre_tool_call_freeze(temp_dir):
    state_file = temp_dir / "state.json"
    cfg = MonitorConfig(
        state_file=str(state_file),
        limit_total_tokens=500,
        freeze_on_limit=True,
        scope="session",
    )
    hooks = TokenMonitorHooks(cfg)

    # Not frozen initially
    abort = hooks.on_pre_tool_call(tool_name="bash", session_id="sess-2")
    assert abort is None

    # Trigger limit
    hooks.on_post_api_request(
        session_id="sess-2",
        usage={"input_tokens": 300, "output_tokens": 250, "total_tokens": 550},
    )
    assert hooks.ledger.is_frozen("sess-2")

    # Pre tool call should now abort
    abort_res = hooks.on_pre_tool_call(tool_name="bash", session_id="sess-2")
    assert abort_res is not None
    assert abort_res.get("abort") == "true"
    assert "Limit reached" in abort_res.get("message", "")
