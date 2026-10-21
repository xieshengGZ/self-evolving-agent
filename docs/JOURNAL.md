# Journal

> 追加式会话记录。每次会话结束写一段：改了什么、为什么、验证结果、下一步。

## 2026-10-21 — Phase 0 + Phase 1 骨架

**改了什么**

- 新增 `AGENTS.md`、`docs/{ARCHITECTURE,CONTRACTS,DECISIONS,ROADMAP,STATUS,JOURNAL}.md`。
- 新增 `app/kernel/`：`registry` / `plugins` / `runtime` / `store` / `bus` / `context` / `http`。
- `agent_loop` 与提示词加载、动作解析从 `app/main.py` 抽到 `app/kernel/loop.py`。
- 工具执行改为经 `runtime.execute_tool` → `plugins/tools_basic`。
- 新增 `plugins/hello` 作为热加载演示。
- 新增 `/api/plugins`、`/api/plugins/{name}/reload`、`/api/plugin/{name}/{op}`。
- 新增 `app/cli.py`（status / map / handoff / reload）与 `Makefile`。
- 修复 `actions.py` 中不存在的 `config.CMD_TIMEOUT`，统一为 `AGENT_CMD_TIMEOUT`。
- 删除 `actions.py` 中重复的 `execute_action` / `parse_action`（分发已归内核）。
- 插件加载改为直接 `compile` 源码，绕开同秒改写时 `importlib` 复用旧 `.pyc` 的问题。
- `.gitignore` 放行 `.env.example`，并补充 `AGENT_PLUGIN_AUTORELOAD` 示例。

**为什么**

- 让工具、规划、上下文能以插件形式扩展，且热替换不重启进程。
- 把“项目现状”从聊天记录搬到文件，降低下次接手的成本。

**验证**

- `compileall` 通过。
- `pytest` 通过（详见 `STATUS.md`）。
- 热替换：修改 `hello` 后调用 reload，新 handler 生效，旧版本在失败时保留。

**下一步**

- Phase 1 完整化：SSE 事件流 + SQLite 存储。
- Phase 2：规划插件。

## 2026-10-21 — Phase 1 完整化（SSE + SQLite）

**改了什么**

- 新增 `app/storage.py`：SQLite（WAL）`sessions/messages/events/plans/meta`，一次性迁移 `state/session.json`。
- `app/session.py` 保留内存工作窗口，`add_event`/`add_message` 即时落库；新增事件订阅队列，支持 SSE 跨线程推送。
- 新增 `app/kernel/sse.py` 与 `GET /api/events`：backlog、`log`/`state` 事件、`Last-Event-ID`、15s 心跳、token 查询参数。
- 前端 `index.html` 从 `setInterval` 轮询改为 `EventSource`；`/api/state` 新增 `since_id` 兼容增量读取。
- 新增 `app/migrate.py`、`app.cli migrate`、`make migrate`；`app.cli status` 改为只读数据库。
- `Store` 增加 `list_sessions` / `save_plan` / `get_plans` / `wait_events` / `db_path`。
- 修复 `memory.new_conv_id()` 同秒内重复的问题（随机后缀）。

**为什么**

- 轮询延迟高且每秒产生请求；SSE 让事件、状态、历史增量实时到达。
- 单文件 JSON 全量重写会随历史增长放大；SQLite 支持按会话保留完整消息与计划，为 Phase 2 恢复打底。

**验证**

- `pytest` 26 passed；`compileall` 通过。
- 用真实 `state/session.json` 副本迁移：36 messages / 76 events / 1 plan，二次迁移自动 no-op。
- SSE：初始 backlog + state、跨线程事件推送、token 鉴权测试通过。
- `app.cli status` 只读会话/事件，不触发迁移或状态写入。

**下一步**

- Phase 2：`planning` 插件与状态机。
- 部署：线上 `agent-services` 于 15:33 由外部启动；已验证 `/api/events`、`state/agent.db` 和旧数据迁移，无需再安排停服窗口。
