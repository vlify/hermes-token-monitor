"""Native directory adapter; keep business modules inside Hermes' reload namespace."""

from importlib import import_module

# Hermes supplies a package namespace; flat imports support source-tree tooling.
_plugin = (import_module(".token_monitor", __package__) if __package__
           else import_module("token_monitor"))

get_hooks = _plugin.get_hooks
on_post_api_request = _plugin.on_post_api_request
on_post_auxiliary_call = _plugin.on_post_auxiliary_call
on_pre_tool_call = _plugin.on_pre_tool_call
on_session_end = _plugin.on_session_end
on_session_start = _plugin.on_session_start
on_transform_llm_output = _plugin.on_transform_llm_output
register = _plugin.register

__all__ = [
    "get_hooks",
    "on_post_api_request",
    "on_post_auxiliary_call",
    "on_pre_tool_call",
    "on_session_end",
    "on_session_start",
    "on_transform_llm_output",
    "register",
]
