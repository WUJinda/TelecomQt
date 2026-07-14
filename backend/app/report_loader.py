"""扫描 experiments 目录，读取 experiment.json。

experiment.json 字段约定见 docs/experiment-schema.md。
数据目录可通过环境变量 EXPERIMENTS_DIR 覆盖（Docker 挂卷用）。
"""
import json
import os
from pathlib import Path


def _data_dir() -> Path:
    return Path(os.environ.get(
        "EXPERIMENTS_DIR",
        Path(__file__).resolve().parents[1] / "data" / "experiments",
    ))


def list_experiments() -> list[dict]:
    """返回所有实验的摘要（按生成时间倒序）。"""
    items: list[dict] = []
    root = _data_dir()
    if not root.exists():
        return items

    for exp_dir in sorted(root.iterdir()):
        if not exp_dir.is_dir():
            continue
        f = exp_dir / "experiment.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        s = data.get("summary") or {}
        items.append({
            "experiment_id": data.get("experiment_id") or exp_dir.name,
            "generated_at": data.get("generated_at", ""),
            "strategy_name": data.get("strategy_name", ""),
            "direction": data.get("direction", ""),
            "mode": data.get("mode", ""),
            "total_pnl": s.get("total_pnl"),
            "win_rate": s.get("win_rate"),
            "total_trades": s.get("total_trades"),
            "instruments_with_trades": s.get("instruments_with_trades"),
            "max_drawdown": s.get("max_drawdown"),
        })

    items.sort(key=lambda x: x.get("generated_at") or "", reverse=True)
    return items


def get_experiment(exp_id: str) -> dict | None:
    """读取单个实验的完整数据；不存在返回 None。"""
    f = _data_dir() / exp_id / "experiment.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
