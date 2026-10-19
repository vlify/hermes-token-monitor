"""Configuration definitions and loaders for hermes-token-monitor."""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field, fields
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
    notify_gateway: bool = False
    footer_mode: str = "on_warning"
    footer_warning_interval: int = 1
    prom_export_path: str = ""
    state_file: str = ""
    language: str = "en"
    include_auxiliary_calls: bool = False

    def __post_init__(self) -> None:
        for key in ("prom_export_path", "state_file"):
            if not isinstance(getattr(self, key), str):
                raise TypeError(f"{key} must be a path string")
        self.state_file = str(Path(self.state_file or os.getenv(STATE_ENV, "").strip()
                                   or DEFAULT_STATE_PATH).expanduser())

        for key in ("limit_total_tokens", "limit_input_tokens", "limit_output_tokens"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 0:
                raise ValueError(f"{key} must be a non-negative integer (0 disables the limit)")
        for key in ("freeze_on_limit", "notify_console", "notify_gateway", "include_auxiliary_calls"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} must be a boolean, not a string or number")
        if self.notify_gateway:
            raise ValueError("notify_gateway is unsupported; use footer_mode or prom_export_path")
        if not isinstance(self.warning_levels, list) or any(
            isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x)
            or not 0 < x <= 1 for x in self.warning_levels
        ):
            raise ValueError("warning_levels must be a list of fractions in (0, 1], e.g. [0.5, 0.8]")
        self.warning_levels = sorted({float(x) for x in self.warning_levels})
        if self.scope not in ("session", "daily", "all_time"):
            raise ValueError("scope must be session, daily or all_time")
        if self.footer_mode not in ("off", "on_warning", "always"):
            raise ValueError("footer_mode must be off, on_warning or always")
        if type(self.footer_warning_interval) is not int or self.footer_warning_interval < 1:
            raise ValueError("footer_warning_interval must be a positive integer")
        if self.language not in ("en", "zh"):
            raise ValueError("language must be en or zh")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MonitorConfig:
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})


def load_config() -> MonitorConfig:
    """Load configuration from Hermes config.yaml and environment, with fallback to legacy state."""
    data: dict[str, Any] = {}
    in_hermes = False
    try:
        from hermes_cli.config import load_config_readonly as hermes_load_config

        in_hermes = True
        root = hermes_load_config()
        if isinstance(root, dict):
            plugins_cfg = root.get("plugins", {})
            entries = plugins_cfg.get("entries", {})
            raw_entry = entries.get("tokenwatch", entries.get("hermes-token-monitor"))
            if isinstance(raw_entry, dict):
                data.update(raw_entry)
                for namespace in ("config", "settings"):
                    nested = raw_entry.get(namespace, {})
                    if not isinstance(nested, dict):
                        raise TypeError(f"tokenwatch.{namespace} must be an object")
                    data.update(nested)
    except (ImportError, AttributeError, KeyError) as exc:
        logger.debug("Failed to read hermes configuration dynamically: %s", exc)

    config_path = os.getenv(CONFIG_ENV, "").strip()
    if config_path:
        text = Path(config_path).expanduser().read_text(encoding="utf-8-sig")
        try:
            override = json.loads(text)
        except json.JSONDecodeError:
            try:
                import hermes_yaml as yaml
            except ImportError:
                try:
                    import yaml
                except ImportError as exc:
                    raise ValueError("YAML config requires Hermes or PyYAML; JSON needs neither") from exc
            override = yaml.safe_load(text)
        if not isinstance(override, dict):
            raise ValueError("Token monitor configuration must be an object")
        data.update(override)
    env_state = os.getenv(STATE_ENV, "").strip()
    if env_state:
        data["state_file"] = env_state
    if not data.get("state_file"):
        legacy_state = profile_home() / "tokenwatch/state.json"
        if in_hermes or os.getenv("HERMES_HOME") or legacy_state.is_file():
            data["state_file"] = str(legacy_state)

    # Relative user paths belong to the active profile, never the gateway's cwd.
    for key in ("state_file", "prom_export_path"):
        if data.get(key) and isinstance(data[key], str):
            path = Path(data[key]).expanduser()
            data[key] = str(path if path.is_absolute() else profile_home() / path)

    return MonitorConfig.from_dict(data)


def profile_home() -> Path:
    """Use the serving Hermes profile, including its ContextVar override."""
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except ImportError:
        return Path(os.getenv("HERMES_HOME") or "~/.config/hermes").expanduser()


