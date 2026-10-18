"""POSIX-locked usage ledger with non-destructive reads and legacy migration."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import re
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from token_monitor.config import MonitorConfig

# Bounded replay protection: recent identified requests survive process restart.
MAX_PROCESSED_REQUESTS = 4096


class LedgerStateError(ValueError):
    """An existing ledger is unreadable or invalid; its bytes must be preserved."""


@dataclass
class UsageRecord:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    warned_levels: list[float] = field(default_factory=list)
    frozen: bool = False
    freeze_reason: str = ""
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def add(self, inp: int, out: int, tot: int | None = None, **usage: int) -> None:
        self.input_tokens += inp
        self.output_tokens += out
        self.total_tokens += tot if tot is not None else (inp + out)
        self.request_count += 1
        for key in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
            setattr(self, key, getattr(self, key) + usage.get(key, 0))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data.pop("_extra") | data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UsageRecord:
        if not isinstance(data, dict):
            raise LedgerStateError("Usage record must be an object")
        known = {f.name for f in fields(cls) if f.name != "_extra"}
        values = {k: copy.deepcopy(v) for k, v in data.items() if k in known}
        for key in known - {"warned_levels", "frozen", "freeze_reason"}:
            value = values.get(key, 0)
            if type(value) is not int or value < 0:
                raise LedgerStateError(f"Invalid non-negative integer counter: {key}")
        levels = values.get("warned_levels", [])
        if not isinstance(levels, list) or any(
            isinstance(x, bool) or not isinstance(x, (int, float))
            or not math.isfinite(x) or not 0 < x <= 1 for x in levels
        ):
            raise LedgerStateError("Invalid warned_levels")
        if not isinstance(values.get("frozen", False), bool):
            raise LedgerStateError("Invalid frozen flag")
        if not isinstance(values.get("freeze_reason", ""), str):
            raise LedgerStateError("Invalid freeze reason")
        return cls(**values, _extra={k: copy.deepcopy(v) for k, v in data.items() if k not in known})


def _normalize_state(data: Any, today: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise LedgerStateError("Ledger must be an object")
    data.setdefault("sessions", {})
    if not isinstance(data["sessions"], dict):
        raise LedgerStateError("sessions must be an object")
    had_sessions = bool(data["sessions"])
    sid = data.get("session_id", "")
    if not isinstance(sid, str):
        raise LedgerStateError("Invalid session_id")
    if not sid and "session" in data:
        sid = "default"
    if sid and "session" in data and sid not in data["sessions"]:
        data["sessions"][sid] = copy.deepcopy(data["session"])
    daily = data.setdefault("daily", {"date": today, "stats": {}})
    if not isinstance(daily, dict):
        raise LedgerStateError("daily must be an object")
    if "stats" not in daily:
        if not isinstance(data.get("day"), str):
            raise LedgerStateError("Legacy daily record has no date")
        daily = data["daily"] = {"date": data["day"], "stats": daily}
    if not isinstance(daily.get("date"), str):
        raise LedgerStateError("Invalid daily date")
    try:
        date.fromisoformat(daily["date"])
    except ValueError as exc:
        raise LedgerStateError("Invalid daily date") from exc
    data.setdefault("all_time", {})
    if not data.get("token_monitor_migrated", False):
        if sid and sid in data["sessions"]:
            raw = data["sessions"][sid]
            if not isinstance(raw, dict):
                raise LedgerStateError("Invalid legacy session record")
            raw.setdefault("frozen", data.get("frozen", False))
            raw.setdefault("freeze_reason", data.get("freeze_reason", ""))
        for raw in (daily["stats"], data["all_time"]):
            if not isinstance(raw, dict):
                raise LedgerStateError("Invalid global usage record")
            frozen = data.get("global_frozen", False)
            if not had_sessions:
                frozen = frozen or data.get("frozen", False)
            raw.setdefault("frozen", frozen)
            raw.setdefault("freeze_reason", data.get("global_freeze_reason") or data.get("freeze_reason", ""))
        _migrate_warning_history(data)
        data["token_monitor_migrated"] = True
    for raw in [*data["sessions"].values(), daily["stats"], data["all_time"]]:
        UsageRecord.from_dict(raw)
    processed = data.get("processed_requests", [])
    if not isinstance(processed, list) or any(not isinstance(x, str) for x in processed):
        raise LedgerStateError("Invalid processed request registry")
    return data


def _migrate_warning_history(data: dict) -> None:
    warned = data.get("warned_keys", [])
    if not isinstance(warned, list) or any(not isinstance(x, str) for x in warned):
        raise LedgerStateError("Invalid legacy warning history")
    for key in warned:
        match = re.fullmatch(r"(session|daily|all_time):(?:(.*):)?(\d{4}-\d{2}-\d{2}):total:([0-9.]+)", key)
        if not match:
            continue
        scope, sid, day, level = match.groups()
        try:
            value = float(level)
        except ValueError:
            continue
        if not 0 < value <= 1:
            continue
        if scope == "session":
            raw = data["sessions"].get(sid or "default")
        elif scope == "daily":
            raw = data["daily"]["stats"] if day == data["daily"]["date"] else None
        else:
            raw = data["all_time"]
        if isinstance(raw, dict):
            levels = raw.setdefault("warned_levels", [])
            if not isinstance(levels, list):
                raise LedgerStateError("Invalid warned_levels")
            if value not in levels:
                levels.append(value)


class TokenLedger:
    """Lock complete updates, including replay checks and threshold transitions."""

    def __init__(self, config: MonitorConfig):
        self.config = config
        self.state_path = Path(self.config.state_file).resolve()
        self.lock_path = self.state_path.with_suffix(".lock")

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Generator[dict[str, Any], None, None]:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            state = self._read_unlocked()
            before = copy.deepcopy(state)
            yield state
            if write and state != before:
                self._write_unlocked(state)
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            text = self.state_path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return _normalize_state({}, self._today())
        except (OSError, UnicodeError) as exc:
            raise LedgerStateError(f"Cannot read ledger {self.state_path}: {exc}") from exc
        try:
            return _normalize_state(json.loads(text), self._today())
        except (ValueError, TypeError, KeyError) as exc:
            raise LedgerStateError(f"Invalid ledger {self.state_path}; original file preserved: {exc}") from exc

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        tmp_path = self.state_path.with_name(f".tmp.{uuid.uuid4().hex}")
        try:
            with tmp_path.open("x", encoding="utf-8") as stream:
                os.chmod(tmp_path, 0o600)
                json.dump(state, stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, self.state_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _scoped_raw(self, state: dict, sid: str, *, create: bool = False) -> dict:
        if self.config.scope == "session":
            return state["sessions"].setdefault(sid, {}) if create else state["sessions"].get(sid, {})
        if self.config.scope == "all_time":
            return state["all_time"]
        daily = state["daily"]
        if daily["date"] != self._today():
            if not create:
                return {}
            daily.update(date=self._today(), stats={})
        return daily["stats"]

    def record_usage(self, session_id: str, input_tokens: int, output_tokens: int,
                     total_tokens: int | None = None, current_date: str | None = None,
                     *, request_id: str = "", **usage: int):
        """Return newly committed threshold transitions, or None for a duplicate request."""
        from token_monitor.evaluator import evaluate_usage
        date_str = current_date or self._today()
        tot = total_tokens if total_tokens is not None else input_tokens + output_tokens
        # Validate the incoming delta before entering the write transaction.
        UsageRecord.from_dict(dict(input_tokens=input_tokens, output_tokens=output_tokens,
                                   total_tokens=tot, **usage))
        digest = hashlib.sha256(f"{session_id}\0{request_id}".encode()).hexdigest() if request_id else None
        with self._transaction(write=True) as state:
            processed = state.setdefault("processed_requests", [])
            if digest and digest in processed:
                return None
            daily = state["daily"]
            if daily["date"] != date_str:
                daily.update(date=date_str, stats={})
            raw_session = state["sessions"].setdefault(session_id, {})
            for raw in (raw_session, daily["stats"], state["all_time"]):
                record = UsageRecord.from_dict(raw)
                record.add(input_tokens, output_tokens, tot, **usage)
                raw.update(record.to_dict())
            raw = (raw_session if self.config.scope == "session" else
                   daily["stats"] if self.config.scope == "daily" else state["all_time"])
            record = UsageRecord.from_dict(raw)
            result = evaluate_usage(record, self.config)
            raw["warned_levels"] = sorted(set(record.warned_levels + result.new_warning_levels))
            result.should_freeze = result.should_freeze and not record.frozen
            if result.should_freeze:
                raw.update(frozen=True, freeze_reason=result.freeze_reason)
            if digest:
                processed.append(digest)
                del processed[:-MAX_PROCESSED_REQUESTS]
            if "session_id" in state or "session" in state:
                state.update(session_id=session_id, session=copy.deepcopy(raw_session))
                state.update(frozen=raw_session.get("frozen", False),
                             freeze_reason=raw_session.get("freeze_reason", ""))
            if self.config.scope != "session" and "global_frozen" in state:
                state.update(global_frozen=raw.get("frozen", False),
                             global_freeze_reason=raw.get("freeze_reason", ""))
            if "day" in state:
                state["day"] = date_str
            return result

    def get_session_stats(self, session_id: str) -> UsageRecord:
        with self._transaction() as state:
            return UsageRecord.from_dict(state["sessions"].get(session_id, {}))

    def get_daily_stats(self, current_date: str | None = None) -> UsageRecord:
        with self._transaction() as state:
            daily = state["daily"]
            raw = daily["stats"] if daily["date"] == (current_date or self._today()) else {}
            return UsageRecord.from_dict(raw)

    def get_all_time_stats(self) -> UsageRecord:
        with self._transaction() as state:
            return UsageRecord.from_dict(state["all_time"])

    def get_scoped_stats(self, session_id: str) -> UsageRecord:
        with self._transaction() as state:
            return UsageRecord.from_dict(self._scoped_raw(state, session_id))

    def is_warned(self, session_id: str, level: float) -> bool:
        return any(abs(x - level) < 1e-4 for x in self.get_scoped_stats(session_id).warned_levels)

    def mark_warned(self, session_id: str, level: float) -> None:
        with self._transaction(write=True) as state:
            raw = self._scoped_raw(state, session_id, create=True)
            record = UsageRecord.from_dict(raw)
            if level not in record.warned_levels:
                record.warned_levels.append(level)
            raw.update(record.to_dict())

    def is_frozen(self, session_id: str) -> bool:
        return self.get_scoped_stats(session_id).frozen

    def get_freeze_reason(self, session_id: str) -> str:
        return self.get_scoped_stats(session_id).freeze_reason

    def set_frozen(self, session_id: str, frozen: bool, reason: str = "") -> None:
        with self._transaction(write=True) as state:
            raw = self._scoped_raw(state, session_id, create=True)
            raw.update(frozen=frozen, freeze_reason=reason if frozen else "")
            if self.config.scope == "session" and state.get("session_id") == session_id:
                state.update(session=copy.deepcopy(raw), frozen=frozen, freeze_reason=raw["freeze_reason"])
            elif self.config.scope != "session":
                state.update(global_frozen=frozen, global_freeze_reason=raw["freeze_reason"])
