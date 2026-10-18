# hermes-token-monitor

A robust, thread-safe, and process-safe token usage monitoring and threshold enforcement plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-11%20passed-brightgreen.svg)]()
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)]()

---

## Highlights

* **True Multi-Session Isolation**: Completely resolves legacy state clobbering (`A100 -> B200 -> A50` correctly accumulates $150$ for session A and $200$ for session B).
* **POSIX File Locking (`fcntl.flock`)**: Safe atomic read-modify-write transactions across concurrent threads and background subagents, preventing lost counts or race conditions.
* **Smart Alert State Machine**: Decouples warning state between sessions and daily scopes. Eliminates repeated alert loops when running `/new`.
* **Execution Guard & Freezing**: Optional hard freeze (`freeze_on_limit = true`) that intercepts `pre_tool_call` when quota is exhausted.
* **Prometheus Metrics Exporter**: Optional textfile exporter for `node_exporter` / scraping systems.

---

## Architecture

```text
token_monitor/
├── __init__.py      # Hermes plugin registration & lifecycle entrypoint
├── config.py        # Typed configuration & environment parser
├── ledger.py        # Atomic POSIX-locked token persistence ledger
├── evaluator.py     # Threshold alert & freeze state machine
├── hooks.py         # Lifecycle hooks (post_api_request, pre_tool_call)
└── exporter.py      # Prometheus exposition format writer
```

---

## Configuration

Configure in your Hermes `config.yaml` or via environment variables:

```yaml
plugins:
  token_monitor:
    scope: "session"                # "session" | "daily" | "all_time"
    limit_total_tokens: 20000000    # Total quota limit (e.g. 20M tokens)
    warning_levels: [0.5, 0.8, 0.95] # Trigger notifications at 50%, 80%, 95%
    freeze_on_limit: false          # Intercept tool calls when limit is reached
    notify_console: true            # Log alerts to stderr/stdout
    prom_export_path: "/tmp/hermes_tokens.prom" # Optional Prometheus metrics path
```

### Environment Overrides

* `HERMES_TOKEN_MONITOR_CONFIG`: Path to JSON/YAML configuration file.
* `HERMES_TOKEN_MONITOR_STATE`: Path to state persistence JSON file (defaults to `~/.local/state/hermes/token_monitor/state.json`).

---

## Testing

Run unit and concurrency tests locally:

```bash
uv run --with pytest pytest
```

Run static analysis:

```bash
uv run --with ruff ruff check .
```

---

## License

[MIT License](LICENSE) © 2026 vlify
