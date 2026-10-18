"""State preservation, event idempotency and Hermes integration contracts."""
import json
import multiprocessing
from pathlib import Path

import pytest

from token_monitor.config import MonitorConfig
from token_monitor.hooks import TokenMonitorHooks
from token_monitor.ledger import TokenLedger


def _hooks(path, **kwargs):
    return TokenMonitorHooks(MonitorConfig(state_file=str(path), notify_console=False, **kwargs))


def _event(hooks, session="A", request="request-1", amount=100):
    hooks.on_post_api_request(session_id=session, api_request_id=request,
                              usage={"input_tokens": amount, "total_tokens": amount})


@pytest.mark.parametrize("content", ["{broken", "[]", '{"sessions": []}',
    '{"sessions":{"A":{"total_tokens":-1}}}', '{"all_time":{"total_tokens":true}}',
    '{"daily":{"date":"not-a-date","stats":{}}}', '{"session_id": 42}'])
def test_invalid_state_never_replaced_on_read_or_update(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content)
    ledger = TokenLedger(MonitorConfig(state_file=str(path)))
    for operation in (ledger.get_all_time_stats, lambda: ledger.record_usage("A", 1, 0)):
        with pytest.raises(ValueError):
            operation()
        assert path.read_text() == content


def test_getters_leave_missing_or_valid_file_untouched(tmp_path):
    path = tmp_path / "state.json"
    ledger = TokenLedger(MonitorConfig(state_file=str(path)))
    ledger.get_all_time_stats()
    assert not path.exists()
    ledger.record_usage("A", 10, 0)
    before = path.read_bytes()
    ledger.get_session_stats("A")
    ledger.get_daily_stats()
    ledger.is_frozen("A")
    assert path.read_bytes() == before


def test_request_dedup_survives_restart_and_is_session_scoped(tmp_path):
    path = tmp_path / "state.json"
    _event(_hooks(path))
    _event(_hooks(path))
    _event(_hooks(path), session="B")
    hooks = _hooks(path)
    assert hooks.ledger.get_session_stats("A").total_tokens == 100
    assert hooks.ledger.get_session_stats("B").total_tokens == 100
    assert hooks.ledger.get_all_time_stats().total_tokens == 200
    # Missing identities must not deduplicate legitimately equal-sized requests.
    _event(hooks, request="")
    _event(hooks, request="")
    assert hooks.ledger.get_session_stats("A").total_tokens == 300


def _process_events(path):
    hooks = _hooks(path, limit_total_tokens=1000, warning_levels=[0.5])
    for n in range(20):
        _event(hooks, request=f"shared-{n}")


