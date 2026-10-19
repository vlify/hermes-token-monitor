"""Hermes lifecycle hooks implementation for hermes-token-monitor."""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict
from typing import Any

from .config import MonitorConfig
from .evaluator import budget_progress
from .exporter import PrometheusExporter
from .ledger import TokenLedger
from .usage import extract_usage

logger = logging.getLogger("hermes.plugin.token_monitor")


class TokenMonitorHooks:
    """Encapsulates all hook logic and maintains ledger / exporter instances."""

    def __init__(self, config: MonitorConfig | None = None):
        if config is None:
            from .config import load_config

            config = load_config()
        self.config = config
        self.ledger = TokenLedger(self.config)
        self._footer_turns: dict[str, int] = {}
        self._footer_lock = threading.Lock()
        self._diagnostics: set[str] = set()
        self.exporter = (
            PrometheusExporter(self.config.prom_export_path)
            if self.config.prom_export_path
            else None
        )


    def on_session_start(
        self,
        *,
        session_id: str = "",
        model: str = "",
        platform: str = "",
        **kwargs: Any,
    ) -> None:
        sid = str(session_id or "default").strip() or "default"
        logger.debug("token_monitor: session start sid=%s model=%s platform=%s", sid, model, platform)

    def on_post_api_request(
        self,
        *,
        session_id: str = "",
        usage: Any = None,
        response: Any = None,
        api_request_id: str = "",
        platform: str = "",
        **kwargs: Any,
    ) -> None:
        sid = str(session_id or "default").strip() or "default"
        if not session_id:
            self._warn_once("identity", "Usage has no session_id; stored in the shared default bucket")
        try:
            delta = extract_usage(usage, response)
        except ValueError as exc:
            self._warn_once("invalid_usage", f"Invalid usage ignored; accounting is incomplete: {exc}")
            return
        if delta is None:
            self._warn_once("missing_usage", "No positive usage reported; accounting may be incomplete")
            return
        res = self.ledger.record_usage(sid, request_id=api_request_id, **asdict(delta))
        if res is None:
            return
        if res.new_warning_levels and self.config.notify_console:
            logger.warning("[token_monitor] %s", res.message)

        if res.should_freeze:
            logger.error("[token_monitor] %s", res.freeze_reason)

        if self.config.notify_gateway and (res.new_warning_levels or res.should_freeze):
            from .notifications import send_alert
            send_alert(sid, platform, res, self.config)

        if self.exporter:
            try:
                self.exporter.export(self.ledger, active_session_id=sid)
            except OSError as exc:
                self._warn_once("export", f"Metrics export failed; ledger accounting succeeded: {exc}")

    def _warn_once(self, key: str, message: str) -> None:
        with self._footer_lock:
            if key in self._diagnostics:
                return
            self._diagnostics.add(key)
        logger.warning("[token_monitor] %s", message)

    def on_post_auxiliary_call(self, **kwargs: Any) -> None:
        if self.config.include_auxiliary_calls:
            self.on_post_api_request(**kwargs)

    def on_pre_tool_call(
        self,
        *,
        tool_name: str = "",
        session_id: str = "",
        **kwargs: Any,
    ) -> dict[str, str] | None:
        sid = str(session_id or "default").strip() or "default"
        if not self.config.freeze_on_limit:
            return None
        record = self.ledger.get_scoped_stats(sid)
        if record.frozen:
            reason = record.freeze_reason or "Token limit reached"
            return {"action": "block", "message": f"Tool execution halted: {reason}"}
        return None

    def on_transform_llm_output(
        self, response_text: str = "", session_id: str = "", **kwargs: Any,
    ) -> str | None:
        """Append the existing warning footer through Hermes' final-response transform."""
        if not response_text.strip() or self.config.footer_mode == "off":
            return None
        if "\n---\n[Token:" in response_text:
            return None
        sid = str(session_id or "default").strip() or "default"
        record = self.ledger.get_scoped_stats(sid)
        metric, used, limit = budget_progress(record, self.config)
        with self._footer_lock:
            turn = self._footer_turns.get(sid, 0) + 1
            self._footer_turns[sid] = turn
        if self.config.footer_mode == "on_warning":
            levels = self.config.warning_levels
            if not levels or limit <= 0 or used < limit * min(levels):
                return None
            if (turn - 1) % self.config.footer_warning_interval:
                return None
        if limit > 0:
            text = f"Token: {int(used / limit * 100)}% ({_compact(used)}/{_compact(limit)})"
        else:
            text = f"Token: {_compact(used)}"
        if metric != "total":
            text += f" {metric}"
        zh = self.config.language == "zh"
        if self.config.scope != "session":
            text += f" {self.config.scope} | " + (
                "全局累计，/new 不重置" if zh else "shared budget; /new does not reset it")
        if limit > 0 and used >= limit:
            ending = "已达上限" if zh else "limit reached"
        elif limit > 0:
            ending = "建议规划收尾" if zh else "used; plan to wrap up"
        else:
            ending = "未设置额度" if zh else "no limit configured"
        return f"{response_text}\n\n---\n[{text} {ending}]"

    def on_session_end(self, *, session_id: str = "", **kwargs: Any) -> None:
        sid = str(session_id or "default").strip() or "default"
        logger.debug("token_monitor: session end sid=%s", sid)
        with self._footer_lock:
            self._footer_turns.pop(sid, None)


def _compact(value: int) -> str:
    for scale, suffix in ((1_000_000, "M"), (1_000, "k")):
        if value >= scale:
            return f"{value / scale:.1f}".removesuffix(".0") + suffix
    return str(value)
