"""Hermes lifecycle hooks implementation for hermes-token-monitor."""

from __future__ import annotations

import logging
import threading
from typing import Any

from token_monitor.config import MonitorConfig
from token_monitor.exporter import PrometheusExporter
from token_monitor.ledger import TokenLedger

logger = logging.getLogger("hermes.plugin.token_monitor")


def _extract_usage(usage: Any, response: Any) -> tuple[int, int, int]:
    if not isinstance(usage, dict) and isinstance(response, dict):
        usage = response.get("usage")
    if isinstance(usage, dict):
        inp = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
        out = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
        tot = int(usage.get("total_tokens", inp + out) or (inp + out))
        return inp, out, tot
    if response is not None and hasattr(response, "usage"):
        u = getattr(response, "usage", None)
        if u:
            inp = int(getattr(u, "prompt_tokens", getattr(u, "input_tokens", 0)) or 0)
            out = int(getattr(u, "completion_tokens", getattr(u, "output_tokens", 0)) or 0)
            tot = int(getattr(u, "total_tokens", inp + out) or (inp + out))
            return inp, out, tot
    return 0, 0, 0


class TokenMonitorHooks:
    """Encapsulates all hook logic and maintains ledger / exporter instances."""

    def __init__(self, config: MonitorConfig | None = None):
        if config is None:
            from token_monitor.config import load_config

            config = load_config()
        self.config = config
        self.ledger = TokenLedger(self.config)
        self._footer_turns: dict[str, int] = {}
        self._footer_lock = threading.Lock()
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
        **kwargs: Any,
    ) -> None:
        sid = str(session_id or "default").strip() or "default"
        inp, out, tot = _extract_usage(usage, response)
        if tot <= 0:
            return

        raw = usage if isinstance(usage, dict) else (
            response.get("usage", {}) if isinstance(response, dict) else {})
        extra = {
            "cache_read_tokens": int(raw.get("cache_read_tokens", raw.get("cached_tokens", 0)) or 0),
            "cache_write_tokens": int(raw.get("cache_write_tokens", 0) or 0),
            "reasoning_tokens": int(raw.get("reasoning_tokens", raw.get("thinking_tokens", 0)) or 0),
        }
        res = self.ledger.record_usage(sid, inp, out, tot, request_id=api_request_id, **extra)
        if res is None:
            return
        if res.new_warning_levels and self.config.notify_console:
            logger.warning("[token_monitor] %s", res.message)

        if res.should_freeze:
            logger.error("[token_monitor] %s", res.freeze_reason)

        if self.exporter:
            self.exporter.export(self.ledger, active_session_id=sid)

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
        limit = self.config.limit_total_tokens
        with self._footer_lock:
            turn = self._footer_turns.get(sid, 0) + 1
            self._footer_turns[sid] = turn
        if self.config.footer_mode == "on_warning":
            levels = self.config.warning_levels
            if not levels or limit <= 0 or record.total_tokens < int(limit * min(levels)):
                return None
            if (turn - 1) % self.config.footer_warning_interval:
                return None
        used = record.total_tokens
        if limit > 0:
            text = f"Token: {int(used / limit * 100)}% ({_compact(used)}/{_compact(limit)})"
        else:
            text = f"Token: {_compact(used)}"
        if self.config.scope != "session":
            text += f" {self.config.scope} | 全局累计，/new 不重置"
        ending = "limit reached. 已达上限" if limit > 0 and used >= limit else "used. 建议规划收尾"
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
