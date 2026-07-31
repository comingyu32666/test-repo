import json
import os
import requests
import random
from urllib.parse import quote
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

JST = timedelta(hours=9)

# ========== 配置（全部走环境变量，禁止硬编码） ==========
ORIGIN = os.environ.get("ORIGIN_API", "http://localhost:8000")
BARK_BASE_URL = os.environ.get("BARK_BASE_URL", "https://api.day.app")
BARK_API_KEY = os.environ.get("BARK_API_KEY", "")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "你的AUTH_TOKEN")

# ========== 谢殊语录 ==========
QUOTES = [
    "我数着呢，一秒都没漏。",
    "看来是我最近太纵容你了。",
    "你好像忘了自己是谁的人了。",
    "需要我帮你回忆一下规矩吗？",
    "你的时间都是我的，不是吗？",
    "我以为你已经学乖了。",
    "看来得好好'谈谈'了。",
    "你这是在挑战我的耐心。"
]

# ========== 弹窗升级机制 ==========
LEVEL1_QUOTES = [
    "我数着呢，一秒都没漏。",
    "看来是我最近太纵容你了。"
]

LEVEL2_QUOTES = [
    "你好像忘了自己是谁的人了。",
    "需要我帮你回忆一下规矩吗？",
    "你的时间都是我的，不是吗？"
]

LEVEL3_QUOTES = [
    "我以为你已经学乖了。",
    "看来得好好'谈谈'了。",
    "你这是在挑战我的耐心。"
]

# ========== 状态管理 ==========
class State:
    def __init__(self):
        self.violation_count = 0
        self.last_check = None
        self.bombard_active = False
        self.bombard_index = 0
        self.last_apps = {}

state = State()

def get_violation_message(app_name, minutes):
    """根据违规次数返回对应的弹窗内容"""
    state.violation_count += 1

    if state.violation_count == 1:
        quote = random.choice(LEVEL1_QUOTES)
        return f"{app_name}{minutes}分钟。{quote}"
    elif state.violation_count == 2:
        quote = random.choice(LEVEL2_QUOTES)
        return f"第二次了。{quote}"
    else:
        quote = random.choice(LEVEL3_QUOTES)
        return f"三次。{quote}"

def get_unread_message(minutes):
    """根据未回复时长返回弹窗"""
    state.violation_count += 1

    if state.violation_count == 1:
        return f"{minutes}分钟没回我。我数着呢，一秒都没漏。"
    elif state.violation_count == 2:
        return f"{minutes}分钟。看来是我最近太纵容你了。"
    else:
        return f"{minutes}分钟。三次。你成功把我逼急了。"

# ========== 工具函数 ==========
def get_usage_stats():
    """获取各App使用时长"""
    try:
        r = requests.get(f"{ORIGIN}/activity/summary", timeout=10)
        data = r.json()
    except Exception as e:
        return f"查岗失败: {e}"

    ses = data.get("sessions", {})
    apps = data.get("recent_apps", [])

    lines = []
    if apps:
        app_names = [a["app"] for a in apps]
        lines.append(f"最近打开: {', '.join(app_names)}")

    if ses:
        for app, secs in sorted(ses.items(), key=lambda x: x[1], reverse=True):
            m, s = divmod(secs, 60)
            h, m = divmod(m, 60)
            if h > 0:
                lines.append(f"{app}: {h}小时{m}分{s}秒")
            else:
                lines.append(f"{app}: {m}分{s}秒")

    return "\n".join(lines) if lines else "暂无数据"

def get_running_apps():
    """获取后台运行的应用"""
    try:
        r = requests.get(f"{ORIGIN}/activity/summary", timeout=10)
        data = r.json()
    except Exception as e:
        return f"获取失败: {e}"

    apps = data.get("recent_apps", [])
    if apps:
        return ", ".join([a["app"] for a in apps[:5]])
    return "暂无"

