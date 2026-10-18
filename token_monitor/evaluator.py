"""Threshold evaluation and alert state machine for hermes-token-monitor."""

from __future__ import annotations

from dataclasses import dataclass, field

from token_monitor.config import MonitorConfig
from token_monitor.ledger import UsageRecord


@dataclass
class EvaluationResult:
    should_warn: bool = False
    new_warning_levels: list[float] = field(default_factory=list)
    should_freeze: bool = False
    freeze_reason: str = ""
    message: str = ""


def evaluate_usage(record: UsageRecord, config: MonitorConfig) -> EvaluationResult:
    """Evaluate current record against configured thresholds and limits."""
    new_levels: list[float] = []
    messages: list[str] = []

    limit = config.limit_total_tokens
    if limit > 0:
        for lvl in config.warning_levels:
            thresh = int(limit * lvl)
            if record.total_tokens >= thresh:
                already = any(abs(warned - lvl) < 1e-4 for warned in record.warned_levels)
                if not already:
                    new_levels.append(lvl)

        if new_levels:
            highest = max(new_levels)
            pct = int(highest * 100)
            messages.append(
                f"⚠️ Token usage reached {pct}% ({record.total_tokens:,}/{limit:,} tokens in {config.scope} scope)"
            )

    should_freeze = False
    freeze_reason = ""
    if config.freeze_on_limit and limit > 0 and record.total_tokens >= limit:
        should_freeze = True
        freeze_reason = f"Limit reached ({record.total_tokens:,}/{limit:,} tokens)"
        messages.append(f"🛑 Freeze triggered: {freeze_reason}")

    return EvaluationResult(
        should_warn=bool(new_levels),
        new_warning_levels=new_levels,
        should_freeze=should_freeze,
        freeze_reason=freeze_reason,
        message="\n".join(messages),
    )
