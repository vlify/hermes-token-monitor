"""Thread-safe and process-safe token usage ledger for hermes-token-monitor."""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from token_monitor.config import MonitorConfig


@dataclass
class UsageRecord:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0
    warned_levels: list[float] = field(default_factory=list)
    frozen: bool = False
    freeze_reason: str = ""

    def add(self, inp: int, out: int, tot: int | None = None) -> None:
        self.input_tokens += inp
        self.output_tokens += out
        self.total_tokens += tot if tot is not None else (inp + out)
        self.request_count += 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UsageRecord:
        return cls(
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
            request_count=int(data.get("request_count", 0)),
            warned_levels=[float(x) for x in data.get("warned_levels", [])],
            frozen=bool(data.get("frozen", False)),
            freeze_reason=str(data.get("freeze_reason", "")),
        )


class TokenLedger:
    """Manages multi-session and global token usage state with POSIX file locking."""

    def __init__(self, config: MonitorConfig):
        self.config = config
        self.state_path = Path(self.config.state_file).resolve()
        self.lock_path = self.state_path.with_suffix(".lock")

    @contextmanager
    def _transaction(self) -> Generator[dict[str, Any], None, None]:
        """Atomic, process-safe read-modify-write transaction with fcntl lock."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        lock_fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            state = self._read_unlocked()
            yield state
            self._write_unlocked(state)
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {
                "version": 1,
                "sessions": {},
                "daily": {"date": self._today(), "stats": UsageRecord().to_dict()},
                "all_time": UsageRecord().to_dict(),
            }
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise TypeError("State is not a JSON object")
                if "sessions" not in data:
                    data["sessions"] = {}
                if "daily" not in data:
                    data["daily"] = {"date": self._today(), "stats": UsageRecord().to_dict()}
                if "all_time" not in data:
                    data["all_time"] = UsageRecord().to_dict()
                return data
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            # Corrupted state fallback (non-destructive clean state structure)
            return {
                "version": 1,
                "sessions": {},
                "daily": {"date": self._today(), "stats": UsageRecord().to_dict()},
                "all_time": UsageRecord().to_dict(),
            }

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        tmp_path = self.state_path.with_name(f".tmp.{uuid.uuid4().hex}")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.state_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def record_usage(
        self,
        session_id: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int | None = None,
        current_date: str | None = None,
    ) -> None:
        """Record usage increment across session, daily, and all_time ledgers."""
        date_str = current_date or self._today()
        tot = total_tokens if total_tokens is not None else (input_tokens + output_tokens)

        with self._transaction() as state:
            # 1. Session record
            sess_map = state["sessions"]
            if session_id not in sess_map:
                sess_record = UsageRecord()
            else:
                sess_record = UsageRecord.from_dict(sess_map[session_id])
            sess_record.add(input_tokens, output_tokens, tot)
            sess_map[session_id] = sess_record.to_dict()

            # 2. Daily record
            daily_data = state["daily"]
            if daily_data.get("date") != date_str:
                daily_record = UsageRecord()
                daily_data["date"] = date_str
            else:
                daily_record = UsageRecord.from_dict(daily_data.get("stats", {}))
            daily_record.add(input_tokens, output_tokens, tot)
            daily_data["stats"] = daily_record.to_dict()

            # 3. All-time record
            all_time_record = UsageRecord.from_dict(state["all_time"])
            all_time_record.add(input_tokens, output_tokens, tot)
            state["all_time"] = all_time_record.to_dict()

    def get_session_stats(self, session_id: str) -> UsageRecord:
        with self._transaction() as state:
            raw = state["sessions"].get(session_id)
            return UsageRecord.from_dict(raw) if raw else UsageRecord()

    def get_daily_stats(self, current_date: str | None = None) -> UsageRecord:
        with self._transaction() as state:
            daily_data = state["daily"]
            date_str = current_date or self._today()
            if daily_data.get("date") != date_str:
                return UsageRecord()
            return UsageRecord.from_dict(daily_data.get("stats", {}))

    def get_all_time_stats(self) -> UsageRecord:
        with self._transaction() as state:
            return UsageRecord.from_dict(state["all_time"])

    def is_warned(self, session_id: str, level: float) -> bool:
        with self._transaction() as state:
            if self.config.scope == "session":
                raw = state["sessions"].get(session_id)
            elif self.config.scope == "daily":
                raw = state["daily"].get("stats")
            else:
                raw = state["all_time"]
            if not raw:
                return False
            rec = UsageRecord.from_dict(raw)
            return any(abs(lvl - level) < 1e-4 for lvl in rec.warned_levels)

    def mark_warned(self, session_id: str, level: float) -> None:
        with self._transaction() as state:
            if self.config.scope == "session":
                sess_map = state["sessions"]
                rec = UsageRecord.from_dict(sess_map.get(session_id, {}))
                if not any(abs(lvl - level) < 1e-4 for lvl in rec.warned_levels):
                    rec.warned_levels.append(level)
                sess_map[session_id] = rec.to_dict()
            elif self.config.scope == "daily":
                daily_data = state["daily"]
                rec = UsageRecord.from_dict(daily_data.get("stats", {}))
                if not any(abs(lvl - level) < 1e-4 for lvl in rec.warned_levels):
                    rec.warned_levels.append(level)
                daily_data["stats"] = rec.to_dict()
            else:
                rec = UsageRecord.from_dict(state["all_time"])
                if not any(abs(lvl - level) < 1e-4 for lvl in rec.warned_levels):
                    rec.warned_levels.append(level)
                state["all_time"] = rec.to_dict()

    def is_frozen(self, session_id: str) -> bool:
        with self._transaction() as state:
            if self.config.scope == "session":
                raw = state["sessions"].get(session_id)
            elif self.config.scope == "daily":
                raw = state["daily"].get("stats")
            else:
                raw = state["all_time"]
            return bool(raw and raw.get("frozen", False))

    def get_freeze_reason(self, session_id: str) -> str:
        with self._transaction() as state:
            if self.config.scope == "session":
                raw = state["sessions"].get(session_id)
            elif self.config.scope == "daily":
                raw = state["daily"].get("stats")
            else:
                raw = state["all_time"]
            return str(raw.get("freeze_reason", "")) if raw else ""

    def set_frozen(self, session_id: str, frozen: bool, reason: str = "") -> None:
        with self._transaction() as state:
            if self.config.scope == "session":
                sess_map = state["sessions"]
                rec = UsageRecord.from_dict(sess_map.get(session_id, {}))
                rec.frozen = frozen
                rec.freeze_reason = reason if frozen else ""
                sess_map[session_id] = rec.to_dict()
            elif self.config.scope == "daily":
                daily_data = state["daily"]
                rec = UsageRecord.from_dict(daily_data.get("stats", {}))
                rec.frozen = frozen
                rec.freeze_reason = reason if frozen else ""
                daily_data["stats"] = rec.to_dict()
            else:
                rec = UsageRecord.from_dict(state["all_time"])
                rec.frozen = frozen
                rec.freeze_reason = reason if frozen else ""
                state["all_time"] = rec.to_dict()
