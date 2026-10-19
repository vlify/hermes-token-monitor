"""Threshold evaluation and alert state machine for hermes-token-monitor."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import MonitorConfig
from .ledger import UsageRecord


@dataclass
class EvaluationResult:
    should_warn: bool = False
    new_warning_levels: list[float] = field(default_factory=list)
    should_freeze: bool = False
    freeze_reason: str = ""
    message: str = ""
    metric: str = "total"
    used: int = 0
    limit: int = 0


def budget_progress(record: UsageRecord, config: MonitorConfig) -> tuple[str, int, int]:
    """Use the most consumed enabled quota for warnings and the reply footer."""
    quotas = [(name, getattr(record, f"{name}_tokens"), getattr(config, f"limit_{name}_tokens"))
              for name in ("total", "input", "output")
              if getattr(config, f"limit_{name}_tokens") > 0]
    return max(quotas, key=lambda item: item[1] / item[2]) if quotas else (
        "total", record.total_tokens, 0)


def evaluate_usage(record: UsageRecord, config: MonitorConfig) -> EvaluationResult:
    """Evaluate current record against configured thresholds and limits."""
    new_levels: list[float] = []
    messages: list[str] = []

    metric, used, limit = budget_progress(record, config)
    if limit > 0:
        for lvl in config.warning_levels:
            thresh = limit * lvl
            if used >= thresh:
                already = any(abs(warned - lvl) < 1e-4 for warned in record.warned_levels)
                if not already:
                    new_levels.append(lvl)

        if new_levels:
            highest = max(new_levels)
            pct = int(highest * 100)
            messages.append(
                f"Token {metric} usage reached {pct}% ({used:,}/{limit:,} tokens in {config.scope} scope)"
            )

    should_freeze = False
    freeze_reason = ""
    if config.freeze_on_limit and limit > 0 and used >= limit:
        should_freeze = True
        freeze_reason = f"Limit reached ({used:,}/{limit:,} {metric} tokens)"
        messages.append(f"🛑 Freeze triggered: {freeze_reason}")

    return EvaluationResult(
        should_warn=bool(new_levels),
        new_warning_levels=new_levels,
        should_freeze=should_freeze,
        freeze_reason=freeze_reason,
        message="\n".join(messages),
        metric=metric,
        used=used,
        limit=limit,
    )
