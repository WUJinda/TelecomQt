"""来财协作面板 · 后端入口。

本地运行：
    cd backend && uvicorn app.main:app --reload --port 8000
浏览器打开 http://localhost:8000 即可看到前端（需先建好 ../frontend/index.html）。
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .routers import ideas, plans, reports, symbols

# 前端目录：默认源码树 TelecomQt/frontend；Docker 里用环境变量覆盖
_FRONTEND_DEFAULT = Path(__file__).resolve().parents[2] / "frontend"
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", _FRONTEND_DEFAULT))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时建表：首次运行创建 app.db，之后幂等。
    init_db()
    yield


app = FastAPI(title="来财协作面板 API", version="0.1.0", lifespan=lifespan)

# 前端与 API 同源（都由本服务托管），无需 CORS。
# 若将来拆分前端到独立端口/域名，再按需引入 CORSMiddleware 并显式配置白名单。

# API
app.include_router(reports.router, prefix="/api", tags=["reports"])
app.include_router(ideas.router, prefix="/api", tags=["ideas"])
app.include_router(plans.router, prefix="/api", tags=["plans"])
app.include_router(symbols.router, prefix="/api", tags=["symbols"])


# 静态资源（本地 vendored 的 echarts/alpine；没有则前端走 CDN）
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
