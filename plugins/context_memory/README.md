# context_memory

把 `state/memory/<conv_id>.md` 的提炼记忆作为独立 section 注入，优先级 600。

注入上限由内核 `AGENT_CONTEXT_MEMORY_MAX_CHARS` 控制，默认 6000 字符。
