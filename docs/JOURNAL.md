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
