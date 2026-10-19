"""User-facing contracts for installation, profiles, usage and quotas."""
import json
from types import SimpleNamespace

import pytest
from token_monitor.config import MonitorConfig, load_config
from token_monitor.hooks import TokenMonitorHooks


def test_settings_override_legacy_and_relative_paths_follow_profile(tmp_path, monkeypatch):
    from hermes_cli import config
    monkeypatch.delenv("HERMES_TOKEN_MONITOR_STATE")
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"plugins": {"entries": {
        "tokenwatch": {"limit_total_tokens": 900, "config": {"limit_total_tokens": 800},
                       "settings": {"limit_total_tokens": 700, "state_file": "meter/state.json",
                                    "prom_export_path": "meter/metrics.prom"}}
    }}})
    cfg = load_config()
    home = tmp_path / "hermes"
    assert cfg.limit_total_tokens == 700
    assert cfg.state_file == str(home / "meter/state.json")
    assert cfg.prom_export_path == str(home / "meter/metrics.prom")


@pytest.mark.parametrize("data", [
    {"freeze_on_limit": "false"}, {"limit_total_tokens": -1},
    {"limit_input_tokens": 1.5}, {"scope": "weekly"},
    {"warning_levels": [float("nan")]}, {"warning_levels": [80]},
])
def test_invalid_config_fails_with_actionable_message(data):
    with pytest.raises(ValueError, match=next(iter(data))):
        MonitorConfig.from_dict(data)


def test_canonical_usage_counts_cached_input_once(tmp_path):
    hooks = TokenMonitorHooks(MonitorConfig(state_file=str(tmp_path / "state.json")))
    hooks.on_post_api_request(session_id="a", usage={
        "input_tokens": 20, "prompt_tokens": 100, "output_tokens": 10,
        "cache_read_tokens": 70, "cache_write_tokens": 10, "reasoning_tokens": 4,
        "total_tokens": 110,
    })
    record = hooks.ledger.get_session_stats("a")
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (100, 10, 110)
    assert (record.cache_read_tokens, record.cache_write_tokens, record.reasoning_tokens) == (70, 10, 4)


def test_sdk_usage_preserves_nested_cache_and_reasoning(tmp_path):
    hooks = TokenMonitorHooks(MonitorConfig(state_file=str(tmp_path / "state.json")))
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20,
        total_tokens=120, prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=15)))
    hooks.on_post_api_request(session_id="a", response=response)
    record = hooks.ledger.get_session_stats("a")
    assert (record.cache_read_tokens, record.reasoning_tokens) == (80, 15)
    assert record.total_tokens == 120


@pytest.mark.parametrize("metric", ["input", "output"])
def test_individual_quota_warns_freezes_and_renders_footer(tmp_path, metric):
    cfg = MonitorConfig(state_file=str(tmp_path / "state.json"), limit_total_tokens=0,
                        freeze_on_limit=True, **{f"limit_{metric}_tokens": 100})
    hooks = TokenMonitorHooks(cfg)
    hooks.on_post_api_request(session_id="a", usage={f"{metric}_tokens": 80})
    assert hooks.ledger.is_warned("a", 0.8)
    assert metric in hooks.on_transform_llm_output(response_text="Done", session_id="a")
    hooks.on_post_api_request(session_id="a", usage={f"{metric}_tokens": 20})
    assert hooks.on_pre_tool_call(session_id="a")["action"] == "block"


def test_exporter_escapes_session_labels(tmp_path):
    cfg = MonitorConfig(state_file=str(tmp_path / "state.json"),
                        prom_export_path=str(tmp_path / "metrics.prom"))
    hooks = TokenMonitorHooks(cfg)
    hooks.on_post_api_request(session_id='a"\\\nb', usage={"input_tokens": 1})
    content = (tmp_path / "metrics.prom").read_text()
    assert 'session_id="a\\"\\\\\\nb"' in content


def test_profile_override_isolates_settings_without_environment_change(tmp_path, monkeypatch):
    import token_monitor
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    monkeypatch.delenv("HERMES_TOKEN_MONITOR_STATE")
    instances = []
    for name, limit in [("a", 100), ("b", 200)]:
        home = tmp_path / name
        home.mkdir()
        (home / "config.yaml").write_text(json.dumps({"plugins": {"entries": {
            "tokenwatch": {"settings": {"limit_total_tokens": limit}}
        }}}))
        token = set_hermes_home_override(home)
        try:
            hooks = token_monitor.get_hooks()
            assert hooks.config.limit_total_tokens == limit
            assert hooks.ledger.state_path == home / "tokenwatch/state.json"
            instances.append(hooks)
        finally:
            reset_hermes_home_override(token)
    assert instances[0] is not instances[1]


def test_live_settings_reload_on_next_hook(tmp_path, monkeypatch):
    import token_monitor
    from hermes_cli import config
    settings = {"limit_total_tokens": 100}
    monkeypatch.setattr(config, "load_config_readonly", lambda: {
        "plugins": {"entries": {"tokenwatch": {"settings": settings}}}})
    first = token_monitor.get_hooks()
    first.on_post_api_request(session_id="a", usage={"input_tokens": 60})
    settings["limit_total_tokens"] = 200
    second = token_monitor.get_hooks()
    assert second.config.limit_total_tokens == 200
    assert second.ledger.get_session_stats("a").total_tokens == 60


