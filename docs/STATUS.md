# STATUS

> 唯一现状真源。每次会话结束必须更新。目标 ≤ 60 行。

## 当前阶段

Phase 0 / 1 / 2 / 3 + P0 稳定性完成：稳定内核、插件热插拔、SSE + SQLite、规划状态机、上下文预算与 artifact。
下一阶段：Phase 4 主机治理（另议）。当前服务端已按用户要求停止。

## 上次完成（2026-10-21）

- 建立 `AGENTS.md` + `docs/` 项目记忆；拆分 `app/kernel/`
- 插件加载器：`plugin.json`、原子热替换、失败保留旧版
- SQLite `state/agent.db` + `session.json` 一次性迁移；SSE `GET /api/events`
- Phase 2：`app/planning.py` + `plugins/planning` + `plugins/classifier`
- Phase 3：`app/context.py`：Section 模型、token 预算、优先级、截断/丢弃、ledger
- Phase 3：`plugins/context_base/plan/memory/window` 四个 section 插件
- Phase 3：大工具结果 artifact 卸载到 `state/artifacts/`，上下文保留摘要+路径
- Phase 3：达到压力阈值触发 `context.compress` hook；无 hook 时超预算确定性压缩
- `/api/context` 返回 messages、parts 和预算 ledger
- 配置：`AGENT_CONTEXT_BUDGET` / `AGENT_CONTEXT_PRESSURE` / `AGENT_ARTIFACT_*`
- P0：classifier 识别概念/解释类问题为 chat，并防止插件误判为 task；显式写/查看/修复仍为 task
- P0：planning prompt 禁止为用户未要求的交付物发明验收；loop 对缺失/未知 action 给定向修复提示

## 已知问题

- token 估算是启发式（ASCII/4、非 ASCII/1），不是准确 tokenizer
- `context.compress` 只有扩展点，尚未内置 LLM 压缩后端
- artifact 目录尚无自动清理/保留上限
- `depends_on` 仅展示，尚未参与计划调度
- 意图分类仍是规则 + 插件 hook，不是语义模型；复杂混合请求可能误判
- 旧会话中已 failed 的计划不会自动清除；需要时调用 planning reset
- SSE token 走查询参数，可能进入访问日志（单用户环境可接受，ADR-009）
- `state/session.json` 迁移后保留，确认稳定后可手动归档

## 下一步（3）

1. Phase 4：类型化特权操作 + root helper + 审批/审计
2. Phase 4：快照/回滚与沙箱策略
3. Phase 3 收尾：LLM 压缩后端 + artifact 清理

## 插件状态

| 插件 | 版本 | 状态 | 能力 |
|---|---|---|---|
| `tools_basic` | 0.1.0 | active | shell / 文件工具 / self_restart / 符号工具 |
| `planning` | 0.1.0 | active | 计划 create/revise；`status` / `reset` |
| `classifier` | 0.1.0 | active | `classify.prompt` 四模式分类 |
| `context_base` | 0.1.0 | active | system / task sections |
| `context_plan` | 0.1.0 | active | plan section |
| `context_memory` | 0.1.0 | active | memory section |
| `context_window` | 0.1.0 | active | history sections |
| `hello` | 0.1.0 | experimental | 热加载演示 |

## 最近验证

- 2026-10-21：`make check` **52 passed**；`compileall` 通过。
- 上下文单测：预算保留/丢弃、压力触发 `context.compress`、fallback、artifact 卸载、真实 context 插件。
- 临时根目录 E2E：8 插件加载；`/api/context` 返回 sections/ledger，预算 500 时 system 超预算后正确丢弃可选段。
- 线上服务已验证 Phase 3 上下文；当前由用户停止，P0 改动需重启后做一次线上回归。
