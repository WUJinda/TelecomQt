"""回测引擎封装 — 将 laicai-bridge 的回测能力封装为 Web API 可调用的接口。

职责：
  1. 扫描 market-data/exports/D1/ 返回可用品种列表
  2. 对选中品种执行回测，生成 K 线复盘图和 experiment.json
  3. 将结果写入指定的会话目录
"""
from __future__ import annotations

import json
import os
import sys
import re
from datetime import datetime
from pathlib import Path

import numpy as np

# ---- 路径常量 ----
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAICAI_BRIDGE = PROJECT_ROOT / "laicai-bridge"
DATA_DIR = Path(os.environ.get(
    "MARKET_DATA_DIR",
    PROJECT_ROOT / "market-data",
)) / "exports" / "D1"
EXPERIMENTS_DIR = Path(os.environ.get(
    "EXPERIMENTS_DIR",
    PROJECT_ROOT / "backend" / "data" / "experiments",
))
SESSIONS_DIR = EXPERIMENTS_DIR / "_sessions"


def _ensure_laicai_path():
    """把 laicai-bridge 加入 sys.path（幂等）。"""
    if str(LAICAI_BRIDGE) not in sys.path:
        sys.path.insert(0, str(LAICAI_BRIDGE))


def _json_default(obj):
    """JSON 序列化 fallback：处理 numpy 类型和 pandas Timestamp。"""
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _slugify(name: str) -> str:
    """把会话名转为安全的目录名。"""
    slug = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', name.strip())
    slug = re.sub(r'_+', '_', slug).strip('_')
    return slug or "unnamed"


def _load_symbol_meta():
    """尝试从 market-data/config.py 加载品种中文名映射。"""
    market_data_dir = Path(os.environ.get(
        "MARKET_DATA_DIR",
        PROJECT_ROOT / "market-data",
    ))
    config_path = market_data_dir / "config.py"
    if not config_path.exists():
        return {}
    if str(market_data_dir) not in sys.path:
        sys.path.insert(0, str(market_data_dir))
    try:
        import config  # type: ignore[import-not-found]
        return getattr(config, "_SYMBOL_META", {})
    except Exception:
        return {}


