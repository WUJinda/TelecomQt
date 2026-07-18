"""SQLite 连接与会话管理。

数据量极小（两人协作），SQLite 单文件足够；用同步 engine + session，
与现有 reports 路由风格一致，不引入异步复杂度。

db 路径默认 ``backend/data/app.db``，Docker 里通过 ``DATABASE_PATH``
环境变量指向挂载卷内的 ``/data/app.db``（与 experiments/ 同级，复用现有 VOLUME）。
"""
import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

_DB_DEFAULT = Path(__file__).resolve().parents[1] / "data" / "app.db"
DB_PATH = Path(os.environ.get("DATABASE_PATH", _DB_DEFAULT))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# FastAPI 用线程池跑同步路由，SQLite 连接需跨线程共享。
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    """启动时建表（幂等）。两人小项目不上 alembic 迁移。"""
    # 必须先 import models，让 SQLModel.metadata 收集到表定义。
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI 依赖：注入一个 Session，请求结束自动关闭。"""
    with Session(engine) as session:
        yield session
