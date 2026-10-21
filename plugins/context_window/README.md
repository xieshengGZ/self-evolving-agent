# context_window

把 `session` 工作窗口中的每条消息变成一个 section，优先级随新近程度增加
（`500 + index`）。token 预算不足时，内核会优先保留较新的消息并丢弃较旧消息。

历史原文仍在 SQLite `messages` 表；本插件只读内存窗口。
