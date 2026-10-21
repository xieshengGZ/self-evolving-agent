# STATUS

> 唯一现状真源。每次会话结束必须更新。目标 ≤ 60 行。

## 当前阶段

Phase 0 完成；Phase 1 骨架完成（稳定内核 + 插件热插拔），完整化待做。

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

## 已知问题

- 规划器仍在 `kernel/loop.py` 直接调用，未插件化（Phase 2）
- 上下文仍在 `session.build_messages` 硬编码，未插件化（Phase 3）
- 存储仍是 `state/session.json` 单文件，`kernel/store.py` 只是 seam
- 前端仍轮询 `/api/state`，未使用事件流
- `.env.example` 被 `.gitignore` 的 `.env.*` 忽略（历史问题）
- `classifier` 仍是规则匹配

## 下一步（3）

1. Phase 1 完整化：SSE 事件流 + SQLite 存储迁移
2. Phase 2：`planning` 插件（Plan 模型 + 状态机 + 恢复）
3. Phase 3：`context` 插件（分层预算 + artifact 卸载）

## 插件状态

| 插件 | 版本 | 状态 | 能力 |
|---|---|---|---|
| `tools_basic` | 0.1.0 | active | shell / read_file / write_file / self_restart / 符号工具 |
| `hello` | 0.1.0 | experimental | 热加载演示：`hello_ping` 工具 + `ping` HTTP op |

## 最近验证

- 2026-10-21：`pytest` 15 passed；`compileall` 通过；`app.main` 导入冒烟通过（22 routes，8 tools，0 plugin errors）；`app.cli status/map` 正常；热替换测试通过（失败保留旧版）。
