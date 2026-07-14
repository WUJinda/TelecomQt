# -*- coding: utf-8 -*-
"""
emit_experiment.py —— LaiCai 回测结果 → experiment.json 适配器（drop-in）

把任意 LaiCai 回测脚本「已经算好的 per-instrument 结果列表」包成协作面板能读的
experiment.json。与具体策略引擎无关：boll / double_top / 任何 run_all 风格的输出都能用。

依赖：仅 numpy（LaiCai 本就用）。

------------------------------------------------------------------------------
接入方法（在你的回测脚本末尾加两行）：
------------------------------------------------------------------------------

    from emit_experiment import emit_experiment

    # all_results 就是你 run_all(data_dir, params) 的返回值
    emit_experiment(
        instruments=all_results,
        strategy_name="日线双峰左侧做空",
        strategy_type="double_top_short",
        direction="short",
        params={                                 # 本次参数快照
            "bb_period": 20, "bb_std": 2.0,
            "bandwidth_min": 0.15, "fee_rate": 0.0001,
        },
        capital={
            "total_capital": 10_000_000,
            "max_per_trade": 1_000_000,
            "max_total_exposure": 6_000_000,
        },
        out_dir="~/Desktop/experiments",         # 输出根目录，自动建 experiment_id 子目录
        mode="baseline",
    )

------------------------------------------------------------------------------
instruments 里每个品种至少要有这些字段（run_all 的输出天然满足）：
    instrument, exchange, kline_style, trade_count, trades[]
trades[] 里每笔至少要有：open_date, close_date, net_pnl, margin, holding_days, win
无交易的品种 trade_count=0、trades=[] 即可。

【可选】trades[i].chart —— 单笔交易复盘图（K 线窗口 + 布林三轨 + 关键位）。
    本函数原样透传、不解析；chart 由 LaiCai 侧构建（参考 build_chart.py 的 attach_charts）。
    字段结构见面板仓库 docs/experiment-schema.md。缺失时前端复盘区回退提示。
------------------------------------------------------------------------------
"""

import json
from datetime import datetime
from pathlib import Path

import numpy as np


def _summarize(instruments):
    """从 per-instrument 结果聚合出 summary（与 run_all 逻辑一致）。"""
    traded = [r for r in instruments if r.get("trade_count", len(r.get("trades", []))) > 0]
    all_trades = [t for r in traded for t in r.get("trades", [])]
    pnls = [t.get("net_pnl", 0) for t in all_trades]
    n = len(all_trades)

    total_pnl = round(sum(pnls), 2) if pnls else 0.0
    total_margin = round(sum(t.get("margin", 0) for t in all_trades), 2)
    wins = sum(1 for t in all_trades if t.get("win"))
    hold_days = [t.get("holding_days", 0) for t in all_trades]

    drawdown = 0.0
    if pnls:
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        drawdown = round(float((cum - peak).min()), 2)

    best = max(traded, key=lambda r: r.get("total_pnl", 0)) if traded else None
    worst = min(traded, key=lambda r: r.get("total_pnl", 0)) if traded else None

    return {
        "instruments_tested": len(instruments),
        "instruments_with_trades": len(traded),
        "total_trades": n,
        "total_pnl": total_pnl,
        "total_margin": total_margin,
        "total_return_rate": round(total_pnl / total_margin * 100, 2) if total_margin else 0.0,
        "win_rate": round(wins / n * 100, 1) if n else 0.0,
        "max_drawdown": drawdown,
        "avg_holding_days": round(sum(hold_days) / n, 1) if n else 0.0,
        "best_instrument": best["instrument"] if best else None,
        "worst_instrument": worst["instrument"] if worst else None,
    }


def emit_experiment(instruments, strategy_name, strategy_type, direction,
                    params=None, capital=None, out_dir=".", mode=""):
    """生成 experiment.json，写入 out_dir/<experiment_id>/experiment.json。"""
    ts = datetime.now()
    exp_id = ts.strftime("%Y%m%d_%H%M%S") + f"_{strategy_type}" + (f"_{mode}" if mode else "")

    envelope = {
        "experiment_id": exp_id,
        "generated_at": ts.strftime("%Y-%m-%dT%H:%M:%S"),
        "strategy_name": strategy_name,
        "strategy_type": strategy_type,
        "direction": direction,
        "mode": mode,
        "params": params or {},
        "capital": capital or {},
        "summary": _summarize(instruments),
        "instruments": instruments,
    }

    out_path = Path(out_dir).expanduser() / exp_id
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "experiment.json").write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[emit_experiment] 已写出: {out_path / 'experiment.json'}")
    return out_path


# ----------------------------------------------------------------------------
# 直接命令行运行：把指定目录下的 *_kline.json 跑一遍 boll 突破做空并导出
# （仅作演示；正式接入请在你的回测脚本里调用 emit_experiment()）
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import os

    # 复用同目录下 _batch_backtest 的引擎（演示用 boll 引擎）
    sys_path = os.path.dirname(os.path.abspath(__file__))
    import sys as _sys
    _sys.path.insert(0, sys_path)
    from _batch_backtest import run_all  # type: ignore

    p = argparse.ArgumentParser(description="跑 boll 突破做空并导出 experiment.json（演示）")
    p.add_argument("--data", required=True, help="K线 JSON 所在目录")
    p.add_argument("--out", default=".", help="输出根目录")
    p.add_argument("--name", default="布林带突破做空")
    args = p.parse_args()

    base_params = {"bb_period": 20, "bb_std": 2.0, "fee_rate": 0.0001,
                   "bandwidth_threshold": 0.04, "breakout_threshold": 0.02}
    results = run_all(args.data, base_params)
    emit_experiment(
        instruments=results,
        strategy_name=args.name,
        strategy_type="boll_breakout_short",
        direction="short",
        params=base_params,
        out_dir=args.out,
        mode="demo",
    )
