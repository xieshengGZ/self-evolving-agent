# tools_basic

核心工具插件，承接原 `app/actions.py` 的动作分发。

## 提供的工具

| 工具 | 说明 |
|---|---|
| `shell` | 执行 shell 命令，超时杀进程组 |
| `write_file` | 整文件写入，写前备份 |
| `read_file` | 读取文件，按 `max_bytes` 截断 |
| `self_restart` | 备份 + 语法检查 + execv 重启 |
| `read_symbol` | 按符号路径读取源码 |
| `read_file_summary` | 输出文件符号签名摘要 |
| `rebuild_index` | 重建 `.index.json` |

## 依赖

- `app.actions` 的 `do_*` 实现
- `app.config.AGENT_CMD_TIMEOUT`

## 状态

`active`。Phase 2/3 会在此基础上增加规划与上下文插件，不要在本插件里写业务编排。
