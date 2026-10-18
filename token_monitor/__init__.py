"""Hermes Token Monitor plugin entrypoint and exported hooks."""

from __future__ import annotations

from typing import Any

from token_monitor.hooks import TokenMonitorHooks

_HOOKS_INSTANCE: TokenMonitorHooks | None = None


def get_hooks() -> TokenMonitorHooks:
    """Lazy initialize global hooks instance."""
    global _HOOKS_INSTANCE
    if _HOOKS_INSTANCE is None:
        _HOOKS_INSTANCE = TokenMonitorHooks()
    return _HOOKS_INSTANCE


def register(ctx: Any = None) -> None:
    """Hermes plugin registration callback."""
    get_hooks()
    if ctx is not None and hasattr(ctx, "register_hook"):
        ctx.register_hook("on_session_start", on_session_start)
        ctx.register_hook("post_api_request", on_post_api_request)
        ctx.register_hook("pre_tool_call", on_pre_tool_call)
        ctx.register_hook("on_session_end", on_session_end)


def on_session_start(*args: Any, **kwargs: Any) -> None:
    get_hooks().on_session_start(*args, **kwargs)


def on_post_api_request(*args: Any, **kwargs: Any) -> None:
    get_hooks().on_post_api_request(*args, **kwargs)


def on_pre_tool_call(*args: Any, **kwargs: Any) -> dict[str, str] | None:
    return get_hooks().on_pre_tool_call(*args, **kwargs)


def on_session_end(*args: Any, **kwargs: Any) -> None:
    get_hooks().on_session_end(*args, **kwargs)
