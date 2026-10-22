# Decisions (ADR)

> 只追加，不删除。格式：日期 / 背景 / 决定 / 后果。

## ADR-001 — 稳定内核 + 插件层

- **日期**：2026-10-21
- **背景**：`app/main.py` 同时承担路由、循环、规划、上下文、工具分发，新增功能必须改动核心并重启。
- **决定**：内核保持在 `app/kernel/`，只负责顺序、预算、取消、持久化、事件；能力拆到 `plugins/<name>/`，通过 `ctx` 注册。
- **后果**：新增工具/规划器/上下文策略不需要改内核；内核变更仍需重启（Python 无法安全热补丁）。

## ADR-002 — 插件清单用 JSON 而不是 YAML

- **日期**：2026-10-21
- **背景**：dsh 使用 YAML 清单，但 Python 项目当前依赖里没有 PyYAML，且清单只需要简单结构。
- **决定**：`plugin.json`（stdlib `json`）。
- **后果**：零新增运行时依赖；未来若需要复杂配置，再引入 schema 校验或 YAML。

## ADR-003 — 每个插件独立 RegistrySet，原子替换

- **日期**：2026-10-21
- **背景**：`importlib.reload` 容易留下旧引用和半更新状态。
- **决定**：每次加载为插件创建全新的 `RegistrySet`，用内容 hash 生成唯一模块名；先 `setup` 到 staging，健康检查通过后加锁替换，最后 `teardown` 旧版。
- **后果**：热替换失败时旧版本继续工作；内存中会短暂同时存在新旧模块。

## ADR-004 — HTTP 不动态增删路由

- **日期**：2026-10-21
- **背景**：FastAPI 的路由表不适合运行时增删，且会让热替换不可控。
- **决定**：所有插件 HTTP op 走固定入口 `POST /api/plugin/{name}/{op}`，由内核查表分发。
- **后果**：热替换不需要触碰 FastAPI；插件的 HTTP 能力受限于“单个 op 处理器”模型。

## ADR-005 — 内核变更允许重启

- **日期**：2026-10-21
- **背景**：用户要求“升级新功能不需要重启”。在 Python 中对任意核心代码做热补丁无法保证一致性。
- **决定**：区分“能力扩展”（插件，热替换）与“内核修复”（重启或后续蓝绿）。不承诺内核热补丁。
- **后果**：大部分迭代靠插件完成；内核应保持小而稳定。

## ADR-006 — 存储先做 seam，不立即迁 SQLite

- **日期**：2026-10-21
- **背景**：当前 `state/session.json` 是单文件全量写入，Phase 1 计划迁 SQLite，但一次性迁移风险高。
- **决定**：先引入 `app/kernel/store.py` 作为稳定接口，内部委托现有 `session`；Phase 1 完整化时只改 Store 实现。
- **后果**：插件不直接依赖 `session`；迁移 SQLite 时插件无感。

## ADR-007 — 文档即记忆

- **日期**：2026-10-21
- **背景**：项目会持续变大，跨会话容易丢失上下文。
- **决定**：`AGENTS.md` 作为唯一入口，`docs/STATUS.md` 作为唯一现状真源，`docs/JOURNAL.md` 记录每次会话，`docs/DECISIONS.md` 记录决策；CLI `app.cli status/handoff` 输出机器可读现状。
- **后果**：每次会话结束必须更新文档；文档过期视为缺陷。

## ADR-008 — SQLite 作为持久层，session 保留为热状态

- **日期**：2026-10-21
- **背景**：`state/session.json` 全量重写，事件/历史增长后写放大明显；Phase 1 需要可恢复的会话、消息、事件与计划。
- **决定**：`app/storage.py` 负责 SQLite（WAL）表 `sessions/messages/events/plans/meta`；`app/session.py` 继续保存内存工作窗口与事件环，但 `add_event`/`add_message` 立即落库，`Store` 对插件保持稳定 API。
- **后果**：热路径不查库；崩溃后可从 SQLite 恢复最近窗口与事件。历史消息全量保留，内存窗口仍受 `AGENT_WINDOW_SIZE` 限制。`state/session.json` 只读迁移，不再写入。

## ADR-009 — SSE 使用 EventSource，token 允许查询参数

