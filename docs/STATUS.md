# STATUS

> 唯一现状真源。每次会话结束必须更新。目标 ≤ 60 行。

## 当前阶段

Phase 0 / Phase 1 完成：稳定内核、插件热插拔、SSE 事件流、SQLite 持久化。
下一阶段：Phase 2 任务规划插件。

## 上次完成（2026-10-21）

- 建立 `AGENTS.md` + `docs/` 项目记忆
- 拆分 `app/kernel/`，`agent_loop` 从 `main.py` 抽离
- 插件加载器：`plugin.json`、原子热替换、失败保留旧版
- `tools_basic` 迁移为插件；`hello` 演示插件
- 新增 `/api/plugins`、`/api/plugins/{name}/reload`、`/api/plugin/{name}/{op}`
- 修复 `config.CMD_TIMEOUT` 拼写错误
- 插件源码改为 `compile` + `exec` 加载，避免同秒改写命中旧 `.pyc`
- 新增 pytest 基线测试与 `app.cli`
- `.gitignore` 放行 `.env.example`，`.env.example` 补插件热加载开关
- SQLite：`app/storage.py` + `state/agent.db`（sessions/messages/events/plans/meta）
- SSE：`GET /api/events`，前端 EventSource 替代 1s 轮询；支持 `Last-Event-ID`
- 旧 `state/session.json` 启动时自动迁移；`python -m app.migrate` 可手动执行
- 修复 `memory.new_conv_id()` 同秒碰撞（加随机后缀）
- `app.cli status` 直接读 SQLite，不触发迁移/会话状态写入

## 已知问题

- 规划器仍在 `kernel/loop.py` 直接调用，未插件化（Phase 2）
- 上下文仍在 `session.build_messages` 硬编码，未插件化（Phase 3）
- SSE token 走查询参数，可能进入访问日志（单用户环境可接受，ADR-009）
- `classifier` 仍是规则匹配
- `state/session.json` 迁移后原文件保留，确认稳定后可手动归档

## 下一步（3）

1. Phase 2：`planning` 插件（Plan 模型 + 状态机 + 恢复）
2. Phase 2：确定性验证器 + `classifier` 插件化
3. Phase 3：`context` 插件（分层预算 + artifact 卸载）

## 插件状态

| 插件 | 版本 | 状态 | 能力 |
|---|---|---|---|
| `tools_basic` | 0.1.0 | active | shell / read_file / write_file / self_restart / 符号工具 |
| `hello` | 0.1.0 | experimental | 热加载演示：`hello_ping` 工具 + `ping` HTTP op |

## 最近验证

- 2026-10-21：`pytest` 26 passed；`compileall` 通过；SQLite 迁移用真实 `session.json` 副本验证（36 messages / 76 events / 1 plan）；SSE backlog + 跨线程推送测试通过；`app.cli status` 不触发迁移。
- 注意：systemd 服务在 14:39 被外部 SIGTERM 停止，当前 inactive；新代码尚未部署到线上进程。
