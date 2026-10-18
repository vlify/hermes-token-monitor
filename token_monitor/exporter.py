"""Prometheus textfile exporter for hermes-token-monitor."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from token_monitor.ledger import TokenLedger


class PrometheusExporter:
    """Exports token usage metrics in Prometheus exposition format to a textfile."""

    def __init__(self, export_path: str):
        self.export_path = Path(export_path).expanduser().resolve() if export_path else None

    def export(self, ledger: TokenLedger, active_session_id: str | None = None) -> None:
        if not self.export_path:
            return

        lines = [
            "# HELP hermes_tokens_total Total tokens consumed by Hermes Agent",
            "# TYPE hermes_tokens_total counter",
        ]

        # 1. Active session metrics
        if active_session_id:
            s_stats = ledger.get_session_stats(active_session_id)
            lines.append(
                f'hermes_tokens_total{{scope="session",session_id="{active_session_id}",type="input"}} {s_stats.input_tokens}'
            )
            lines.append(
                f'hermes_tokens_total{{scope="session",session_id="{active_session_id}",type="output"}} {s_stats.output_tokens}'
            )
            lines.append(
                f'hermes_tokens_total{{scope="session",session_id="{active_session_id}",type="total"}} {s_stats.total_tokens}'
            )

        # 2. Daily metrics
        d_stats = ledger.get_daily_stats()
        lines.append(f'hermes_tokens_total{{scope="daily",type="input"}} {d_stats.input_tokens}')
        lines.append(f'hermes_tokens_total{{scope="daily",type="output"}} {d_stats.output_tokens}')
        lines.append(f'hermes_tokens_total{{scope="daily",type="total"}} {d_stats.total_tokens}')

        # 3. All-time metrics
        a_stats = ledger.get_all_time_stats()
        lines.append(f'hermes_tokens_total{{scope="all_time",type="input"}} {a_stats.input_tokens}')
        lines.append(f'hermes_tokens_total{{scope="all_time",type="output"}} {a_stats.output_tokens}')
        lines.append(f'hermes_tokens_total{{scope="all_time",type="total"}} {a_stats.total_tokens}')

        lines.append("")
        content = "\n".join(lines)

        self.export_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.export_path.with_name(f".tmp.{uuid.uuid4().hex}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, self.export_path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