- **日期**：2026-10-21
- **背景**：前端每秒轮询 `/api/state`，延迟高、请求多；`EventSource` 不能设置 `Authorization` 头。
- **决定**：新增 `GET /api/events`，支持 `Authorization` 或 `?token=`；服务端通过每连接 `asyncio.Queue` 推送 `log`/`state` 事件，支持 `Last-Event-ID` 重连与 15s 心跳。HTTP 路由保持不变，前端用 EventSource 替代 `setInterval`。
- **后果**：token 可能出现在访问日志中，仅适用于当前单用户、可信托管环境；未来可换短时票据或同源 Cookie。SSE 只读，写操作仍走 POST。

## ADR-010 — 计划状态机在内核，LLM 规划在插件

- **日期**：2026-10-21
- **背景**：Phase 1 的 `kernel/loop.py` 直接拼规划提示词、解析计划、按动作推进；计划是未类型化的 list，失败后只能整轮重来，且重启后无法可靠恢复。
- **决定**：`app/planning.py` 提供 `Plan`/`PlanStep`、合法状态迁移、重规划计数、确定性验收和 SQLite/session 持久化；`plugins/planning` 只负责 LLM 提示词与 `create/revise`。内核通过 `ctx.planners.default` 获取规划器，插件缺失/失败时降级到旧 `app/planner.py`。
- **后果**：规划策略可热替换，状态机与验收保持内核一致；`plans` 表升级到 schema v2，新增 `acceptance/current_step/revision`，启动时自动补列。计划验收命令与 agent 工具同权限，仍受危险命令拦截和超时约束。

## ADR-011 — 分类器复用 hook bus，不新增注册表

- **日期**：2026-10-21
- **背景**：`classifier` 原为 `app/classifier.py` 规则匹配；为了可替换分类策略，需要插件入口，但为一个单函数能力新增注册表会扩大内核 API。
- **决定**：内核在 `classifier.classify()` 中发布 `classify.prompt` 钩子，按插件加载顺序取第一个合法返回值；插件缺失、抛异常或返回非法值时回退规则匹配。模式固定为 `chat/task/continuation/control`。
- **后果**：`classifier` 可热替换且无需提升 `api_version`；钩子模型保持“旁路增强”，主流程不会因分类插件故障中断。

## ADR-012 — 上下文用 Section + 内核预算，插件只提供片段

- **日期**：2026-10-21
- **背景**：`session.build_messages()` 硬编码 system/memory/task/plan/history，无法按 token 预算裁剪，也无法热替换记忆/历史策略；工具大结果虽然截断，但仍会挤占窗口。
- **决定**：`app/context.py` 定义 Section 模型、token 估算、预算排序/截断/丢弃和 artifact 卸载；`context_base/context_plan/context_memory/context_window` 通过 `ctx.context` 注册 section builder。内核按 `order` 渲染、按 `priority` 保留；超预算时先调用可选的 `context.compress` 钩子，否则确定性压缩。`session.build_messages()` 仅作 fallback。
- **后果**：上下文策略可单独热替换；预算和顺序由内核统一保证。token 估算是启发式（非 tokenizer 精确值），压缩模型后端尚未内置，当前以确定性裁剪 + hook 扩展点为主；artifacts 需要后续定期清理策略。

## ADR-013 — 版本号编码开发/稳定状态

- **日期**：2026-10-22
- **背景**：项目进入可发布阶段，需要让人和 Agent 只读版本号就能判断当前是开发线还是稳定版，并明确下一阶段从哪个版本继续。
- **决定**：采用 `MAJOR.MINOR.PATCH`：主版本号标识阶段；`MINOR` 单数为开发中、偶数为稳定版；`PATCH` 表示同一稳定阶段内的功能更新。当前冻结为 `v1.2.0`，后续开发从 `v2.1.0` 开始。版本唯一来源是根目录 `VERSION`，由 `app/version.py` 读取并通过 `/api/version`、`/api/state.version` 和 CLI 暴露。
- **后果**：即使不读提交历史，也能从版本号判断成熟度；稳定版发布必须通过测试和线上回归，并打带注释 Git 标签。新增版本相关说明维护在 `docs/VERSIONING.md`。
