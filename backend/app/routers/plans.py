"""交易计划 CRUD。

看板式管理：计划中 → 已执行 / 已放弃。
执行后通过 PATCH 回填 actual_entry / actual_pnl。
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..database import get_session
from ..models import PLAN_STATUSES, Plan, PlanCreate, PlanUpdate

router = APIRouter()


@router.get("/plans")
def list_(
    status: Optional[str] = Query(default=None, description="按状态过滤"),
    session: Session = Depends(get_session),
):
    """所有计划，按创建时间倒序；可按 status 过滤。"""
    stmt = select(Plan)
    if status:
        stmt = stmt.where(Plan.status == status)
    stmt = stmt.order_by(Plan.created_at.desc())
    return session.exec(stmt).all()


@router.post("/plans")
def create(payload: PlanCreate, session: Session = Depends(get_session)):
    plan = Plan(**payload.model_dump())
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


@router.patch("/plans/{plan_id}")
def update(plan_id: int, payload: PlanUpdate, session: Session = Depends(get_session)):
    """部分更新（状态流转、执行回填 actual_*）。仅更新实际传入的字段。"""
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in PLAN_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法状态，可选：{list(PLAN_STATUSES)}")
    for k, v in data.items():
        setattr(plan, k, v)
    plan.updated_at = datetime.now()
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


@router.delete("/plans/{plan_id}")
def delete(plan_id: int, session: Session = Depends(get_session)):
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    session.delete(plan)
    session.commit()
    return {"ok": True}
