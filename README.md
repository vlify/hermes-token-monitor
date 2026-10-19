# hermes-token-monitor

Hermes Agent 的 token 统计与额度插件，插件标识为 `tokenwatch`。按会话、每日或全部历史累计用量；在回复尾部提示额度；可选阻止后续工具调用、导出 Prometheus textfile。

## 安装与启用

通过 Hermes 官方插件管理器安装本仓库的**已发布版本**：

```bash
hermes plugins install vlify/hermes-token-monitor --no-enable
hermes plugins doctor tokenwatch --ci
hermes plugins enable tokenwatch
```

尚未发布的本地改动，先对源码目录验证：

```bash
hermes plugins doctor /absolute/path/to/hermes-token-monitor --ci
```

也可把包含 `plugin.yaml`、根 `__init__.py` 和 `token_monitor/` 的目录放到**目标 profile 的** `plugins/tokenwatch/`，再启用。目标根目录遵循 Hermes 的 profile 和 `HERMES_HOME` 解析，不需要使用作者的目录布局。已有插件请先备份；账本放在插件代码目录之外。

备份目录也必须放在插件扫描目录之外。`plugins/tokenwatch.bak/` 只要仍包含相同名称的 manifest，就可能参与发现并覆盖正式插件；目录名带 `.bak` 不代表禁用。

Python 包另提供 `hermes_agent.plugins` entry point，供 Hermes 环境包管理器安装后发现；仅在另一个 venv 中 pip 安装不会自动进入正在运行的 Hermes。目录安装同时提供 manifest 的设置表单，推荐普通用户使用。

已有进程如何重新加载插件，遵循所用 Hermes 版本的机制；文件更新不能证明运行中的网关已加载。插件不自动重启服务、不改写主配置。

目录插件的业务模块使用 Hermes 分配的模块命名空间，支持宿主的插件热加载，避免全局导入缓存继续执行旧代码。安装新版后仍需由宿主重新发现插件。

## 配置

在目标 profile 的 Hermes 配置中手动设置，或使用支持插件设置表单的 Hermes 界面：

```yaml
plugins:
  enabled:
    - tokenwatch
  entries:
    tokenwatch:
      settings:
        scope: session
        limit_total_tokens: 20000000
        limit_input_tokens: 0
        limit_output_tokens: 0
        warning_levels: [0.5, 0.8, 0.95]
        freeze_on_limit: false
        footer_mode: on_warning
        footer_warning_interval: 1
        language: en
        include_auxiliary_calls: false
```

`language: zh` 使用中文回复提示；日志与管理命令保留英文技术字段。默认只提示、不冻结。额度单位是 token；各额度的 `0` 表示关闭该项，任意启用额度达到上限均可触发冻结。`warning_levels` 是 `(0, 1]` 的小数，`80` 不是 `80%`。警告进度取已启用额度中使用比例最高的一项；同一范围共用一组已发阈值，避免每种额度重复提醒。旧账本已发警告记录继续保留。

| 设置 | 默认值 | 含义 |
|---|---|---|
| `scope` | `session` | `session`、`daily` 或 `all_time` |
| `limit_total_tokens` | `20000000` | 总 token 额度 |
| `limit_input_tokens` / `limit_output_tokens` | `0` | 输入／输出额度 |
| `warning_levels` | `[0.5, 0.8, 0.95]` | 阈值比例，排序并去重 |
| `freeze_on_limit` | `false` | 超限后阻止工具调用 |
| `notify_console` | `true` | 记录新跨越阈值的日志 |
| `footer_mode` | `on_warning` | `off`、`on_warning` 或 `always` |
| `footer_warning_interval` | `1` | 每 N 次该会话的非空回复展示警告 |
| `language` | `en` | 回复提示语言：`en` 或 `zh` |
| `include_auxiliary_calls` | `false` | 是否统计辅助调用 |
| `state_file` | 当前 profile 的 `tokenwatch/state.json` | 自定义账本路径 |
| `prom_export_path` | 空 | Prometheus textfile 路径；空值关闭 |

兼容旧的 `plugins.entries.tokenwatch` 平铺配置与 `config` 子树，也兼容 `hermes-token-monitor` 旧标识。优先级从低到高：

```text
默认值
  → 当前插件条目的平铺配置
  → config 子树
  → settings 子树
  → HERMES_TOKEN_MONITOR_CONFIG 指定的 JSON/YAML 文件
  → HERMES_TOKEN_MONITOR_STATE 指定的账本路径
```

新旧标识同时存在时使用 `tokenwatch`。相对账本和导出路径以当前 profile 为基准，不随启动工作目录变化；`~` 展开到当前用户。未在 Hermes 中运行且未指定 profile 时，独立账本默认位于 `~/.local/state/hermes/token_monitor/state.json`。独立使用 YAML 需要 Hermes YAML 库或 PyYAML；JSON 无额外依赖。

设置在下一次 Hook 调用时重新读取，账本继续保留。显式把不同 profile 指向同一账本，等于主动共享配额和会话 ID 命名空间；默认各自隔离。非法类型、负额度、无效范围会给出包含设置名的错误，不把字符串 `"false"` 当作布尔值。旧 `notify_gateway: true` 会明确报错：独立网关通知未实现，请使用 footer 或指标导出。

本版修正新请求的输入计数口径，但不会追溯重算旧账本：旧版本保存的输入计数可能不含缓存。历史总量原样保留；对已有历史启用输入额度时应考虑这一差异。

## 统计与冻结口径

