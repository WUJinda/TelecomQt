"""报告相关 API。"""
from fastapi import APIRouter, HTTPException

from ..report_loader import get_experiment, list_experiments

router = APIRouter()


@router.get("/reports")
def list_():
    """所有实验列表（摘要）。"""
    return list_experiments()


@router.get("/reports/{exp_id}")
def detail(exp_id: str):
    """单个实验完整数据（含逐笔交易明细）。"""
    data = get_experiment(exp_id)
    if data is None:
        raise HTTPException(status_code=404, detail="实验不存在")
    return data
