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

## 2026-10-21 — Phase 2 规划插件与状态机

**改了什么**

- 新增 `app/planning.py`：`Plan`/`PlanStep`、合法状态迁移、SQLite/session 双写、重启恢复、重规划上限、确定性验收（文件/内容/命令/退出码/manual）。
- 新增 `plugins/planning`：LLM 规划器 `create/revise`，注册 `ctx.planners.default`；HTTP `status/reset`。
- 新增 `plugins/classifier`：通过 `classify.prompt` hook 返回 `chat/task/continuation/control`；`app/classifier.py` 保留规则兜底。
- 改写 `app/kernel/loop.py`：启动/恢复计划、逐步记录、连续失败/重复动作/验收失败触发重规划、`done` 前强制验收。
- 改写 `app/main.py` 路由：新任务清旧计划，`continuation` 保留计划，`control` 支持 stop/status/restart，`/api/state` 暴露 `plan/plan_text`。
- `plans` schema v2：新增 `acceptance/current_step/revision`，启动自动补列；`Store` 增加 `update_plan/get_plan`。
- `session` 补 `snapshot` 与 getter。
- 配置新增 `AGENT_MAX_REPLANS=2`、`AGENT_PLAN_MAX_STEPS=8`。
- 修复：计划保存时先拿 DB id 再写 state；恢复时跳过终态计划；补齐 `KernelRuntime.emit_hook`；补插件 README；移除 `/api/stop` 对不存在函数的调用。

**为什么**

- Phase 1 的计划是 loop 内联、未类型化，失败只能整轮重来，重启无法恢复。
- 将“LLM 怎么规划”放插件、“计划合法性与验收”放内核，既支持热替换，也保证状态机一致。

**验证**

- `make check`：40 passed，`compileall` 通过。
- 单测覆盖：Plan/Step JSON、状态迁移、文件/命令验收、plans 持久化、真实插件加载、classifier hook、loop 创建-执行-验收-重规划、running 状态 control。
- 临时根目录 E2E：4 插件加载；`planning.status`、`/api/chat` control 路由、`/api/state.plan` 均正常。
- 线上服务 idle 后重启；4 插件加载，plans 表自动补 `acceptance/current_step/revision`（schema v2），SSE 200。

**下一步**

- Phase 3：上下文插件（Section + token 预算 + artifact 卸载）。

## 2026-10-21 — Phase 3：上下文插件

**做了什么**

- 新增 `app/context.py`：`Section` 模型、token 估算、按 `priority` 保留/按 `order` 渲染、预算截断/丢弃、artifact 卸载、ledger。
- 新增 `plugins/context_base`（system/task）、`context_plan`（plan_text）、`context_memory`（提炼记忆）、`context_window`（逐条历史）。
- `ContextRegistry.entries()`、`PluginManager.context_builders()`、`KernelRuntime.context_builders()`；plugin snapshot 暴露 builder 名称。
- loop 与 chat 改用 `context.build()`；大工具结果经 `offload_result()` 落盘到 `state/artifacts/<conv_id>/`，history/event 只写摘要。
- `/api/context` 返回 messages、parts、ledger；新增 `context.compress` hook 作为可替换压缩后端。
- 配置新增 `AGENT_CONTEXT_BUDGET=12000`、`AGENT_CONTEXT_PRESSURE=0.7`、`AGENT_CONTEXT_MEMORY_MAX_CHARS=6000`、`AGENT_ARTIFACT_OFFLOAD=1`、`AGENT_ARTIFACT_THRESHOLD=2000`。
- 更新 ARCHITECTURE / CONTRACTS / DECISIONS ADR-012 / ROADMAP / STATUS / README / AGENTS。

**为什么**

- 旧 `session.build_messages()` 硬编码，无法按 token 预算裁剪，也不能热替换记忆/历史策略；大工具结果仍挤占窗口。

**验证**

- `make check`：**50 passed**，`compileall` 通过；`git diff --check` 通过。
- 单测覆盖：预算保留/丢弃、token 截断、压力触发 `context.compress`、fallback 系统段兜底、artifact 卸载/防路径逃逸、4 个真实 context 插件加载、runtime builder 接线。
- 临时根目录 E2E：8 插件加载，`/api/context` sections/ledger 正常，budget=500 时 system 超预算后正确丢弃可选段。
- 线上 idle 后重启：8 插件加载，`/api/context` 使用 7557/12000 tokens，fallback=false、builder_errors=[]。

**下一步**

- Phase 4：类型化特权操作、root helper、审批/审计、快照/回滚。
- 上下文增强：内置 LLM 压缩后端、artifact 清理策略。

## 2026-10-21 — 修复概念问题被误判为任务

**现象**

- 用户问“事件经 SSE 推前端、状态持久化到 SQLite。sse是什么，sqlite是什么”，被 classifier 判为 `task`。
- planning 生成了 `docs/sse_sqlite.md` 的验收计划；模型直接 `done` 回答概念，验收失败，重规划 2 次后计划 failed。
- 过程中模型还返回过 `{"kind":"assistant","message":"..."}`，被 runtime 报“动作缺少 action 字段”。

**根因**

- `app/classifier.py` 与 `plugins/classifier` 的规则只有控制/继续/问候，其余默认 `task`；没有识别疑问/解释类问题。
- 任务模式下的 done 必须满足计划验收，回答概念不能替代计划文件。

**修复**

