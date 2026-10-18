"""Migration coverage uses synthetic historical records, never production state."""
import json

from token_monitor.config import MonitorConfig
from token_monitor.ledger import TokenLedger


def test_historical_state_migration(tmp_path):
    target = tmp_path / "state.json"
    record = dict(input_tokens=100, output_tokens=20, total_tokens=120, request_count=2,
                  cache_read_tokens=60, cache_write_tokens=10, reasoning_tokens=5)
    initial = dict(day="2026-10-18", session_id="existing", session=record,
                   sessions={"existing": record}, daily=record, all_time=record,
                   warned_keys=[])
    target.write_text(json.dumps(initial))
    before = target.read_bytes()
    ledger = TokenLedger(MonitorConfig(state_file=str(target)))
    assert ledger.get_all_time_stats().total_tokens == 120
    assert ledger.get_daily_stats(current_date="2026-10-18").total_tokens == 120
    assert ledger.get_session_stats("existing").request_count == 2
    assert target.read_bytes() == before  # migration only persists on a real mutation
    ledger.record_usage("existing", 1000, 200, current_date="2026-10-18")
    updated = json.loads(target.read_text())
    for stats in (updated["all_time"], updated["daily"]["stats"], updated["sessions"]["existing"]):
        assert stats["total_tokens"] == 1320
        assert stats["request_count"] == 3
        assert stats["cache_read_tokens"] == 60
        assert stats["cache_write_tokens"] == 10
        assert stats["reasoning_tokens"] == 5
