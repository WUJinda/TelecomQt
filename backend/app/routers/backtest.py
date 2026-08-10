"""回测中心 API 路由。

端点总览：
  GET    /api/backtest/strategy-params         — 策略参数定义
  GET    /api/backtest/datasets                — 可回测品种列表

  GET    /api/backtest/sessions                — 会话列表（含 record_count）
  POST   /api/backtest/sessions                — 创建会话
  PATCH  /api/backtest/sessions/{id}           — 更新会话
  DELETE /api/backtest/sessions/{id}           — 删除会话（级联删除记录和文件）

  GET    /api/backtest/sessions/{id}/records   — 会话下回测记录列表
  GET    /api/backtest/records/{id}            — 单条记录详情（含 experiment.json）
  DELETE /api/backtest/records/{id}            — 删除记录

  POST   /api/backtest/run                     — 执行回测
"""
from __future__ import annotations

import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..database import get_session
from ..models import (
    BacktestRecord,
    BacktestSession,
    BacktestSessionCreate,
    BacktestSessionUpdate,
)
from ..strategy_params import STRATEGY_PARAM_GROUPS, get_default_params, validate_params
from ..backtest_engine import BacktestEngine, _slugify, SESSIONS_DIR

router = APIRouter()

# 全局引擎实例（无状态，可安全共享）
_engine: BacktestEngine | None = None


def _get_engine() -> BacktestEngine:
    global _engine
    if _engine is None:
        _engine = BacktestEngine()
    return _engine


# ---- Pydantic 输入 ----

class RunRequest(BaseModel):
    """执行回测请求体。"""
    session_id: int
    label: str = ""
    instruments: list[str]
    params: dict = {}


class ImportRequest(BaseModel):
    """将外部实验归入会话的请求体。"""
    experiment_id: str                          # 扁平目录的 experiment_id


# ============================================================
# 策略参数 & 数据集
# ============================================================

@router.get("/strategy-params")
def get_strategy_params():
    """返回策略参数定义，前端据此渲染参数表单。"""
    return {"groups": STRATEGY_PARAM_GROUPS, "defaults": get_default_params()}


@router.get("/datasets")
def list_datasets():
    """扫描 D1 目录，返回可回测品种列表。"""
    return _get_engine().list_datasets()


# ============================================================
# 会话 CRUD
# ============================================================

@router.get("/sessions")
def list_sessions(session: Session = Depends(get_session)):
    """列出所有回测会话，每项含 record_count。"""
    sessions = session.exec(
        select(BacktestSession).order_by(BacktestSession.created_at.desc())
    ).all()
    result = []
    for s in sessions:
        count = session.exec(
            select(BacktestRecord).where(BacktestRecord.session_id == s.id)
        ).all()
        result.append({
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "strategy_type": s.strategy_type,
            "status": s.status,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "record_count": len(count),
        })
    return result


