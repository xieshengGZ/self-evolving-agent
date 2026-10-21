# STATUS

> 唯一现状真源。每次会话结束必须更新。目标 ≤ 60 行。

## 当前阶段

Phase 0 / 1 / 2 完成：稳定内核、插件热插拔、SSE + SQLite、规划状态机。
下一阶段：Phase 3 上下文管理插件。

## 上次完成（2026-10-21）

- 建立 `AGENTS.md` + `docs/` 项目记忆；拆分 `app/kernel/`
- 插件加载器：`plugin.json`、原子热替换、失败保留旧版；`tools_basic` / `hello`
- SQLite `state/agent.db`（sessions/messages/events/plans/meta）+ `session.json` 一次性迁移
- SSE `GET /api/events`，前端 EventSource 替代 1s 轮询；支持 `Last-Event-ID`
- Phase 2：`app/planning.py`（Plan/PlanStep、状态机、恢复、确定性验收）
- Phase 2：`plugins/planning`（`ctx.planners.default`，create/revise，status/reset）
- Phase 2：`plugins/classifier`（hook `classify.prompt`，chat/task/continuation/control）
- Phase 2：新任务清旧计划；`continuation` 保留计划；`done` 前强制验收
- `plans` schema v2：`acceptance/current_step/revision`，旧库启动自动补列
- `app/session.py` 补 `snapshot` 与 getter，`plan_text` 注入每轮上下文

## 已知问题

- 上下文仍在 `session.build_messages` 硬编码，未插件化（Phase 3）
- `acceptance` 为空时计划完成即通过；需要强保证时必须写验收项
- `depends_on` 仅展示，尚未参与调度
- `classifier` 依赖插件；插件异常时回退 `app/classifier.py` 规则
- SSE token 走查询参数，可能进入访问日志（单用户环境可接受，ADR-009）
- `state/session.json` 迁移后保留，确认稳定后可手动归档

## 下一步（3）

1. Phase 3：`context` 插件（Section 模型 + token 预算 + 分层优先级）
2. Phase 3：工具结果 artifact 卸载 + `/api/context` 预算展示
3. Phase 3：压力触发压缩与可替换压缩后端

## 插件状态

| 插件 | 版本 | 状态 | 能力 |
|---|---|---|---|
| `tools_basic` | 0.1.0 | active | shell / read_file / write_file / self_restart / 符号工具 |
| `planning` | 0.1.0 | active | LLM 计划 create/revise；`status` / `reset` HTTP op |
| `classifier` | 0.1.0 | active | `classify.prompt` hook；四模式分类 |
| `hello` | 0.1.0 | experimental | 热加载演示：`hello_ping` 工具 + `ping` HTTP op |

## 最近验证

- 2026-10-21：`make check` **40 passed**；`compileall` 通过。
- Phase 2 集成测试：计划创建/执行/验收、失败重规划、重启恢复、新任务清计划。
- control 在 running 状态也可用；修复 `/api/stop` 调用不存在的 `actions.kill_current_process`。
- 临时根目录 E2E：4 插件加载，`planning.status` 正常，`/api/chat` 的“状态”经 classifier 走 control，`/api/state` 含 `plan`。
- 线上服务已于 17:32 重启；4 插件加载，plans 表已补 `acceptance/current_step/revision`，schema_version=2；SSE 正常。
