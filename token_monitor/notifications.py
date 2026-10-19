"""Deliver opt-in alerts through Hermes' live adapters, without starting an agent turn."""
from __future__ import annotations

import asyncio
import logging
from contextvars import copy_context
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import MonitorConfig, profile_home
from .evaluator import EvaluationResult

logger = logging.getLogger("hermes.plugin.token_monitor.notifications")


@dataclass(frozen=True)
class NotificationRoute:
    home: Path
    platform: str
    chat_id: str
    thread_id: str
    message_id: str
    user_id: str
    scope_id: str
    parent_chat_id: str


def current_route(session_id: str, platform: str = "") -> NotificationRoute | None:
    # get_session_env falls back to process-global environment values when unbound.
    # Only task-bound ContextVars can prove the destination of a concurrent turn.
    ctx = copy_context()
    bound = {var.name: ctx[var] for var in ctx if var.name.startswith("HERMES_SESSION_")}

    def value(name: str) -> str:
        item = bound.get("HERMES_SESSION_" + name)
        return item if isinstance(item, str) else ""

    from gateway.session_context import NON_MESSAGING_SESSION_SURFACES
    current_platform = value("PLATFORM")
    if (not session_id or value("ID") != session_id or not value("CHAT_ID")
            or current_platform in NON_MESSAGING_SESSION_SURFACES
            or (platform and platform != current_platform)):
        return None
    return NotificationRoute(profile_home().resolve(), current_platform, value("CHAT_ID"),
        value("THREAD_ID"), value("MESSAGE_ID"), value("USER_ID"), value("SCOPE_ID"),
        value("PARENT_CHAT_ID"))


def notification_text(result: EvaluationResult, config: MonitorConfig) -> str:
    if config.language == "zh":
        state = "已达到额度，后续工具调用已冻结" if result.should_freeze else "额度提醒，建议规划收尾"
        return (f"[Token Monitor] {state}\n"
                f"{config.scope} / {result.metric}: {result.used:,}/{result.limit:,} tokens "
                f"({result.used / result.limit:.0%})")
    state = "Limit reached; subsequent tool calls are blocked" if result.should_freeze else "Budget warning; plan to wrap up"
    return (f"[Token Monitor] {state}\n"
            f"{config.scope} / {result.metric}: {result.used:,}/{result.limit:,} tokens "
            f"({result.used / result.limit:.0%})")


async def deliver(route: NotificationRoute, adapter: Any, platform: Any, message: str) -> bool:
    """Use Hermes' common adapter API, preserving raw IDs and reply anchors."""
    from gateway.warning_notifications import warning_notifications_enabled
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(route.home)
    try:
        if not warning_notifications_enabled(platform):
            logger.info("[token_monitor] Gateway alert suppressed by display policy")
            return False
        # Only the owning profile's resolved adapter is admitted. BasePlatformAdapter.send
        # takes reply_to independently of thread metadata (email needs the former).
        metadata = {"notification_category": "diagnostic"}
        for key, value in (("user_id", route.user_id), ("scope_id", route.scope_id),
                           ("parent_chat_id", route.parent_chat_id)):
            if value:
                metadata[key] = value
        if route.platform == "telegram" and route.message_id:
            metadata["telegram_reply_to_message_id"] = route.message_id
        if route.thread_id:
            metadata["thread_id"] = route.thread_id
        receipt = await adapter.send(chat_id=route.chat_id, content=message,
                                     reply_to=route.message_id or None, metadata=metadata)
        success = (receipt.get("success") is True and receipt.get("delivered") is not False
                   if isinstance(receipt, dict) else getattr(receipt, "success", False) is True)
        if not success:
            # Adapter exception strings may contain credentials; keep logs bounded
            # to the outcome and let the host own transport diagnostics/redaction.
            logger.warning("[token_monitor] Gateway alert was not delivered; no automatic retry")
        return success
    finally:
        reset_hermes_home_override(token)


def send_alert(session_id: str, platform: str, result: EvaluationResult, config: MonitorConfig):
    """Schedule at most one attempt for a threshold already committed by the ledger."""
    try:
        route = current_route(session_id, platform)
        if route is None:
            logger.info("[token_monitor] Gateway alert skipped: no matching task-bound chat")
            return None
        from gateway.config import Platform
        from tools.send_message_senders import _live_adapter
        native_platform = Platform(route.platform)
        runner, adapter = _live_adapter(native_platform)
        loop = getattr(runner, "_gateway_loop", None)
        if adapter is None or loop is None or not loop.is_running():
            logger.warning("[token_monitor] Gateway alert skipped: owning live adapter unavailable")
            return None
        coro = deliver(route, adapter, native_platform, notification_text(result, config))
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception:
            coro.close()
            raise

        def completed(done):
            if done.cancelled() or done.exception() is not None:
                logger.warning("[token_monitor] Gateway alert failed; no automatic retry")
            elif done.result():
                logger.info("[token_monitor] Gateway alert delivered (%s)", route.platform)

        future.add_done_callback(completed)
        return future
    except Exception as exc:  # noqa: BLE001 - host/plugin boundary; never expose credential-bearing errors
        logger.warning("[token_monitor] Gateway alert unavailable (%s); accounting remains committed",
                       type(exc).__name__)
        return None
