"""Hermes lifecycle hooks implementation for hermes-token-monitor."""

from __future__ import annotations

import logging
from typing import Any

from token_monitor.config import MonitorConfig
from token_monitor.evaluator import evaluate_usage
from token_monitor.exporter import PrometheusExporter
from token_monitor.ledger import TokenLedger

logger = logging.getLogger("hermes.plugin.token_monitor")


def _extract_usage(usage: Any, response: Any) -> tuple[int, int, int]:
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
        **kwargs: Any,
    ) -> None:
        sid = str(session_id or "default").strip() or "default"
        inp, out, tot = _extract_usage(usage, response)
        if tot <= 0:
            return

        self.ledger.record_usage(sid, inp, out, tot)

        if self.config.scope == "session":
            record = self.ledger.get_session_stats(sid)
        elif self.config.scope == "daily":
            record = self.ledger.get_daily_stats()
        else:
            record = self.ledger.get_all_time_stats()

        res = evaluate_usage(record, self.config)
        for lvl in res.new_warning_levels:
            self.ledger.mark_warned(sid, lvl)
            if self.config.notify_console:
                logger.warning("[token_monitor] %s", res.message)

        if res.should_freeze and not self.ledger.is_frozen(sid):
            self.ledger.set_frozen(sid, True, reason=res.freeze_reason)
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
        if self.ledger.is_frozen(sid):
            reason = self.ledger.get_freeze_reason(sid) or "Token limit reached"
            return {"abort": "true", "message": f"Tool execution halted: {reason}"}
        return None

    def on_session_end(self, *, session_id: str = "", **kwargs: Any) -> None:
        sid = str(session_id or "default").strip() or "default"
        logger.debug("token_monitor: session end sid=%s", sid)
