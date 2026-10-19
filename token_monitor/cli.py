"""Budget inspection and explicit unfreeze through Hermes' CLI extension API."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict


def setup_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=("status", "unfreeze"), nargs="?", default="status")
    parser.add_argument("--session", default="", help="Exact session ID (required to unfreeze a session)")
    parser.set_defaults(func=handle_command)


def handle_command(args: argparse.Namespace) -> None:
    from . import get_hooks
    hooks = get_hooks()
    sid = args.session
    if args.action == "unfreeze":
        if hooks.config.scope == "session" and not sid:
            raise SystemExit("Session scope requires --session ID; no session was changed")
        hooks.ledger.set_frozen(sid, False)
    record = (hooks.ledger.get_scoped_stats(sid) if sid or hooks.config.scope != "session"
              else hooks.ledger.get_all_time_stats())
    stats = asdict(record)
    stats.pop("_extra")
    print(json.dumps({
        "scope": hooks.config.scope if sid or hooks.config.scope != "session" else "all_time",
        "session_id": sid or None,
        "state_file": str(hooks.ledger.state_path),
        "include_auxiliary_calls": hooks.config.include_auxiliary_calls,
        "limits": {name: getattr(hooks.config, f"limit_{name}_tokens")
                   for name in ("total", "input", "output")},
        "usage": stats,
    }, ensure_ascii=False, indent=2))
