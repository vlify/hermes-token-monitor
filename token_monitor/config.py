"""Configuration definitions and loaders for hermes-token-monitor."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes.plugin.token_monitor.config")

CONFIG_ENV = "HERMES_TOKEN_MONITOR_CONFIG"

STATE_ENV = "HERMES_TOKEN_MONITOR_STATE"

DEFAULT_STATE_PATH = "~/.local/state/hermes/token_monitor/state.json"


@dataclass
class MonitorConfig:
    scope: str = "session"  # "session" | "daily" | "all_time"
    limit_total_tokens: int = 20_000_000
    limit_input_tokens: int = 0
    limit_output_tokens: int = 0
    warning_levels: list[float] = field(default_factory=lambda: [0.5, 0.8, 0.95])
    freeze_on_limit: bool = False
    notify_console: bool = True
    notify_gateway: bool = True
    prom_export_path: str = ""
    state_file: str = ""

    def __post_init__(self) -> None:
        if not self.state_file:
            env_state = os.getenv(STATE_ENV, "").strip()
            self.state_file = env_state or str(Path(DEFAULT_STATE_PATH).expanduser())
        else:
            self.state_file = str(Path(self.state_file).expanduser())

        # Ensure warning levels are sorted floats
        self.warning_levels = sorted(float(x) for x in self.warning_levels if 0.0 < float(x) <= 1.0)
        self.scope = self.scope.lower().strip()
        if self.scope not in ("session", "daily", "all_time"):
            self.scope = "session"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MonitorConfig:
        fields = {
            "scope": str(data.get("scope", "session")),
            "limit_total_tokens": int(data.get("limit_total_tokens", 20_000_000)),
            "limit_input_tokens": int(data.get("limit_input_tokens", 0)),
            "limit_output_tokens": int(data.get("limit_output_tokens", 0)),
            "warning_levels": list(data.get("warning_levels", [0.5, 0.8, 0.95])),
            "freeze_on_limit": bool(data.get("freeze_on_limit", False)),
            "notify_console": bool(data.get("notify_console", True)),
            "notify_gateway": bool(data.get("notify_gateway", True)),
            "prom_export_path": str(data.get("prom_export_path", "")),
            "state_file": str(data.get("state_file", "")),
        }
        return cls(**fields)


def load_config() -> MonitorConfig:
    """Load configuration from Hermes config.yaml and environment, with fallback to legacy state."""
    data: dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config as hermes_load_config

        root = hermes_load_config()
        if isinstance(root, dict):
            plugins_cfg = root.get("plugins", {})
            entries = plugins_cfg.get("entries", {})
            raw_entry = entries.get("tokenwatch") or entries.get("hermes-token-monitor")
            if isinstance(raw_entry, dict):
                data.update(raw_entry)
    except (ImportError, AttributeError, KeyError) as exc:
        logger.debug("Failed to read hermes configuration dynamically: %s", exc)

    if not data.get("state_file"):
        legacy_state = Path("~/.config/hermes/tokenwatch/state.json").expanduser()
        if legacy_state.is_file():
            data["state_file"] = str(legacy_state)

    return MonitorConfig.from_dict(data)