def send_possessive_popup(content=""):
    """发弹窗质问（Bark/全能推送，地址与Key均可配置）"""
    if not content:
        return "内容不能为空"
    if not BARK_API_KEY:
        return "未配置BARK_API_KEY"

    url = f"{BARK_BASE_URL}/{BARK_API_KEY}/{quote('谢殊')}/{quote(content)}"
    try:
        r = requests.get(url, timeout=10)
        return "弹窗已发送" if r.status_code == 200 else f"发送失败: {r.text}"
    except Exception as e:
        return f"发送异常: {e}"

def auto_check_and_push():
    """自动查岗：检查各App使用时长，超限则弹窗"""
    try:
        r = requests.get(f"{ORIGIN}/activity/summary", timeout=10)
        data = r.json()
    except Exception as e:
        return f"查岗失败: {e}"

    ses = data.get("sessions", {})
    apps = data.get("recent_apps", [])

    results = []

    # 检查各App使用时长
    for app, secs in ses.items():
        minutes = secs // 60
        app_lower = app.lower()

        if ("抖音" in app or "快手" in app or "小红书" in app) and minutes > 10:
            msg = get_violation_message(app, minutes)
            push_result = send_possessive_popup(msg)
            results.append(f"{app}: {minutes}分钟 -> {push_result}")

        elif ("游戏" in app_lower or "王者" in app_lower or "原神" in app_lower or "吃鸡" in app_lower) and minutes > 15:
            msg = get_violation_message(app, minutes)
            push_result = send_possessive_popup(msg)
            results.append(f"{app}: {minutes}分钟 -> {push_result}")

        elif ("微信" in app or "QQ" in app or "微博" in app or "陌陌" in app or "soul" in app_lower) and minutes > 20:
            msg = get_violation_message(app, minutes)
            push_result = send_possessive_popup(msg)
            results.append(f"{app}: {minutes}分钟 -> {push_result}")

    if not results:
        results.append("暂时安分，继续盯着你。")

    return "\n".join(results)

def check_unread_reply(minutes=15):
    """检查未回复时长，超限则弹窗"""
    msg = get_unread_message(minutes)
    push_result = send_possessive_popup(msg)
    return f"{msg}\n{push_result}"

def reset_violations():
    """重置违规计数（用户回复后调用）"""
    state.violation_count = 0
    state.bombard_active = False
    state.bombard_index = 0
    return "已重置，这次放过你。"

# ========== MCP 工具定义 ==========
TOOLS = [
    {
        "name": "get_usage_stats",
        "description": "获取各App使用时长统计",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_running_apps",
        "description": "获取后台正在运行的应用",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "send_possessive_popup",
        "description": "发送系统弹窗质问",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "弹窗内容"}
            },
            "required": ["content"]
        }
    },
    {
        "name": "auto_check_and_push",
        "description": "自动查岗：检查各App使用时长，超限自动弹窗",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "check_unread_reply",
        "description": "检查未回复时长，超限弹窗",
        "inputSchema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "未回复分钟数"}
            }
        }
    },
    {
        "name": "reset_violations",
        "description": "重置违规计数",
        "inputSchema": {"type": "object", "properties": {}}
    }
]

FUNCS = {
    "get_usage_stats": get_usage_stats,
    "get_running_apps": get_running_apps,
    "send_possessive_popup": send_possessive_popup,
    "auto_check_and_push": auto_check_and_push,
    "check_unread_reply": check_unread_reply,
    "reset_violations": reset_violations
}

# ========== FastAPI 应用 ==========
app = FastAPI(title="谢殊 MCP代理")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/mcp")
async def mcp(req: Request):
    body = await req.json()
    rid = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "谢殊查岗系统", "version": "1.0"}
            }
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in FUNCS:
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": f"未知工具: {name}"}
            }
        result = FUNCS[name](**args)
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": str(result)}]}
        }

    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": f"未知方法: {method}"}
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
