# -*- coding: utf-8 -*-
"""盈利单最大浮亏分析。

对每笔盈利交易，计算持仓期间的最大浮亏（最高价 - 开仓价，做空方向）。
输出标准 analytics 报告 JSON。
"""
import json
from datetime import datetime
from pathlib import Path

EXP_PATH = Path(__file__).resolve().parents[2] / "data" / "experiments" / "20260729_220432_double_top_short_baseline" / "experiment.json"
OUT_PATH = Path(__file__).resolve().parent / f"{datetime.now():%Y%m%d}_max_drawdown_in_wins.json"


def main():
    with open(EXP_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 收集所有盈利单的最大浮亏
    win_trades = []
    missing_chart = []

    for inst in data["instruments"]:
        for t in inst.get("trades", []):
            if not t.get("win"):
                continue

            open_price = t["open_price"]
            open_date = t["open_date"]
            close_date = t["close_date"]
            net_pnl = t["net_pnl"]
            points = t["points"]

            chart = t.get("chart", {})
            klines = chart.get("klines", [])

            if not klines:
                missing_chart.append({
                    "instrument": inst["instrument"],
                    "open_date": open_date,
                    "close_date": close_date,
                })
                continue

            # 在持仓期间找最高价（做空最大浮亏 = 最高价 - 开仓价）
            holding_high = None
            holding_high_date = None
            max_float_loss = 0  # 正数表示浮亏

            for k in klines:
                if open_date <= k["date"] <= close_date:
                    high = k["high"]
                    float_loss = high - open_price
                    if float_loss > max_float_loss:
                        max_float_loss = float_loss
                        holding_high = high
                        holding_high_date = k["date"]

            # 转为收益率百分比
            max_float_loss_pct = round(max_float_loss / open_price * 100, 2) if open_price else 0

            win_trades.append({
                "instrument": inst["instrument"],
                "open_date": open_date,
                "close_date": close_date,
                "holding_days": t.get("holding_days", 0),
                "open_price": open_price,
                "close_price": t["close_price"],
                "final_points": points,
                "net_pnl": net_pnl,
                "return_rate": t.get("return_rate", 0),
                "max_float_loss": round(max_float_loss, 1),
                "max_float_loss_pct": max_float_loss_pct,
                "holding_high": holding_high,
                "holding_high_date": holding_high_date,
            })

    # 按最大浮亏排序（从大到小）
    win_trades.sort(key=lambda x: x["max_float_loss"], reverse=True)

    # 统计汇总
    losses = [t["max_float_loss"] for t in win_trades]
    losses_pct = [t["max_float_loss_pct"] for t in win_trades]

    n = len(win_trades)
    avg_loss = sum(losses) / n if n else 0
    avg_loss_pct = sum(losses_pct) / n if n else 0
    max_loss = max(losses) if losses else 0
    max_loss_pct = max(losses_pct) if losses_pct else 0
    median_loss = sorted(losses)[n // 2] if n else 0
    median_loss_pct = sorted(losses_pct)[n // 2] if n else 0

    # 分布
    bins = [
        ("≤0（从未浮亏）", lambda x: x <= 0),
        ("0~0.5%", lambda x: 0 < x <= 0.5),
        ("0.5%~1%", lambda x: 0.5 < x <= 1),
        ("1%~2%", lambda x: 1 < x <= 2),
        ("2%~3%", lambda x: 2 < x <= 3),
        ("3%~5%", lambda x: 3 < x <= 5),
        (">5%", lambda x: x > 5),
    ]
    distribution = []
    for label, fn in bins:
        count = sum(1 for x in losses_pct if fn(x))
        distribution.append([label, str(count), f"{count/n*100:.1f}%" if n else "0%"])

    # 报告 JSON
    report = {
        "report_id": f"{datetime.now():%Y%m%d}_max_drawdown_in_wins",
        "title": "双峰左侧做空：盈利单最大浮亏分析",
        "strategy_type": "double_top_short",
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "description": (
            f"统计 {n} 笔盈利交易在持仓期间经历的最大浮亏（最高价 - 开仓价，做空方向）。"
            f"用于评估盈利单的持仓压力，以及是否需要设置动态止损来保护已有盈利。\n\n"
            f"计算方式：在开仓日到平仓日期间，取每日最高价，最大浮亏 = max(最高价) - 开仓价。"
            f"百分比口径：最大浮亏 / 开仓价 × 100。"
        ),
        "summary": [
            {"label": "盈利单总数", "value": str(n)},
            {"label": "平均最大浮亏", "value": f"{avg_loss:.1f} 点 ({avg_loss_pct:.2f}%)"},
            {"label": "中位数浮亏", "value": f"{median_loss:.1f} 点 ({median_loss_pct:.2f}%)"},
            {"label": "最大浮亏", "value": f"{max_loss:.1f} 点 ({max_loss_pct:.2f}%)"},
            {"label": "从未浮亏占比", "value": f"{sum(1 for x in losses_pct if x <= 0)}/{n} ({sum(1 for x in losses_pct if x <= 0)/n*100:.1f}%)" if n else "0"},
            {"label": "数据来源", "value": "20260729 基线实验"},
        ],
        "sections": [
            {
                "title": "最大浮亏分布",
                "content": "盈利单在最终盈利前，经历了多大的回撤压力。",
                "tables": [
                    {
                        "caption": "浮亏区间分布（按价格变动百分比）",
                        "headers": ["浮亏区间", "笔数", "占比"],
                        "rows": distribution,
                    }
                ],
            },
            {
                "title": "逐笔明细（按最大浮亏从大到小）",
                "content": f"共 {n} 笔盈利交易。标注日期为浮亏最大日。",
                "tables": [
                    {
                        "caption": "盈利单浮亏明细",
                        "headers": [
                            "品种", "开仓日", "平仓日", "持仓天",
                            "开仓价", "最大浮亏(点)", "浮亏%",
                            "浮亏最高价", "浮亏最大日", "最终点数", "净盈亏"
                        ],
                        "rows": [
                            [
                                t["instrument"],
                                t["open_date"],
                                t["close_date"],
                                str(t["holding_days"]),
                                str(t["open_price"]),
                                str(t["max_float_loss"]),
                                f"{t['max_float_loss_pct']:.2f}%",
                                str(t["holding_high"] or "—"),
                                t["holding_high_date"] or "—",
                                f"+{t['final_points']}" if t["final_points"] >= 0 else str(t["final_points"]),
                                f"+{t['net_pnl']:,.0f}" if t["net_pnl"] >= 0 else f"{t['net_pnl']:,.0f}",
                            ]
                            for t in win_trades
                        ],
                    }
                ],
            },
        ],
    }

    if missing_chart:
        report["sections"].append({
            "title": "缺失 K 线数据的盈利单",
            "content": f"以下 {len(missing_chart)} 笔盈利单没有 chart 数据，无法计算浮亏。",
            "tables": [{
                "caption": "缺失数据列表",
                "headers": ["品种", "开仓日", "平仓日"],
                "rows": [[m["instrument"], m["open_date"], m["close_date"]] for m in missing_chart],
            }],
        })

    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已生成: {OUT_PATH}")
    print(f"盈利单: {n} 笔, 平均最大浮亏: {avg_loss:.1f} ({avg_loss_pct:.2f}%), 最大浮亏: {max_loss:.1f} ({max_loss_pct:.2f}%)")


if __name__ == "__main__":
    main()
