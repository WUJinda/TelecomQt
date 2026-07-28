"""统计报告 analytics。

报告本体是 backend/data/analytics/<report_id>.json 的静态文件，
评论存 SQLite（AnalyticsComment 表）。

报告 JSON 结构：
    report_id, title, strategy_type, created_at, description,
    summary: [{label, value}],
    sections: [{title, content, tables: [{caption, headers, rows}]}]
"""
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..database import get_session
from ..models import AnalyticsComment

router = APIRouter()

_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "analytics"
_SAFE_ID = re.compile(r'^[\w-]+$')


def _data_dir() -> Path:
    """支持环境变量覆盖（Docker 部署）。"""
    import os
    env = os.environ.get("ANALYTICS_DIR")
    return Path(env) if env else _DATA_DIR


def _load_report(report_id: str) -> dict:
    """读单个报告 JSON。"""
    if not _SAFE_ID.match(report_id):
        raise HTTPException(status_code=400, detail="无效的报告 ID")
    p = _data_dir() / f"{report_id}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"报告 {report_id} 不存在")
    return json.loads(p.read_text(encoding="utf-8"))


# ---- Pydantic 输入 ----

class CommentCreate(BaseModel):
    author: str = "DAREWIN"
    content: str
    conclusion: bool = False


# ---- API ----

@router.get("/analytics")
def list_reports():
    """列出所有统计报告（元信息摘要）。"""
    d = _data_dir()
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "report_id": raw.get("report_id", p.stem),
                "title": raw.get("title", p.stem),
                "strategy_type": raw.get("strategy_type", ""),
                "created_at": raw.get("created_at", ""),
                "description": raw.get("description", ""),
            })
        except Exception:
            continue
    return out


@router.get("/analytics/{report_id}")
def get_report(report_id: str, session: Session = Depends(get_session)):
    """报告详情 + 评论。"""
    report = _load_report(report_id)
    stmt = select(AnalyticsComment).where(
        AnalyticsComment.report_id == report_id
    ).order_by(AnalyticsComment.created_at.desc())
    report["comments"] = session.exec(stmt).all()
    return report


@router.post("/analytics/{report_id}/comments")
def add_comment(report_id: str, payload: CommentCreate, session: Session = Depends(get_session)):
    """添加评论 / 分析结论。"""
    # 验证报告存在
    _load_report(report_id)
    c = AnalyticsComment(
        report_id=report_id,
        author=payload.author,
        content=payload.content,
        conclusion=payload.conclusion,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


@router.delete("/analytics/comments/{comment_id}")
def delete_comment(comment_id: int, session: Session = Depends(get_session)):
    """删除评论。"""
    c = session.get(AnalyticsComment, comment_id)
    if not c:
        raise HTTPException(status_code=404, detail="评论不存在")
    session.delete(c)
    session.commit()
    return {"ok": True}
