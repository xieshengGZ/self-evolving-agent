# AGENTS.md — 项目唯一入口

> 任何新会话（人或 AI）先读这一页，再读 `docs/STATUS.md`。
> 不要依赖聊天记录；跨会话的结论必须落到文件里。

## 这是什么

`agent-services` 是运行在 Ubuntu 上的单会话 AI Agent 守护进程：

- Web 页面输入任务 → DeepSeek 返回 JSON 动作 → 本地执行 → 循环直到完成
- systemd 管理，监听 `0.0.0.0:8088`
- 支持自更新/重启，但**新功能应通过插件热加载，不重启进程**

## 现在在哪

- 当前版本：**v1.2.0（stable）**；下一开发线：**v2.1.0**
- 当前阶段：**Phase 0 + 1 + 2 + 3 + P0 完成**（稳定内核 + 热插拔 + SSE/SQLite + 规划 + 上下文预算/artifact）
- 现状唯一真源：`docs/STATUS.md`
- 版本规则：`docs/VERSIONING.md`
- 路线图：`docs/ROADMAP.md`
- 架构：`docs/ARCHITECTURE.md`
- 插件契约：`docs/CONTRACTS.md`
- 决策记录：`docs/DECISIONS.md`

## 快速命令

```bash
.venv/bin/python -m pytest -q          # 测试
.venv/bin/python -m compileall -q app plugins
.venv/bin/python -m app.cli status     # 项目/服务/插件/下一步
.venv/bin/python -m app.cli map        # 插件与工具地图
.venv/bin/python -m app.cli reload hello
.venv/bin/python -m app.migrate        # session.json -> SQLite（一次性）
make check                             # test + compileall
```

生产：

```bash
sudo systemctl status agent-services
sudo systemctl restart agent-services
```

## 架构地图

```
app/main.py            # bootstrap：路由装配，尽量不再写业务
app/kernel/            # 稳定内核，不 import 任何插件
  registry.py          # tools / hooks / http / planners / context 注册表
  plugins.py           # 发现、加载、卸载、原子热替换
  runtime.py           # 单例运行时：store + bus + plugins
  store.py             # 插件可见的存储 API（session 热数据 + SQLite 查询）
  loop.py              # agent 主循环
  http.py              # /api/plugins* 与 /api/plugin/{name}/{op}
  sse.py               # /api/events 事件流
app/storage.py         # SQLite：sessions/messages/events/plans
app/session.py         # 内存热状态 + SQLite 持久化
app/planning.py        # Plan/PlanStep、状态机、确定性验收、计划持久化
app/context.py         # Section、token 预算、压缩/丢弃、artifact 卸载、ledger
state/agent.db         # 运行时数据库（不入库）
plugins/
  tools_basic/         # shell / read / write / self_restart / 符号工具
  planning/            # LLM 任务规划器（ctx.planners.default）
  classifier/          # chat/task/continuation/control 分类（hook）
  context_base/        # system/task sections
  context_plan/        # plan_text section
  context_memory/      # 长期记忆 section
  context_window/      # 工作窗口历史 messages
  hello/               # 热加载演示插件
docs/                  # 项目记忆
tests/                 # 可执行的记忆
```

依赖方向只有一条：`main → kernel → plugins`。
`app/planning.py` 是内核与 planning 插件共享的稳定领域层；`app/context.py` 是上下文预算/渲染的稳定层。
**插件之间、插件与 main.py 之间禁止互相 import**，只能通过 `ctx` 交互。

## 铁律

1. 插件只 import `app.kernel.api`，不 import `app.main` 或其他插件。
2. 修改插件契约必须提升 `api_version`，并同步 `docs/CONTRACTS.md`。
3. 任何跨会话的决策必须写入 `docs/DECISIONS.md` 或 `docs/STATUS.md`。
4. 不提交密钥；`.env`、`state/`、`backups/` 不入库。
5. 新增/修改插件必须带 `plugin.json` 与该插件的 `README.md`。
6. 内核代码变更仍需重启；不要试图热补丁内核。
7. 计划必须可验收；能确定性检查的，不要只写自然语言。

## 上手顺序

1. `AGENTS.md`（本页）
2. `docs/STATUS.md`
3. `docs/ARCHITECTURE.md`
4. 与任务相关的 `plugins/<name>/README.md`
5. `docs/CONTRACTS.md`（写插件时）

## 完成定义（DoD）

- [ ] `make check` 通过
- [ ] 新增/修改的插件能被热加载，失败时旧版本仍在
- [ ] `docs/STATUS.md` 已更新（当前阶段 / 已知问题 / 下一步）
- [ ] `docs/JOURNAL.md` 追加本次会话记录
- [ ] 如有架构选择，补一条 ADR 到 `docs/DECISIONS.md`
