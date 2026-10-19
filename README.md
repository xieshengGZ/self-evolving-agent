# agent-services

一个能操作 Ubuntu 系统、可自我修改并重启的单会话 AI Agent 服务。
用户通过 Web 页面和它对话，它调用 DeepSeek 规划动作，由自身执行并回传结果，循环直到任务完成。

## 架构

- FastAPI + uvicorn，单进程
- 动作协议：`shell` / `write_file` / `read_file` / `self_restart` / `done`
- 提示词外置：`prompts/system.md`，每轮热加载
- 会话状态：`state/session.json`
- 自更新：`self_restart` → 备份 → 语法校验 → `os.execv` 重启，失败自动回滚
- 手动救援：`/manual` 页面，可执行命令、看崩溃日志、手动重启

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

