import sys
from pathlib import Path

_pkg_dir = str(Path(__file__).resolve().parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from token_monitor import (
    get_hooks,
    on_post_api_request,
    on_pre_tool_call,
    on_session_end,
    on_session_start,
    register,
)

__all__ = [
    "get_hooks",
    "on_post_api_request",
    "on_pre_tool_call",
    "on_session_end",
    "on_session_start",
    "register",
]
