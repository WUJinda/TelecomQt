"""策略想法 CRUD。

风格与 reports.py 一致：APIRouter + HTTPException，路由与数据层分离。
数据层换成 SQLModel session（依赖注入）。
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..database import get_session
from ..models import IDEA_STATUSES, Idea, IdeaCreate, IdeaUpdate

router = APIRouter()


@router.get("/ideas")
def list_(
    status: Optional[str] = Query(default=None, description="按状态过滤"),
    session: Session = Depends(get_session),
):
    """所有想法，按创建时间倒序；可按 status 过滤。"""
    stmt = select(Idea)
    if status:
        stmt = stmt.where(Idea.status == status)
    stmt = stmt.order_by(Idea.created_at.desc())
    return session.exec(stmt).all()


@router.post("/ideas")
def create(payload: IdeaCreate, session: Session = Depends(get_session)):
    idea = Idea(**payload.model_dump())
    session.add(idea)
    session.commit()
    session.refresh(idea)
    return idea


@router.patch("/ideas/{idea_id}")
def update(idea_id: int, payload: IdeaUpdate, session: Session = Depends(get_session)):
    """部分更新（状态流转、改内容）。仅更新实际传入的字段。"""
    idea = session.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="想法不存在")
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in IDEA_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法状态，可选：{list(IDEA_STATUSES)}")
    for k, v in data.items():
        setattr(idea, k, v)
    idea.updated_at = datetime.now()
    session.add(idea)
    session.commit()
    session.refresh(idea)
    return idea


@router.delete("/ideas/{idea_id}")
def delete(idea_id: int, session: Session = Depends(get_session)):
    idea = session.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="想法不存在")
    session.delete(idea)
    session.commit()
    return {"ok": True}
