"""Keep every plugin test away from the operator's Hermes state and configuration."""
import pytest


@pytest.fixture(autouse=True)
def isolated_plugin_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_TEST_ISOLATION", str(home))
    monkeypatch.setenv("HERMES_DISABLE_LAZY_INSTALLS", "1")
    monkeypatch.setenv("HERMES_TOKEN_MONITOR_STATE", str(home / "state.json"))
    monkeypatch.delenv("HERMES_TOKEN_MONITOR_CONFIG", raising=False)
    import token_monitor
    monkeypatch.setattr(token_monitor, "_HOOKS_INSTANCE", None)
    monkeypatch.setattr(token_monitor, "_HOOKS_BY_HOME", {})
