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
  entries:
    tokenwatch:
      scope: "session"                # "session" | "daily" | "all_time"
      limit_total_tokens: 20000000
      warning_levels: [0.5, 0.8, 0.95]
      freeze_on_limit: false
      notify_console: true
      footer_mode: "on_warning"        # off | on_warning | always
      footer_warning_interval: 1
```

### Environment Overrides

* `HERMES_TOKEN_MONITOR_CONFIG`: Path to JSON/YAML configuration file.
* `HERMES_TOKEN_MONITOR_STATE`: Explicit state file override. In Hermes, the default is the current profile's `tokenwatch/state.json`; standalone use retains `~/.local/state/hermes/token_monitor/state.json`.

## 状态保全与兼容

- getter 不再写入状态文件；损坏 JSON、错误结构或非法计数会抛出 `LedgerStateError` 并保留原文件，不自动清零。先备份和排查损坏原因，不能直接用空账本覆盖历史。
- 正常增量写入时兼容旧 `day/daily`、单会话镜像、sessions 注册表、全局冻结和已发 total 阈值。保留缓存/思考计数、未知字段与原始 warned_keys；不重建旧版本已经丢失的历史。
- 请求计数、去重、阈值和冻结转换在同一 POSIX 文件锁事务内提交。具备 api_request_id 的请求按会话去重，最近 4096 个请求身份的摘要持久化；超出窗口的重放不保证去重。缺少请求身份时不按 token 数猜测重复。
- 默认恢复回复 footer，独立网关通知保持关闭；不新增消息注入或外部投递。footer 走 Hermes 的 `transform_llm_output`，不会重写历史消息；达到软阈值时提示收尾，启用 freeze_on_limit 才通过 action=block 阻止工具。
- 更换插件文件不等于运行网关已加载。主配置和服务操作由用户执行；本轮代码更新不自动重启或主动迁移生产账本。

迁移测试只用合成旧状态，不能读取真实生产账本。完整测试从 Hermes 源码仓的官方 runner 执行，并通过 `-o pythonpath=<本项目绝对路径>` 指定候选；具体命令与结果见本轮修复交接。

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