- 主调用接收 `post_api_request` 中 Hermes 归一化后的 usage。输入统计优先取包含缓存的 `prompt_tokens`；缓存读写作为明细保留，不重复加到总量。思考 token 是输出明细，不额外加到输出／总量。
- 兼容 `input/output_tokens` 和 OpenAI 形状的 SDK usage；provider 原生格式应由 Hermes 归一化，不在插件中重复实现 provider 适配。
- 开启 `include_auxiliary_calls` 后接收 `post_auxiliary_call`，包括 Hermes 经该路径发出的标题、压缩等调用。父会话 ID 存在时归入父会话；缺失身份时使用共享 `default` 桶并记录一次警告，不猜测所属会话。子 Agent 使用事件携带的会话 ID，不自动合并到父会话。
- 每个有 `api_request_id` 的事件按会话去重，最近 4096 个身份摘要持久化；超出窗口的重放不保证去重。没有身份时不按相同 token 数猜测重复。
- 缺失、零值或非法 usage 不生成 token 数，并记录一次诊断。未发出事件的调用无法被插件感知，账本不能视为供应商账单。
- `daily` 按运行进程的本地日期切换，`/new` 不清零每日或全部历史额度。多个进程共享账本时应使用同一时区和本地文件系统。
- `freeze_on_limit` 通过 `pre_tool_call` 返回 `action=block`；不能撤销已发送请求、停止所有后续 API 请求或充当资金硬上限。冻结持久化，修改额度不会自动清除旧冻结。

CLI、网关、定时任务、子 Agent 的覆盖都取决于相应执行路径是否发出上述 Hook。Codex app-server 等特殊运行时需要宿主提供 usage 桥接；仅安装插件不能补齐缺失的事件。提示是否展示也取决于宿主是否调用 `transform_llm_output`，以及其他输出变换插件的执行顺序。

## 查看与解除冻结

```bash
hermes tokenwatch status
hermes tokenwatch status --session SESSION_ID
hermes tokenwatch unfreeze --session SESSION_ID
```

命令使用当前 profile 和配置的 scope。`session` 模式不指定 ID 时，`status` 显示全部历史汇总，`unfreeze` 拒绝执行；`daily`／`all_time` 模式的 `unfreeze` 清除当前共享范围的冻结。先确认／调整额度再解除冻结；用量仍超限时后续新 usage 会重新触发冻结。命令不清空历史计数，不提供隐式重置。

## 数据与支持范围

账本用 POSIX `fcntl.flock` 串行化完整读改写事务，并原子替换。损坏 JSON、非法结构或计数会抛出 `LedgerStateError` 并保留原文件；不自动清零。兼容旧单会话、日统计、全局冻结、缓存／思考计数和未知字段，不恢复旧版本已丢失的历史。账本含会话 ID 和用量，不保存提示词、回复或 API 密钥。

Prometheus 导出是本地 textfile，不自动启动 HTTP 服务或发送数据。会话标签转义特殊字符；导出失败记录诊断，不撤销成功的记账。

声明 Python 3.10+ 的 POSIX 支持范围；实际回归在 Linux/Python 3.11 完成，包构建在 Python 3.14 完成。macOS 仍需宿主实测；原生 Windows、网络文件系统、跨主机分布式记账不在当前支持范围，可在 Linux/WSL 中使用。最低 Hermes 版本尚未完成版本矩阵验证，应使用具有 `load_config_readonly`、profile 路径 API、`post_api_request`、`pre_tool_call` 和 `transform_llm_output` 的版本。辅助统计还需 `post_auxiliary_call`。

暂不提供价格表、费用换算、按 provider/model 分账及差异化额度。这些可以后续扩展，不影响当前按 profile／会话统计的使用范围。

## 验证

官方契约：[插件文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)、[事件 Hook 文档](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/hooks.md)。

测试使用临时 profile、配置和账本，通过对应 Hermes checkout 的官方 runner 执行。例如在 Hermes 源码根目录，指定具有 pytest 的测试解释器：

```bash
HERMES_PYTHON=/path/to/test/python scripts/run_tests.sh -j 2 \
  /absolute/path/to/hermes-token-monitor/tests \
  -o pythonpath=/absolute/path/to/hermes-token-monitor
```

测试包括配置优先级、profile ContextVar 隔离、并发事务、旧状态保全、事件去重、缓存计数、三类额度、辅助统计开关、语言、实际 Hook 阻断和管理命令。测试不读生产账本、不发送通知、不调用真实模型。

### 生产验收记录：2026-10-19

在 Linux/Python 3.14 的实际 Hermes 安装中，使用 `openai-codex` 的 `gpt-6.1-sol`，通过 CLI 完成 A → B → A 三轮真实请求，并逐项核对供应商 usage、插件账本和 Hermes 会话数据库：

| 请求 | 未缓存输入 | 缓存读取 | 输出 | 总量 |
|---|---:|---:|---:|---:|
| 会话 A 首轮 | 2601 | 0 | 10 | 2611 |
| 会话 B 首轮 | 2607 | 0 | 10 | 2617 |
| 会话 A 续聊 | 475 | 2432 | 12 | 2919 |

A 累计 5530 tokens／2 次请求，B 累计 2617 tokens／1 次请求，全局增量恰好 8147 tokens／3 次请求。缓存输入未漏计、未重复计入总量，续聊未清零或串账。测试请求未调用工具，生产额度和主配置保持原样。

官方插件控制接口完成生产网关热加载，两个适配器重新挂接，服务 PID 与启动时间不变。这里只验证了网关加载状态；聊天平台消息到回复的完整链路未包含在这三轮 CLI 实测中。

本轮隔离回归由 Hermes 官方 runner 执行：60 passed，0 failed，包含真实目录加载器更换业务模块的热加载用例；Plugin Doctor 和全仓 Ruff E4/E7/E9/F 检查通过。生产冻结阈值未人为降低，冻结与辅助统计开关由隔离回归验证。

## License

MIT，见 [LICENSE](LICENSE)。
