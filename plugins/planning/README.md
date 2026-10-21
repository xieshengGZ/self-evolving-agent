# planning

把用户任务转成可执行、可验收的计划。

- 注册 `ctx.planners` 的 `default` 规划器，供 `app.kernel.loop` 在任务开始时调用。
- 计划包含目标、步骤、依赖、每步预期和确定性验收项（文件存在、命令退出码、测试通过等）。
- 执行失败或验收失败时，内核调用 `revise` 重规划；最多重规划 `AGENT_MAX_REPLANS` 次。
- HTTP op：`status` 查看当前计划，`reset` 取消当前计划。
