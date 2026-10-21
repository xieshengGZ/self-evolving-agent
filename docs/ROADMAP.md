# Roadmap

> 勾选状态以 `STATUS.md` 为准；本文件只描述阶段目标。

## Phase 0 — 基线（已完成）

- [x] `AGENTS.md` + `docs/` 骨架
- [x] 合并重复的 `execute_action`
- [x] 修复 `config.CMD_TIMEOUT` → `AGENT_CMD_TIMEOUT`
- [x] pytest 冒烟测试
- [x] 记录当前架构与决策

## Phase 1 — 内核 + 热插拔（已完成）

- [x] `app/kernel/` 模块拆分
- [x] 插件契约 + `plugin.json` + 加载器
- [x] 原子热替换（失败保留旧版）
- [x] `/api/plugins`、`/api/plugins/{name}/reload`、`/api/plugin/{name}/{op}`
- [x] `tools_basic` 迁移为插件
- [x] `hello` 演示插件
- [x] agent loop 抽离到 `app/kernel/loop.py`
- [x] 事件流 SSE 替代前端轮询
- [x] `store` 迁移 SQLite（sessions/messages/events/plans）
- [x] 旧的 `state/session.json` 一次性迁移脚本（`app.migrate`）
- [x] 插件自动监听（`AGENT_PLUGIN_AUTORELOAD` 已留开关）

## Phase 2 — 任务规划插件

- [ ] `Plan` / `PlanStep` 模型（goal / acceptance / deps / status / evidence）
- [ ] `planning` 状态机：planning → executing → verifying → done/replan
- [ ] 持久化与重启恢复
- [ ] 重规划触发器（连续失败 / 验收失败 / 用户改目标）
- [ ] 确定性验证器（exit code、文件存在、测试通过）
- [ ] `classifier` 插件化（chat / task / continuation / control）
- [ ] 同会话新任务强制 `reset_plan`

## Phase 3 — 上下文管理插件

- [ ] `Section` 模型 + token 预算 + 分层优先级
- [ ] `context_base` / `context_plan` / `context_memory` / `context_window`
- [ ] 工具结果 artifact 卸载（原文落盘，上下文只放摘要 + 引用）
- [ ] token 账本 + `/api/context` 展示每段预算
- [ ] 压力触发压缩（60~70%）与可替换压缩后端

## Phase 4 — 主机治理（另议）

- [ ] 类型化特权操作（install_package / write_system_file / manage_service）
- [ ] root helper（Go/Rust）+ 策略 + 审批 + 审计
- [ ] 快照/回滚（btrfs/ZFS/LVM）
- [ ] 沙箱（bwrap / systemd-run）与危险命令策略
