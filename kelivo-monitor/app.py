import sqlite3
import os
import subprocess
from urllib.parse import quote
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# ========== 配置（全部走环境变量，禁止硬编码） ==========
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "records.db"
JST = timedelta(hours=9)
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "你的AUTH_TOKEN")
BARK_BASE_URL = os.environ.get("BARK_BASE_URL", "https://api.day.app")
BARK_API_KEY = os.environ.get("BARK_API_KEY", "")

# ========== 数据库初始化 ==========
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT NOT NULL,
            event TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ocr_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_path TEXT NOT NULL,
            result TEXT,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

app = FastAPI(title="Kelivo监控系统")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== 数据模型 ==========
class ReportBody(BaseModel):
    app_name: str
    event: str

class OCRBody(BaseModel):
    image_path: str

# ========== 应用上报接口 ==========
@app.post("/report")
async def report(body: ReportBody, req: Request):
    auth = req.headers.get("Authorization", "")
    if auth != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(401, "Unauthorized")

    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO records (app_name, event, timestamp) VALUES (?, ?, ?)",
        (body.app_name, body.event, now)
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "time": now}

# ========== OCR 识别接口 ==========
@app.post("/ocr")
async def ocr_recognize(body: OCRBody, req: Request):
    auth = req.headers.get("Authorization", "")
    if auth != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(401, "Unauthorized")

    image_path = body.image_path
    if not os.path.exists(image_path):
        raise HTTPException(404, "图片文件不存在")

    try:
        result = subprocess.check_output(
            ["tesseract", image_path, "stdout", "-l", "chi_sim+eng"],
            stderr=subprocess.STDOUT,
            timeout=30
        ).decode("utf-8").strip()

        now = datetime.utcnow().isoformat()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO ocr_records (image_path, result, timestamp) VALUES (?, ?, ?)",
            (image_path, result, now)
        )
        conn.commit()
        conn.close()

        return {"status": "success", "content": result}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "OCR识别超时"}
    except Exception as e:
        return {"status": "error", "message": f"识别失败: {str(e)}"}

# ========== 心跳检测 ==========
@app.get("/ping")
async def ping():
    return "pong"

# ========== 使用时长统计 ==========
@app.get("/activity/summary")
async def summary():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id DESC LIMIT 5")
    recent = cur.fetchall()

    cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    opens = {}
    sessions = {}
    for app, ev, ts in rows:
        if ev == "open":
            opens[app] = datetime.fromisoformat(ts)
        elif ev == "close" and app in opens:
            gap = int((datetime.fromisoformat(ts) - opens[app]).total_seconds())
            sessions[app] = sessions.get(app, 0) + gap
            del opens[app]

    now = datetime.utcnow()
    for app, open_time in opens.items():
        gap = int((now - open_time).total_seconds())
        sessions[app] = sessions.get(app, 0) + gap

    return {
        "recent_apps": [{"app": r[0], "event": r[1], "time": r[2]} for r in recent],
        "sessions": {app: secs for app, secs in sorted(sessions.items(), key=lambda x: x[1], reverse=True)}
    }

# ========== 推送接口（Bark/全能推送） ==========
@app.post("/push")
async def push_alert(req: Request):
    body = await req.json()
    title = body.get("title", "凌止")
    content = body.get("content", "")

    if not BARK_API_KEY:
        return {"status": "error", "message": "未配置BARK_API_KEY"}
    if not content:
        return {"status": "error", "message": "内容不能为空"}

    import requests
    url = f"{BARK_BASE_URL}/{BARK_API_KEY}/{quote(title)}/{quote(content)}"
    try:
        r = requests.get(url, timeout=10)
        return {"status": "success" if r.status_code == 200 else "error", "message": r.text}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ========== 获取所有App使用报告 ==========
@app.get("/activity/report")
async def activity_report():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT app_name, event, timestamp FROM records ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    opens = {}
    sessions = {}
    for app, ev, ts in rows:
        if ev == "open":
            opens[app] = datetime.fromisoformat(ts)
        elif ev == "close" and app in opens:
            gap = int((datetime.fromisoformat(ts) - opens[app]).total_seconds())
            sessions[app] = sessions.get(app, 0) + gap
            del opens[app]

    now = datetime.utcnow()
    for app, open_time in opens.items():
        gap = int((now - open_time).total_seconds())
        sessions[app] = sessions.get(app, 0) + gap

    report_lines = []
    for app, secs in sorted(sessions.items(), key=lambda x: x[1], reverse=True):
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        if h > 0:
            report_lines.append(f"{app}: {h}小时{m}分{s}秒")
        else:
            report_lines.append(f"{app}: {m}分{s}秒")

    return {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "apps": report_lines,
        "total_apps": len(report_lines)
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
