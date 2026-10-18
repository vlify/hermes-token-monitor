import sys
from pathlib import Path

_pkg_dir = str(Path(__file__).resolve().parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from token_monitor import (  # noqa: E402 - plugin-local package path must be installed first
    get_hooks,
    on_post_api_request,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
    on_transform_llm_output,
    register,
)

__all__ = [
    "get_hooks",
    "on_post_api_request",
    "on_pre_tool_call",
    "on_session_end",
    "on_session_start",
    "on_transform_llm_output",
    "register",
]
