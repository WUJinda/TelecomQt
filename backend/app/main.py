"""来财协作面板 · 后端入口。

本地运行：
    cd backend && uvicorn app.main:app --reload --port 8000
浏览器打开 http://localhost:8000 即可看到前端（需先建好 ../frontend/index.html）。
"""
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .routers import reports

# 前端目录：默认源码树 TelecomQt/frontend；Docker 里用环境变量覆盖
_FRONTEND_DEFAULT = Path(__file__).resolve().parents[2] / "frontend"
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", _FRONTEND_DEFAULT))

app = FastAPI(title="来财协作面板 API", version="0.1.0")

# 本地开发时前端可能单独跑在别的端口，放开 CORS；上线后由反代/Cloudflare 收口
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API
app.include_router(reports.router, prefix="/api", tags=["reports"])


# 静态资源（本地 vendored 的 echarts/alpine/pico；没有则前端走 CDN）
_assets = FRONTEND_DIR / "assets"
if _assets.exists():
    app.mount("/assets", StaticFiles(directory=_assets), name="assets")


@app.get("/")
def index():
    """返回前端入口页。"""
    target = FRONTEND_DIR / "index.html"
    if target.exists():
        return FileResponse(target)
    return {"msg": "前端未找到", "FRONTEND_DIR": str(FRONTEND_DIR),
            "hint": "先按 docs/设计方案.md 建好 frontend/index.html"}


@app.get("/api/health")
def health():
    return {"ok": True, "version": "0.1.0"}
