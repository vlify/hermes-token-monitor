"""Hermes Token Monitor plugin entrypoint and exported hooks."""

from __future__ import annotations

import os
import threading
from typing import Any

from .hooks import TokenMonitorHooks

_HOOKS_INSTANCE: TokenMonitorHooks | None = None
_HOOKS_BY_HOME: dict[tuple[str, str, str], TokenMonitorHooks] = {}
_HOOKS_LOCK = threading.Lock()


def get_hooks() -> TokenMonitorHooks:
    """Lazy initialize global hooks instance."""
    if _HOOKS_INSTANCE is not None:  # explicit injection for embedders/tests
        return _HOOKS_INSTANCE
    from .config import CONFIG_ENV, STATE_ENV, load_config, profile_home
    key = (str(profile_home().resolve()), os.getenv(CONFIG_ENV, ""), os.getenv(STATE_ENV, ""))
    with _HOOKS_LOCK:
        config = load_config()
        if key not in _HOOKS_BY_HOME or _HOOKS_BY_HOME[key].config != config:
            _HOOKS_BY_HOME[key] = TokenMonitorHooks(config)
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
        from hermes_cli.plugins import VALID_HOOKS
        if "post_auxiliary_call" in VALID_HOOKS:
            ctx.register_hook("post_auxiliary_call", on_post_auxiliary_call)
        elif get_hooks().config.include_auxiliary_calls:
            raise RuntimeError("include_auxiliary_calls requires Hermes with post_auxiliary_call support")
    if ctx is not None and hasattr(ctx, "register_cli_command"):
        from .cli import handle_command, setup_parser
        ctx.register_cli_command(name="tokenwatch", help="Inspect or unfreeze token budgets",
                                 setup_fn=setup_parser, handler_fn=handle_command)


def on_session_start(*args: Any, **kwargs: Any) -> None:
    get_hooks().on_session_start(*args, **kwargs)


def on_post_api_request(*args: Any, **kwargs: Any) -> None:
    get_hooks().on_post_api_request(*args, **kwargs)


def on_post_auxiliary_call(**kwargs: Any) -> None:
    get_hooks().on_post_auxiliary_call(**kwargs)


def on_pre_tool_call(*args: Any, **kwargs: Any) -> dict[str, str] | None:
    return get_hooks().on_pre_tool_call(*args, **kwargs)


def on_session_end(*args: Any, **kwargs: Any) -> None:
    get_hooks().on_session_end(*args, **kwargs)


def on_transform_llm_output(*args: Any, **kwargs: Any) -> str | None:
    return get_hooks().on_transform_llm_output(*args, **kwargs)