@pytest.mark.parametrize("enabled", [False, True])
def test_auxiliary_opt_in_through_real_plugin_context(tmp_path, monkeypatch, enabled):
    import token_monitor
    from hermes_cli import config, plugins
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"plugins": {"entries": {
        "tokenwatch": {"settings": {"include_auxiliary_calls": enabled}}
    }}})
    manager = plugins.PluginManager()
    ctx = plugins.PluginContext(plugins.PluginManifest(name="tokenwatch"), manager)
    token_monitor.register(ctx)
    main = {"session_id": "a", "api_request_id": "main", "usage": {"input_tokens": 20}}
    aux = {"session_id": "a", "api_request_id": "aux", "usage": {"prompt_tokens": 30}}
    manager.invoke_hook("post_api_request", **main)
    manager.invoke_hook("post_auxiliary_call", **aux)
    manager.invoke_hook("post_auxiliary_call", **aux)
    assert token_monitor.get_hooks().ledger.get_session_stats("a").total_tokens == (
        50 if enabled else 20)
    assert "tokenwatch" in manager._cli_commands


def test_cli_status_and_explicit_session_unfreeze(tmp_path, monkeypatch, capsys):
    import argparse

    import token_monitor
    from token_monitor.cli import handle_command, setup_parser
    hooks = TokenMonitorHooks(MonitorConfig(state_file=str(tmp_path / "state.json")))
    monkeypatch.setattr(token_monitor, "_HOOKS_INSTANCE", hooks)
    hooks.ledger.record_usage("a", 100, 0)
    hooks.ledger.set_frozen("a", True, "limit")
    parser = argparse.ArgumentParser()
    setup_parser(parser)
    before = hooks.ledger.state_path.read_bytes()
    handle_command(parser.parse_args(["status", "--session", "a"]))
    assert json.loads(capsys.readouterr().out)["usage"]["total_tokens"] == 100
    assert hooks.ledger.state_path.read_bytes() == before
    with pytest.raises(SystemExit, match="--session"):
        handle_command(parser.parse_args(["unfreeze"]))
    assert hooks.ledger.is_frozen("a")
    handle_command(parser.parse_args(["unfreeze", "--session", "a"]))
    assert not hooks.ledger.is_frozen("a")
    assert hooks.ledger.get_session_stats("a").total_tokens == 100


@pytest.mark.parametrize("language,expected,absent", [
    ("en", "plan to wrap up", "建议"), ("zh", "建议规划收尾", "plan to wrap up")])
def test_footer_language(tmp_path, language, expected, absent):
    hooks = TokenMonitorHooks(MonitorConfig(state_file=str(tmp_path / "state.json"),
                                           language=language, limit_total_tokens=100))
    hooks.on_post_api_request(session_id="a", usage={"input_tokens": 80})
    text = hooks.on_transform_llm_output(response_text="Done", session_id="a")
    assert expected in text
    assert absent not in text


def test_export_failure_does_not_lose_usage(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("occupied")
    hooks = TokenMonitorHooks(MonitorConfig(state_file=str(tmp_path / "state.json"),
                                           prom_export_path=str(blocker / "metrics.prom")))
    hooks.on_post_api_request(session_id="a", api_request_id="one", usage={"input_tokens": 10})
    assert hooks.ledger.get_session_stats("a").total_tokens == 10
    assert "Metrics export failed" in caplog.text


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": -1}, {"input_tokens": True}])
def test_unknown_or_invalid_usage_never_creates_counts(tmp_path, caplog, usage):
    hooks = TokenMonitorHooks(MonitorConfig(state_file=str(tmp_path / "state.json")))
    hooks.on_post_api_request(session_id="a", usage=usage)
    hooks.on_post_api_request(session_id="a", usage=usage)
    assert not hooks.ledger.state_path.exists()
    assert len(caplog.records) == 1


def test_fractional_threshold_does_not_warn_before_one_token(tmp_path):
    hooks = TokenMonitorHooks(MonitorConfig(state_file=str(tmp_path / "state.json"),
        limit_total_tokens=0, limit_output_tokens=1, warning_levels=[0.5]))
    hooks.on_post_api_request(session_id="a", usage={"input_tokens": 1})
    assert not hooks.ledger.is_warned("a", 0.5)


def test_directory_plugin_manifest_and_entrypoint(tmp_path, monkeypatch):
    from pathlib import Path

    from hermes_cli import plugins
    root = Path(__file__).resolve().parents[1]
    manifest = plugins.parse_manifest_file(root / "plugin.yaml", root, "user", "")
    assert manifest is not None
    manager = plugins.PluginManager()
    ctx = plugins.PluginContext(manifest, manager)
    module = manager._load_directory_module(manifest)
    module.register(ctx)
    manager.invoke_hook("post_api_request", session_id="installed", api_request_id="one",
                        usage={"prompt_tokens": 90, "completion_tokens": 10})
    assert module.get_hooks().ledger.get_session_stats("installed").total_tokens == 100


@pytest.mark.parametrize("provider,api_mode,raw", [
    ("openai", "chat_completions", {
        "prompt_tokens": 100, "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 80}}),
    ("anthropic", "anthropic_messages", {
        "input_tokens": 20, "cache_read_input_tokens": 70,
        "cache_creation_input_tokens": 10, "output_tokens": 20}),
    ("openai-codex", "codex_responses", {
        "input_tokens": 100, "output_tokens": 20,
        "input_tokens_details": {"cached_tokens": 80}}),
])
def test_real_hermes_usage_normalizer_contract(tmp_path, provider, api_mode, raw):
    from agent.api_request_hooks import ApiRequestHooksMixin
    agent = SimpleNamespace(provider=provider, api_mode=api_mode)
    usage = ApiRequestHooksMixin._usage_summary_for_api_request_hook(
        agent, SimpleNamespace(usage=raw))
    hooks = TokenMonitorHooks(MonitorConfig(state_file=str(tmp_path / "state.json")))
    hooks.on_post_api_request(session_id="a", usage=usage)
    record = hooks.ledger.get_session_stats("a")
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (100, 20, 120)
    assert record.cache_read_tokens + record.cache_write_tokens == 80
