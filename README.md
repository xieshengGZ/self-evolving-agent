# agent-services

一个能完全操作 Ubuntu 系统、可自我升级完善的 AI Agent 服务。
用户通过 Web 页面和它对话，它调用 DeepSeek 规划动作，由自身执行并回传结果，循环直到任务完成。
<img width="1920" height="924" alt="08ac9414fc739a4e37dbad533874c838" src="https://github.com/user-attachments/assets/1ed7b4c7-77ca-4fe4-a5ac-1f712c1a3d44" />
<img width="1920" height="919" alt="bba496ec1e0342b76837383c53d2d86c" src="https://github.com/user-attachments/assets/497212f7-b230-4adb-86fd-70179d995c7b" />

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

