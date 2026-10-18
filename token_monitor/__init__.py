"""Hermes Token Monitor plugin entrypoint and exported hooks."""

from __future__ import annotations

from typing import Any
import os
import threading

from token_monitor.hooks import TokenMonitorHooks

_HOOKS_INSTANCE: TokenMonitorHooks | None = None
_HOOKS_BY_HOME: dict[tuple[str, str, str], TokenMonitorHooks] = {}
_HOOKS_LOCK = threading.Lock()


def get_hooks() -> TokenMonitorHooks:
    """Lazy initialize global hooks instance."""
    if _HOOKS_INSTANCE is not None:  # explicit injection for embedders/tests
        return _HOOKS_INSTANCE
    from token_monitor.config import CONFIG_ENV, STATE_ENV, profile_home
    key = (str(profile_home().resolve()), os.getenv(CONFIG_ENV, ""), os.getenv(STATE_ENV, ""))
    with _HOOKS_LOCK:
        if key not in _HOOKS_BY_HOME:
            _HOOKS_BY_HOME[key] = TokenMonitorHooks()
        return _HOOKS_BY_HOME[key]


def register(ctx: Any = None) -> None:
    """Hermes plugin registration callback."""
    get_hooks()
    if ctx is not None and hasattr(ctx, "register_hook"):
        ctx.register_hook("on_session_start", on_session_start)
        ctx.register_hook("post_api_request", on_post_api_request)
        ctx.register_hook("pre_tool_call", on_pre_tool_call)
        ctx.register_hook("on_session_end", on_session_end)
        ctx.register_hook("transform_llm_output", on_transform_llm_output)


def on_session_start(*args: Any, **kwargs: Any) -> None:
    get_hooks().on_session_start(*args, **kwargs)


def on_post_api_request(*args: Any, **kwargs: Any) -> None:
    get_hooks().on_post_api_request(*args, **kwargs)


def on_pre_tool_call(*args: Any, **kwargs: Any) -> dict[str, str] | None:
    return get_hooks().on_pre_tool_call(*args, **kwargs)


def on_session_end(*args: Any, **kwargs: Any) -> None:
    get_hooks().on_session_end(*args, **kwargs)


def on_transform_llm_output(*args: Any, **kwargs: Any) -> str | None:
    return get_hooks().on_transform_llm_output(*args, **kwargs)
