# context_base

最基础的上下文 section：系统提示词与当前任务。

| 顺序 | section | 优先级 | 说明 |
|---|---|---|---|
| 10 | `system` | 1000 | `prompts/system.md`，必需 |
| 20 | `task` | 900 | 当前任务，必需 |

内核负责 token 预算、截断、最终 message 顺序。
