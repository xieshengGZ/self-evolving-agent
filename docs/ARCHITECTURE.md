# Architecture

> 稳定文档。只有架构变化时才修改。当前实现阶段见 `STATUS.md`。

## 目标

1. **新能力不重启上线**：工具、规划器、上下文策略、HTTP op 都是插件，可热替换。
2. **低耦合**：内核不 import 插件，插件不 import 内核实现细节，只依赖 `ctx`。
3. **可恢复**：每一步可持久化，进程重启后能继续（SQLite 已落地）。
4. **可交接**：新人/AI 读 `AGENTS.md` + `STATUS.md` 就能接手。

## 分层

```
┌────────────────────────────────────────────────────┐
│ 客户端：Web / (未来) 桌面                            │
└───────────────────────┬────────────────────────────┘
                        │ HTTP / SSE
┌───────────────────────▼────────────────────────────┐
│ FastAPI bootstrap (app/main.py)                     │
│  - 鉴权、静态页、兼容旧 API                          │
└───────────────────────┬────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────┐
│ Kernel (app/kernel/)                                │
│  runtime · plugins · registry · bus · store · loop  │
│  只负责：顺序、预算、取消、持久化、事件、生命周期      │
└───────────┬───────────────────────────┬────────────┘
            │ 调用                       │ 加载/热替换
┌───────────▼─────────────┐   ┌─────────▼──────────────┐
│ Plugin layers            │   │ PluginManager           │
│  tools / hooks / http    │   │ discover → setup → swap │
│  planners / context      │   │ teardown → unload        │
└──────────────────────────┘   └────────────────────────┘
            │
┌───────────▼────────────────────────────────────────┐
│ Store（session 热状态 + SQLite 持久化）              │
│  sessions / messages / events / plans               │
└───────────────────────┬────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────┐
│ app/storage.py → state/agent.db (WAL)              │
└────────────────────────────────────────────────────┘
```

## 内核模块

| 文件 | 职责 |
|---|---|
| `app/kernel/registry.py` | `ToolRegistry` / `HookBus` / `HttpRegistry` / `PlannerRegistry` / `ContextRegistry`，每个插件一份 `RegistrySet` |
| `app/kernel/plugins.py` | 扫描 `plugins/*/plugin.json`；版本化模块名加载；staging → 校验 → 原子替换 → teardown 旧版 |
| `app/kernel/runtime.py` | `KernelRuntime` 单例：`Store` + `EventBus` + `PluginManager`；`execute_tool` / `snapshot` |
| `app/kernel/store.py` | 插件可见的存储 API；实时状态走 `session`，查询/计划走 `Storage` |
| `app/kernel/bus.py` | 进程内事件总线，可桥接 session 事件 |
| `app/kernel/loop.py` | agent 主循环、system prompt 加载、动作 JSON 解析 |
| `app/kernel/http.py` | `/api/plugins`、`/api/plugins/{name}/reload`、`/api/plugin/{name}/{op}` |
| `app/kernel/sse.py` | `/api/events` SSE：backlog、实时事件、状态快照、心跳 |
| `app/kernel/context.py` | `PluginContext`：插件能看到的唯一 API |
| `app/storage.py` | SQLite 持久化与 `session.json` 一次性迁移 |
| `app/session.py` | 内存工作窗口 + 事件订阅/条件变量 + `Storage` 写入 |

## 插件模型

每个插件一个目录：

```
plugins/<name>/
├── plugin.json     # 清单
├── plugin.py       # entry，暴露 setup/teardown/healthcheck
└── README.md       # 该插件的职责、配置、状态
```

`setup(ctx)` 在**独立的 RegistrySet** 里注册能力。内核保存所有插件的 layer，调用时按加载顺序解析（后加载者覆盖同名工具）。

详细契约见 `CONTRACTS.md`。

## 热替换流程

```
文件变化 / POST /api/plugins/{name}/reload
        │
        ▼
  discover() 读 plugin.json
        │
        ▼
  用内容 hash 生成新模块名，exec_module
        │
        ▼
  setup(新 RegistrySet)  ← 失败则丢弃，旧 layer 不动
        │
        ▼
  健康检查（可选 healthcheck）
        │
        ▼
  加锁，原子替换 self._plugins[name]
        │
        ▼
  teardown(旧 ctx)
```

关键点：

- **不使用 `importlib.reload`**：用唯一模块名，避免半更新和残留引用。
- **两阶段**：新版本完全构建成功后才切换；失败保留旧版本。
- **按 step 快照**：agent 每一步开始时取一次分辨率，执行中途换插件不影响当前步。
- **HTTP 路由不动态增删**：所有插件 op 走固定入口 `/api/plugin/{name}/{op}`。

## 数据流（当前）

```
POST /api/chat
  → classifier（规则，Phase 2 可插件化）
  → 后台线程 agent_loop
      → planner（Phase 2 迁插件）
      → session.build_messages（Phase 3 迁插件）
      → deepseek.chat
      → kernel.loop.parse_action
      → runtime.execute_tool → tools_basic 插件
      → session 记录 → SQLite（messages/events）+ 通知 SSE 订阅者
```

## 事件流（SSE）

```
浏览器 EventSource
  → GET /api/events?token=...&since=<last_event_id>
  → 先回放 since 之后的 backlog，再发 state 快照
  → session.add_event() 通过 loop.call_soon_threadsafe 推给每个订阅队列
  → 每条 log 事件后跟一条 state（status/history/last_event_id）
  → 15s 空闲发注释心跳；断线由 EventSource 自动重连（Last-Event-ID）
```

## 持久化

`state/agent.db`（SQLite，WAL）包含：

| 表 | 内容 |
|---|---|
| `sessions` | `conv_id` / status / task / task_state / 时间戳 |
| `messages` | 每个会话的完整消息（内存只保留 `AGENT_WINDOW_SIZE` 条） |
| `events` | 每个会话的事件（内存只保留最近 500 条） |
| `plans` | 计划快照，Phase 2 使用 |
| `meta` | schema 版本与一次性迁移标记 |

`state/session.json` 不再写入；启动时自动迁移一次（`meta.json_migrated` 防重复），
也可手动执行 `python -m app.migrate`。迁移不删除原文件，便于回滚核对。

## 依赖规则

- `app/kernel/*` 不得 import `plugins/*`。
- `plugins/*` 不得 import `app/kernel` 的具体实现（只能 `app.kernel.api`）。
- `plugins/*` 之间不得互相 import；需要协作时通过 `ctx.hooks` / `ctx.bus`。
- `app/main.py` 只做装配，不写业务逻辑。

## 明确的非目标

- 不承诺热补丁内核；内核变更用 `systemctl restart` 或后续蓝绿方案。
- 当前不做多租户；单用户 token 模型不变。
- 当前不做容器/微虚拟机沙箱；Phase 4 再议。
