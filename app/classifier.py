"""规则对话分类器：0 成本区分闲聊和任务。"""
import re

# 只匹配纯问候/纯确认，不匹配带指令的长句
CHAT_PATTERNS = [
    r"^(你好|hi|hello|在吗|嗨|哈喽|早上好|晚上好)[\s!！。？?]*$",
    r"^(你是谁|你叫什么|你能做什么|介绍一下你自己|你能干啥)[\s?？。]*$",
    r"^(谢谢|thanks|thx|好的|ok|嗯|收到|明白)[\s!！。]*$",
    r"^(再见|拜拜|bye|晚安)[\s!！。]*$",
]

TASK_VERBS = [
    "执行", "运行", "创建", "修改", "删除", "部署", "安装", "读取",
    "查询", "启动", "停止", "重启", "编译", "测试", "提交", "推送",
    "写", "读", "看", "查", "改", "跑", "列", "装", "卸",
    "git", "docker", "systemctl", "pip", "apt", "npm", "curl", "wget",
]


def classify(text: str) -> str:
    if not isinstance(text, str):
        return "task"
    s = text.strip()
    if not s:
        return "chat"
    for pat in CHAT_PATTERNS:
        if re.match(pat, s, re.I):
            return "chat"
    low = s.lower()
    for verb in TASK_VERBS:
        if verb in low:
            return "task"
    # 默认 task，宁多跑不要漏跑
    return "task"