class BacktestEngine:
    """回测引擎，封装 laicai-bridge 的调用。"""

    def __init__(self):
        _ensure_laicai_path()

    # ---- 品种扫描 ----

    def list_datasets(self) -> list[dict]:
        """扫描 D1 目录，返回可用品种列表。

        每项包含 instrument, exchange, name, kline_style, total_records, date_range, filename。
        """
        if not DATA_DIR.exists():
            return []

        symbol_meta = _load_symbol_meta()
        datasets = []

        for p in sorted(DATA_DIR.glob("*_kline.json")):
            try:
                # 只读头部信息，避免加载大文件
                with open(p, "r", encoding="utf-8") as f:
                    raw = json.load(f)

                instrument = raw.get("instrument", p.stem.replace("_kline", ""))
                data = raw.get("data", [])

                # 尝试从 config.py 获取中文名
                prefix = instrument.rstrip("0123456789")
                name = raw.get("name", "")
                if not name:
                    meta_entry = symbol_meta.get(prefix, {})
                    name = meta_entry.get("name", "") if isinstance(meta_entry, dict) else ""

                date_range = ""
                if data:
                    dates = [r.get("date", "") for r in data]
                    if dates:
                        date_range = f"{dates[0]} ~ {dates[-1]}"

                datasets.append({
                    "instrument": instrument,
                    "exchange": raw.get("exchange", "?"),
                    "name": name or instrument,
                    "kline_style": raw.get("kline_style", "D1"),
                    "total_records": raw.get("total_records", len(data)),
                    "date_range": date_range,
                    "filename": p.name,
                })
            except (json.JSONDecodeError, KeyError):
                continue

        return datasets

    # ---- 回测执行 ----

    def run(self, instruments: list[str], params: dict,
            session_name: str = "", label: str = "") -> dict:
        """执行回测，返回完整 experiment dict。

        参数：
            instruments  品种代码列表，如 ["ag0", "rb0", "cu0"]
            params       已校验的策略参数 dict
            session_name 会话名（决定输出目录路径）
            label        本次回测标签（写入 experiment 元信息）

        返回：
            experiment.json 格式的完整 dict，含 experiment_id 和输出路径信息。
        """
        _ensure_laicai_path()
        import double_top_backtest as dtb
        from build_chart import attach_charts

        # ---- 1. 设置资金管理参数（monkey-patch 模块级常量）----
        dtb.MAX_PER_TRADE = params.get("max_per_trade", 1_000_000)
        dtb.MAX_TOTAL_EXPOSURE = params.get("max_total_exposure", 6_000_000)

        # ---- 2. 构建回测引擎参数（合并默认 + 用户参数）----
        engine_params = {**dtb.DEFAULT_PARAMS, **params}

        # ---- 3. 对每个选中品种执行回测 ----
        all_results = []
        instruments_lower = {s.lower(): s for s in instruments}

        for kline_path in sorted(DATA_DIR.glob("*_kline.json")):
            # 从文件头读取 instrument 字段来匹配
            try:
                with open(kline_path, "r", encoding="utf-8") as f:
                    head = json.load(f)
                inst = head.get("instrument", "")
                inst_stem = kline_path.stem.replace("_kline", "")
            except (json.JSONDecodeError, KeyError):
                continue

            # 大小写不敏感匹配品种代码或文件名
            matched = (inst.lower() in instruments_lower
                       or inst_stem.lower() in instruments_lower)
            if not matched:
                continue

            result = dtb.backtest_one(str(kline_path), engine_params)
            if result is None:
                continue
            all_results.append(result)

        # ---- 4. 跨品种总敞口控制 ----
        all_results = dtb.apply_global_exposure_limit(
            all_results,
            max_total_exposure=dtb.MAX_TOTAL_EXPOSURE,
            fee_rate=engine_params.get("fee_rate", 0.0001),
        )

        # ---- 5. 为每笔交易生成 K 线复盘图 ----
        for r in all_results:
            attach_charts(r, engine_params)

        # ---- 6. 构建 experiment envelope ----
        from emit_experiment import _summarize

        ts = datetime.now()
        strategy_type = "double_top_short"
        mode_part = label.strip().replace(" ", "_") if label else "web"
        base_id = ts.strftime("%Y%m%d_%H%M%S") + f"_{strategy_type}_{mode_part}"

        summary = _summarize(all_results)

        envelope = {
            "experiment_id": base_id,
            "generated_at": ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "strategy_name": "日线双峰左侧做空",
            "strategy_type": strategy_type,
            "direction": "short",
            "mode": "web",
            "label": label,
            "params": params,
            "capital": {
                "total_capital": params.get("total_capital", 10_000_000),
                "max_per_trade": params.get("max_per_trade", 1_000_000),
                "max_total_exposure": params.get("max_total_exposure", 6_000_000),
            },
            "summary": summary,
            "instruments": all_results,
        }

        return envelope

    # ---- 文件写入 ----

    def write_experiment(self, envelope: dict, session_slug: str) -> Path:
        """将 experiment dict 写入会话目录，返回写入路径。

        路径: EXPERIMENTS_DIR/_sessions/<session_slug>/<experiment_id>/experiment.json
        """
        exp_id = envelope["experiment_id"]
        out_dir = SESSIONS_DIR / session_slug / exp_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "experiment.json"
        out_path.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        return out_path

    @staticmethod
    def get_experiment_path(session_slug: str, experiment_id: str) -> Path | None:
        """获取会话下某个 experiment.json 的路径，不存在返回 None。"""
        p = SESSIONS_DIR / session_slug / experiment_id / "experiment.json"
        return p if p.exists() else None

    @staticmethod
    def read_experiment(session_slug: str, experiment_id: str) -> dict | None:
        """读取 experiment.json，不存在返回 None。"""
        p = BacktestEngine.get_experiment_path(session_slug, experiment_id)
        if p is None:
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def delete_experiment(session_slug: str, experiment_id: str) -> bool:
        """删除 experiment 目录，返回是否删除成功。"""
        p = SESSIONS_DIR / session_slug / experiment_id
        if p.exists():
            import shutil
            shutil.rmtree(p, ignore_errors=True)
            return True
        return False
