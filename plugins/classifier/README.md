# classifier

提示词分类插件，输出 `chat` / `task` / `continuation` / `control`。

- 通过 `ctx.hooks.on("classify.prompt", ...)` 接入，不新增内核注册表。
- `chat`：问候、自我介绍、致谢等，走一次轻量回复。
- `task`：新任务，内核会重置同会话旧计划并规划。
- `continuation`：继续当前任务，保留计划并恢复执行。
- `control`：停止、状态、重启等控制命令。
- 插件缺失或异常时，`app.classifier` 使用同规则兜底。
