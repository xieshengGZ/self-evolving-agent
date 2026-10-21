# Plugin Contract v1

> 修改本契约必须提升 `api_version`，并同步本文档与 `AGENTS.md`。

## 清单 `plugin.json`

```json
{
  "name": "tools_basic",
  "version": "0.1.0",
  "api_version": 1,
  "entry": "plugin.py",
  "status": "active",
  "description": "Core shell/filesystem tools",
  "config": {}
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 必须与目录名一致，全局唯一 |
| `version` | 是 | 插件版本，建议 semver |
| `api_version` | 是 | 当前只接受 `1` |
| `entry` | 是 | 相对插件目录的 Python 文件，禁止 `..` 越界 |
| `status` | 否 | `active` / `experimental` / `disabled`；`disabled` 不加载 |
| `description` | 否 | 一行说明 |
| `config` | 否 | 传给 `ctx.config` 的字典（当前为普通值，后续可加 schema 校验） |

## 生命周期

```python
# plugins/<name>/plugin.py
from app.kernel.api import PluginContext

def setup(ctx: PluginContext) -> None:
    ...

def healthcheck(ctx: PluginContext) -> bool:   # 可选
    return True

def teardown(ctx: PluginContext) -> None:      # 可选
    ...
```

- `setup` 必须存在，可重复调用（每次加载使用全新的 `RegistrySet`）。
- `healthcheck` 返回 `False` 或抛异常会使本次加载失败，旧版本保留。
- `teardown` 在旧版本被替换或卸载后调用；不得假设进程会退出。

## `ctx` 暴露的 API

| 属性 | 类型 | 用途 |
|---|---|---|
| `ctx.name` / `ctx.version` | `str` | 插件身份 |
| `ctx.config` | `dict` | 来自 `plugin.json` 的 `config` |
| `ctx.log` | `logging.Logger` | `plugin.<name>` 命名空间 |
| `ctx.tools` | `ToolRegistry` | 注册模型可调用的工具 |
| `ctx.hooks` | `HookBus` | 订阅/发布生命周期事件 |
| `ctx.http` | `HttpRegistry` | 注册 `/api/plugin/{name}/{op}` 处理器 |
| `ctx.planners` | `PlannerRegistry` | 注册任务规划器（Phase 2） |
| `ctx.context` | `ContextRegistry` | 注册上下文构建器（Phase 3） |
| `ctx.store` | `Store` | 读写会话/事件/状态，查询 SQLite |
| `ctx.bus` | `EventBus` | 进程内事件 |
| `ctx.llm` | module | `app.deepseek`，惰性导入 |

## 工具

```python
ctx.tools.register(
    name="shell",
    handler=handler,
    description="Run a shell command",
    schema={...},          # 可选，仅用于描述
)

def handler(payload: dict) -> dict:
    # payload 是模型返回的整个动作对象
    return {"result_of": "shell", ...}
```

- 同名工具后加载的插件覆盖先加载的。
- 未注册的动作返回 `{"error": "unknown action: <name>"}`。
- handler 抛出的异常会被内核捕获并转为 `{"error": "..."}`。

## HTTP op

```python
ctx.http.register("ping", handler)

def handler(payload: dict) -> dict | Any:
    return {"ok": True}
```

调用方式：

```
POST /api/plugin/<plugin-name>/<op>
Authorization: Bearer <AGENT_WEB_TOKEN>
Content-Type: application/json

{}
```

- 只支持 POST；payload 必须是 JSON 对象或空。
- 返回 dict 时原样返回；其他类型包装为 `{"ok": true, "result": ...}`。
- 不要注册任意路径；固定入口是为了让热替换不触碰 FastAPI 路由表。

## Store（SQLite）

`Store` 的实时读写仍走 `app.session` 的内存窗口；持久化由 `app.storage.Storage` 落到
`state/agent.db`（WAL）。插件常用的方法：

| 方法 | 说明 |
|---|---|
| `add_event(kind, message, **extra)` | 追加事件，立即写入 SQLite |
| `get_events(since=0)` / `last_event_id()` | 读取内存窗口事件 / 最新事件 id |
| `wait_events(last_id, timeout)` | 阻塞等待新事件（同步调用） |
| `append_history(role, content)` / `get_history()` | 当前窗口消息 |
| `get_status()` / `set_status()` | 运行状态 |
| `get_conv_id()` / `new_conversation(task)` | 会话切换 |
| `get_task()` / `set_task()` / `get_task_state()` / `set_task_state()` | 任务与计划状态 |
| `list_sessions(limit=50)` | 最近会话（SQLite） |
| `save_plan(conv_id, goal, steps, status)` / `get_plans(conv_id)` | 计划持久化（Phase 2 使用） |
| `db_path()` | 当前数据库路径 |

约定：`add_event` / `append_history` 立即持久化；`set_status` / `set_task` /
`set_task_state` 会同步会话元数据；`persist()` 仍可显式刷一次。

## HTTP 事件流（SSE）

```
GET /api/events?token=<AGENT_WEB_TOKEN>&since=<last_event_id>
```

- `Authorization: Bearer <token>` 也可用；`EventSource` 不能设置请求头，所以额外接受 `token` 查询参数。
- 服务端事件：`log`（带 `id`，数据为事件对象）、`state`（status/history/last_event_id/conv_id/task）。
- 浏览器重连时自动带 `Last-Event-ID`，优先于 `since`。
- 连接空闲时每 15 秒发送注释心跳；服务端不会为每个客户端创建轮询定时器。

## 钩子

```python
ctx.hooks.on("after_step", handler)
def handler(**kwargs): ...
```

约定的事件名（内核逐步接入）：

| 事件 | 时机 |
|---|---|
| `kernel.started` | 内核启动完成 |
| `plugin.loaded` / `plugin.unloaded` | 插件生命周期 |
| `before_step` | 每次模型调用前 |
| `after_step` | 工具执行并记录后 |
| `plan.created` / `plan.revised` | Phase 2 |
| `context.built` | Phase 3 |

钩子异常会被记录，不影响主流程。

## 规则

1. 不 import `app.main` 或其他插件。
2. 不在 import 阶段做 I/O 或联网；放到 `setup`。
3. 不修改全局解释器状态；需要共享走 `ctx.bus` / `ctx.store`。
4. 不在插件里读取密钥值；需要时通过 `config` 的引用或环境变量名。
5. 加载必须幂等：同一个插件重复 `setup` 不产生副作用。
