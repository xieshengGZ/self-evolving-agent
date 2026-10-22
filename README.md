# agent-services

一个能完全操作 Ubuntu 系统、可自我升级完善的 AI Agent 服务。
用户通过 Web 页面和它对话，它调用 DeepSeek 规划动作，由自身执行并回传结果，循环直到任务完成。

当前版本：**v1.2.0**（stable）。版本规则见 [`docs/VERSIONING.md`](docs/VERSIONING.md)；下一开发线为 `v2.1.0`。
<img width="1920" height="924" alt="08ac9414fc739a4e37dbad533874c838" src="https://github.com/user-attachments/assets/1ed7b4c7-77ca-4fe4-a5ac-1f712c1a3d44" />
<img width="1920" height="919" alt="bba496ec1e0342b76837383c53d2d86c" src="https://github.com/user-attachments/assets/497212f7-b230-4adb-86fd-70179d995c7b" />

> 新会话先读 [`AGENTS.md`](AGENTS.md)，再看 [`docs/STATUS.md`](docs/STATUS.md)；不要依赖聊天记录。

## 架构

- FastAPI + uvicorn，单进程；稳定内核在 `app/kernel/`
- 动作协议：`shell` / `write_file` / `read_file` / `self_restart` / `done`
- 插件系统：`plugins/<name>/{plugin.json,plugin.py,README.md}`，新工具/HTTP op 热加载不重启
- 规划：`plugins/planning` 通过 `ctx.planners` 接入，计划状态机/验收/恢复在 `app/planning.py`
- 分类：`plugins/classifier` 通过 `classify.prompt` hook 接入，规则兜底
- 上下文：`plugins/context_*` 提供 section，`app/context.py` 负责 token 预算、截断/丢弃和 ledger
- artifact：大工具结果落盘 `state/artifacts/`，上下文只保留预览与路径
- HTTP：`/api/version`、`/api/plugins`、`/api/plugins/{name}/reload`、`/api/plugin/{name}/{op}`
- 实时事件：`GET /api/events`（SSE），前端已从轮询切换为 EventSource
- 提示词外置：`prompts/system.md`，每轮热加载
- 会话状态：SQLite `state/agent.db`（sessions/messages/events/plans）；旧 `session.json` 自动迁移一次
- 计划：`planning → executing → verifying → done/replan`，支持进程重启恢复、确定性验收、同会话新任务重置
- 上下文预算：`AGENT_CONTEXT_BUDGET`（默认 12000 tokens）+ 优先级；`/api/context` 展示每段预算
- 自更新：`self_restart` → 备份 → 语法校验 → `os.execv` 重启，失败自动回滚
- 手动救援：`/manual` 页面，可执行命令、看崩溃日志、手动重启

## 开发

```bash
.venv/bin/pip install -r requirements-dev.txt
make check                    # compileall + pytest
.venv/bin/python -m app.cli status
.venv/bin/python -m app.cli map
.venv/bin/python -m app.cli reload hello
.venv/bin/python -m app.migrate        # 手动迁移旧 session.json
```

## 快速开始

```bash
# 1. 安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 配置
cp .env.example .env
vim .env  # 填入 DEEPSEEK_API_KEY 和 AGENT_WEB_TOKEN

# 3. 启动
sudo cp agent-services.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agent-services

# 4. 访问
# http://<服务器IP>:8088