- classifier 增加 `QUESTION_PATTERNS` 与 `EXPLICIT_TASK_PATTERNS`：
  - `是什么`/`什么是`/`区别`/`解释`/`介绍` 等归 `chat`；
  - “帮我写/创建/查看/分析……”等显式动作仍归 `task`。
- 新增单测覆盖用户原句、概念问题、显式写作任务；`make check` 51 passed。
- 线上重启后用原句调用 `/api/chat`，返回 `mode=chat`，正常直接回答，不再创建计划。

**注意**

- 该会话中旧的 failed 计划仍在 `state`，可用 `POST /api/plugin/planning/reset` 清理；新逻辑不会再为概念问题建计划。

## 2026-10-21 — P0：意图判定与计划护栏

**做了什么**

- classifier 增加概念/解释问题识别：`是什么`、`什么是`、`区别`、`解释`、`介绍`、`为什么` 等归 `chat`。
- 显式动作（`帮我写/创建/查看/分析/修复/部署`）仍归 `task`；插件 hook 若误返回 task，core 会做一次概念问题降级。
- planning prompt 明确：纯解释/问答不规划；不得发明用户未要求的交付物，acceptance 只验收明确要求或实际产出。
- loop 在执行前校验 action：缺失/非字符串 action 或未知 action 时，返回定向修复提示（含可用动作和示例），不进入工具/计划记录。
- chat 模式新增 `deepseek.chat_text()` 返回原始文本，不再复用动作 JSON 解析；修复答案含 JSON 时只保存 JSON 片段的问题。
- 测试 conftest 强制测试 token，避免 shell 环境污染导致 SSE/control 测试偶发失败。

**验证**

- `make check`：**54 passed**；`compileall` 通过。
- 新增测试：用户原句/概念问题/“为什么部署失败”/“什么时候…”归 chat；显式写作仍 task；插件误判 task 时 core 降级；非法/未知 action 后能继续并完成计划；chat_text 与动作 JSON 分离。

**注意**

- 线上重启后回归：原句与“什么时候…”均返回 `mode=chat`，计划 id 未变化，无新 plan 事件；8 插件无加载错误。

## 2026-10-22 — 发布 v1.2.0

**做了什么**

- 根目录新增 `VERSION=1.2.0`，`app/version.py` 解析主/次/补丁号并按 MINOR 奇偶推导 `stable/development`。
- `app/__init__.py` 暴露 `__version__`；FastAPI、`/api/version`、`/api/state.version`、`app.cli status` 均可读取版本。
- 新增 `docs/VERSIONING.md`：主版本阶段、单数开发/偶数稳定、补丁为功能更新，以及发布流程。
- 记录 ADR-013；更新 README、AGENTS、STATUS。
- 发布线：`v1.2.0` stable；下一开发线 `v2.1.0`。

**验证**

- `make check`：**56 passed**，`compileall` 通过。
- 版本单测覆盖 `VERSION`、奇偶通道、`/api/version` 和 `/api/state.version`。

**下一步**

- 以 `v2.1.0` 开始 Phase 4：类型化特权操作、审批/审计、快照/回滚。


## 2026-10-22 — v1.2.0 GitHub 包装

**做了什么**

- 新增 MIT License、英文主 README 和中文 `README.zh-CN.md`；统一标题为 Self-Evolving Agent，补徽章、演示 GIF、截图、安全与路线图。
- 新增 `docs/assets/demo.gif`、`social-preview.png`、两张压缩 UI 截图；GIF 由真实 UI 截图和标题卡生成，后续可替换为真人录屏。
- 新增 `install.sh`：创建 venv、交互式写入 `.env`、生成随机 token、动态生成 systemd unit/logrotate 并启动服务；`deploy.sh` 复用该脚本。
- 新增 CI workflow、SECURITY、CONTRIBUTING、issue/PR 模板、v1.2.0 Release notes 和 GitHub 发布清单。
- 版本仍为 v1.2.0；下一开发线 v2.1.0。

**验证**

- `make check`：**56 passed**，`compileall` 通过。
- `bash -n install.sh deploy.sh` 通过；YAML 模板解析通过。
- 需要仓库管理员手动完成的 GitHub 设置：About 描述/topics、social preview、Release 页面（当前环境没有 GitHub CLI 认证）。

**下一步**

- 用干净 Ubuntu VM 验证 `./install.sh`，然后创建 v1.2.0 Release 并开始 v2.1.0。

## 2026-10-22 — 许可证改为 Unlicense

- 将 MIT License 改为 **Unlicense**（公有领域），去掉署名要求，作为最宽松的开源选项。
- 同步更新 README、中文 README、STATUS、v1.2.0 Release notes 和演示 GIF 的许可证文案。

## 2026-10-22 — 许可证改回 MIT

- 按用户要求将 Unlicense 改回 **MIT License**，恢复版权声明与许可声明保留要求。
- 同步更新 README、中文 README、STATUS、v1.2.0 Release notes 和演示 GIF 的许可证文案。

## 2026-10-22 — 修复新机器 root 安装报错

**现象**

- 新机器以 `root` 登录，执行 `./install.sh` 被旧守卫直接拒绝。

**修复**

- 直接 root 登录改为允许安装，但明确警告服务将以 root 运行。
- 通过普通用户 + `sudo` 调用时，脚本先降回该普通用户，避免 venv、`.env`、`state` 被 root 拥有。
- systemd unit/logrotate 使用实际 `SERVICE_USER`/`SERVICE_GROUP`。
- README 中英文补充 root 安装说明。

**验证**

- `bash -n install.sh` 通过；root 分支用 mock `sudo`/`systemctl` 跑通。