@router.post("/sessions")
def create_session(payload: BacktestSessionCreate, session: Session = Depends(get_session)):
    """创建回测会话。"""
    s = BacktestSession(
        name=payload.name,
        description=payload.description,
        strategy_type=payload.strategy_type,
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


@router.patch("/sessions/{session_id}")
def update_session(session_id: int, payload: BacktestSessionUpdate,
                   session: Session = Depends(get_session)):
    """更新会话（名称、描述、状态）。"""
    s = session.get(BacktestSession, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    update_data = payload.model_dump(exclude_unset=True)
    for key, val in update_data.items():
        setattr(s, key, val)
    s.updated_at = __import__("datetime").datetime.now()
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, session: Session = Depends(get_session)):
    """删除会话，同时级联删除其下所有回测记录和 JSON 文件。"""
    s = session.get(BacktestSession, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")

    session_slug = _slugify(s.name)

    # 删除关联记录
    records = session.exec(
        select(BacktestRecord).where(BacktestRecord.session_id == session_id)
    ).all()
    for r in records:
        # 删除 experiment 文件（仅面板内回测；external 只删索引不动原文件）
        if r.experiment_id and r.source != "external":
            _get_engine().delete_experiment(session_slug, r.experiment_id)
        session.delete(r)

    # 尝试删除会话目录（可能已空）
    session_dir = SESSIONS_DIR / session_slug
    if session_dir.exists():
        import shutil
        shutil.rmtree(session_dir, ignore_errors=True)

    session.delete(s)
    session.commit()
    return {"ok": True, "deleted_records": len(records)}


# ============================================================
# 外部实验归入会话
# ============================================================

@router.post("/sessions/{session_id}/import")
def import_external_experiment(session_id: int, payload: ImportRequest,
                               session: Session = Depends(get_session)):
    """将外部推送的实验（扁平目录）归入指定会话。

    不移动文件，只在 BacktestRecord 表建立索引关联。
    """
    s = session.get(BacktestSession, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")

    exp_id = payload.experiment_id
    # 安全校验：experiment_id 只允许字母/数字/下划线/连字符
    if not exp_id or not re.fullmatch(r'[A-Za-z0-9_\-]+', exp_id):
        raise HTTPException(status_code=400, detail="无效的 experiment_id")

    # 读取扁平目录下的 experiment.json
    from ..report_loader import get_experiment as load_flat_experiment
    exp_data = load_flat_experiment(exp_id)
    if exp_data is None:
        raise HTTPException(status_code=404, detail=f"外部实验 {exp_id} 不存在")

    # 检查是否已归入过
    existing = session.exec(
        select(BacktestRecord).where(
            BacktestRecord.session_id == session_id,
            BacktestRecord.experiment_id == exp_id,
            BacktestRecord.source == "external",
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="该实验已归入此会话")

    summary = exp_data.get("summary", {})
    params = exp_data.get("params", {})

    record = BacktestRecord(
        session_id=session_id,
        experiment_id=exp_id,
        experiment_path=exp_id,           # 扁平目录，直接用 experiment_id
        label=exp_data.get("mode") or "",
        params_json=json.dumps(params, ensure_ascii=False),
        total_pnl=summary.get("total_pnl"),
        win_rate=summary.get("win_rate"),
        total_trades=summary.get("total_trades"),
        max_drawdown=summary.get("max_drawdown"),
        total_return_rate=summary.get("total_return_rate"),
        source="external",
        status="completed",
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return {"ok": True, "record_id": record.id, "experiment_id": exp_id}


# ============================================================
# 回测记录
# ============================================================

@router.get("/sessions/{session_id}/records")
def list_records(session_id: int, session: Session = Depends(get_session)):
    """列出会话下所有回测记录。"""
    s = session.get(BacktestSession, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    records = session.exec(
        select(BacktestRecord)
        .where(BacktestRecord.session_id == session_id)
        .order_by(BacktestRecord.created_at.desc())
    ).all()
    return records


@router.get("/records/{record_id}")
def get_record(record_id: int, session: Session = Depends(get_session)):
    """单条记录详情，从 experiment.json 读取完整数据。"""
    r = session.get(BacktestRecord, record_id)
    if not r:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 从数据库行构建元信息
    record_data = {
        "id": r.id,
        "session_id": r.session_id,
        "experiment_id": r.experiment_id,
        "label": r.label,
        "params_json": r.params_json,
        "total_pnl": r.total_pnl,
        "win_rate": r.win_rate,
        "total_trades": r.total_trades,
        "max_drawdown": r.max_drawdown,
        "total_return_rate": r.total_return_rate,
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }

    # 读取完整 experiment.json
    if r.source == "external":
        # 外部归入的实验：从扁平目录读取
        from ..report_loader import get_experiment as load_flat_experiment
        experiment = load_flat_experiment(r.experiment_id)
    else:
        # 面板内回测：从会话目录读取
        bt_session = session.get(BacktestSession, r.session_id)
        if bt_session:
            session_slug = _slugify(bt_session.name)
            experiment = _get_engine().read_experiment(session_slug, r.experiment_id)
        else:
            experiment = None

    if experiment:
        record_data["experiment"] = experiment
    else:
        record_data["experiment"] = None
        record_data["warning"] = "experiment.json 文件未找到"

    return record_data


@router.delete("/records/{record_id}")
def delete_record(record_id: int, session: Session = Depends(get_session)):
    """删除单条回测记录和对应的 JSON 文件。"""
    r = session.get(BacktestRecord, record_id)
    if not r:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 删除 experiment 文件（仅面板内回测才删；external 只删索引不动原文件）
    if r.source != "external":
        bt_session = session.get(BacktestSession, r.session_id)
        if bt_session:
            session_slug = _slugify(bt_session.name)
            _get_engine().delete_experiment(session_slug, r.experiment_id)

    session.delete(r)
    session.commit()
    return {"ok": True}


# ============================================================
# 执行回测
# ============================================================

@router.post("/run")
def run_backtest(payload: RunRequest, session: Session = Depends(get_session)):
    """执行回测。

    流程：
    1. 校验 session_id 存在
    2. 校验并修正参数
    3. 调用引擎执行回测
    4. 写入 experiment.json 到会话目录
    5. 创建 BacktestRecord 数据库行
    6. 返回完整 experiment 数据 + record_id
    """
    # 1. 校验会话
    s = session.get(BacktestSession, payload.session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")

    if not payload.instruments:
        raise HTTPException(status_code=400, detail="请至少选择一个品种")

    # 2. 校验参数
    params = validate_params(payload.params)

    # 3. 执行回测
    engine = _get_engine()
    session_slug = _slugify(s.name)
    try:
        envelope = engine.run(
            instruments=payload.instruments,
            params=params,
            session_name=s.name,
            label=payload.label,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回测执行失败: {e}")

    # 4. 写入 experiment.json
    try:
        engine.write_experiment(envelope, session_slug)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入实验文件失败: {e}")

    # 5. 创建数据库记录
    summary = envelope.get("summary", {})
    record = BacktestRecord(
        session_id=payload.session_id,
        experiment_id=envelope["experiment_id"],
        experiment_path=f"_sessions/{session_slug}/{envelope['experiment_id']}",
        label=payload.label,
        params_json=json.dumps(params, ensure_ascii=False),
        total_pnl=summary.get("total_pnl"),
        win_rate=summary.get("win_rate"),
        total_trades=summary.get("total_trades"),
        max_drawdown=summary.get("max_drawdown"),
        total_return_rate=summary.get("total_return_rate"),
        status="completed",
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    # 6. 返回结果
    return {
        "record_id": record.id,
        "experiment_id": envelope["experiment_id"],
        "summary": summary,
        "experiment": envelope,
    }