def test_concurrent_processes_dedup_and_warning_transition(tmp_path):
    path = tmp_path / "state.json"
    ctx = multiprocessing.get_context("fork")
    processes = [ctx.Process(target=_process_events, args=(path,)) for _ in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    ledger = _hooks(path).ledger
    assert ledger.get_session_stats("A").request_count == 20
    assert ledger.get_all_time_stats().total_tokens == 2000
    assert ledger.is_warned("A", 0.5)


def test_legacy_fields_freeze_and_warning_history_preserved(tmp_path):
    path = tmp_path / "state.json"
    record = dict(input_tokens=50, output_tokens=10, total_tokens=60, request_count=1,
                  cache_read_tokens=40, reasoning_tokens=5, custom_tag="preserve")
    old = dict(day="2026-10-18", session_id="A", session=dict(record, frozen=True),
               daily=record, all_time=record, global_frozen=True,
               global_freeze_reason="legacy freeze",
               warned_keys=["session:A:2026-10-18:total:0.5"], marker="preserve")
    path.write_text(json.dumps(old))
    hooks = _hooks(path, limit_total_tokens=100, warning_levels=[0.5])
    assert hooks.ledger.get_session_stats("A").total_tokens == 60
    assert hooks.ledger.is_frozen("A")
    assert hooks.ledger.is_warned("A", 0.5)
    assert _hooks(path, scope="all_time").ledger.is_frozen("A")
    _event(hooks, amount=1)
    new = json.loads(path.read_text())
    assert new["all_time"]["cache_read_tokens"] == 40
    assert new["all_time"]["reasoning_tokens"] == 5
    assert new["sessions"]["A"]["custom_tag"] == "preserve"
    assert new["marker"] == "preserve"
    assert new["warned_keys"] == old["warned_keys"]
    assert new["session"]["total_tokens"] == 61
    hooks.ledger.set_frozen("A", False)
    assert not _hooks(path).ledger.is_frozen("A")


def test_real_hermes_gate_consumes_freeze_and_footer_is_session_isolated(tmp_path, monkeypatch):
    from hermes_cli import plugins
    import token_monitor
    hooks = _hooks(tmp_path / "state.json", limit_total_tokens=100, freeze_on_limit=True)
    monkeypatch.setattr(token_monitor, "_HOOKS_INSTANCE", hooks)
    manager = plugins.PluginManager()
    monkeypatch.setattr(plugins, "_plugin_manager", manager)
    ctx = plugins.PluginContext(plugins.PluginManifest(name="tokenwatch"), manager)
    token_monitor.register(ctx)
    _event(hooks, amount=100)
    assert plugins.get_pre_tool_call_block_message("terminal", {}, session_id="A")
    assert plugins.get_pre_tool_call_block_message("terminal", {}, session_id="B") is None
    footers = manager.invoke_hook("transform_llm_output", response_text="Done", session_id="A")
    assert any(isinstance(text, str) and "Token:" in text for text in footers)
    assert not any(isinstance(text, str) for text in manager.invoke_hook(
        "transform_llm_output", response_text="Done", session_id="B"))
    assert "Done" in next(text for text in footers if isinstance(text, str))
    assert hooks.on_transform_llm_output(response_text=footers[0], session_id="A") is None


def test_freeze_disabled_and_footer_off_do_not_block_or_transform(tmp_path):
    hooks = _hooks(tmp_path / "state.json", footer_mode="off")
    hooks.ledger.set_frozen("A", True, reason="old freeze")
    assert hooks.on_pre_tool_call(session_id="A") is None
    assert hooks.on_transform_llm_output(response_text="Done", session_id="A") is None


def test_usage_metadata_and_daily_rollover(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    hooks = _hooks(path, scope="daily", limit_total_tokens=100, freeze_on_limit=True)
    monkeypatch.setattr(TokenLedger, "_today", staticmethod(lambda: "2026-10-18"))
    hooks.on_post_api_request(session_id="A", api_request_id="day1", usage=dict(
        input_tokens=80, output_tokens=20, total_tokens=100, cache_read_tokens=60,
        reasoning_tokens=10))
    assert hooks.ledger.is_frozen("A")
    monkeypatch.setattr(TokenLedger, "_today", staticmethod(lambda: "2026-10-19"))
    assert not hooks.ledger.is_frozen("A")
    _event(hooks, request="day2", amount=1)
    assert hooks.ledger.get_daily_stats().total_tokens == 1
    assert hooks.ledger.get_all_time_stats().total_tokens == 101
    assert hooks.ledger.get_session_stats("A").cache_read_tokens == 60


def test_config_environment_overrides_and_profile_home(tmp_path, monkeypatch):
    from token_monitor.config import load_config
    from hermes_cli import config as hermes_config
    home = tmp_path / "profile"
    (home / "tokenwatch").mkdir(parents=True)
    legacy = home / "tokenwatch/state.json"
    legacy.write_text("{}")
    monkeypatch.delenv("HERMES_TOKEN_MONITOR_STATE")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(hermes_config, "load_config_readonly", lambda: {})
    assert Path(load_config().state_file) == legacy
    cfg = tmp_path / "monitor.json"
    cfg.write_text(json.dumps(dict(footer_mode="off", limit_total_tokens=123)))
    monkeypatch.setenv("HERMES_TOKEN_MONITOR_CONFIG", str(cfg))
    override = tmp_path / "explicit.json"
    monkeypatch.setenv("HERMES_TOKEN_MONITOR_STATE", str(override))
    config = load_config()
    assert config.state_file == str(override)
    assert config.limit_total_tokens == 123
    assert config.footer_mode == "off"


def test_profile_factories_keep_separate_ledgers(tmp_path, monkeypatch):
    import token_monitor
    from hermes_cli import config as hermes_config
    monkeypatch.delenv("HERMES_TOKEN_MONITOR_STATE")
    monkeypatch.setattr(hermes_config, "load_config_readonly", lambda: {})
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile-A"))
    first = token_monitor.get_hooks()
    _event(first)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile-B"))
    second = token_monitor.get_hooks()
    assert second is not first
    assert second.ledger.state_path != first.ledger.state_path
    assert second.ledger.get_all_time_stats().total_tokens == 0


def test_warning_transition_committed_once_across_threads(tmp_path, caplog):
    import threading
    hooks = _hooks(tmp_path / "state.json", limit_total_tokens=20, warning_levels=[0.5])
    hooks.config.notify_console = True
    threads = [threading.Thread(target=_event, args=(hooks, "A", f"request-{n}", 1))
               for n in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert hooks.ledger.get_session_stats("A").total_tokens == 20
    assert len([record for record in caplog.records if record.name == "hermes.plugin.token_monitor"]) == 1
