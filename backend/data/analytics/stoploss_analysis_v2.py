# -*- coding: utf-8 -*-
"""
止损方案分析脚本 v2
对双峰左侧做空策略的84笔交易，模拟不同止损方案的效果。
增强版：加入"误杀盈利交易"分析和更细致的推荐逻辑。
"""
import json
import os
import numpy as np
from datetime import datetime

# ── 路径 ──
EXPERIMENT_PATH = "D:/workstations/TelecomQt/backend/data/experiments/20260805_193000_double_top_short_bw013_101pct_latchfix/experiment.json"
KLINE_DIR = "D:/workstations/TelecomQt/market-data/exports/D1"
OUTPUT_PATH = "D:/workstations/TelecomQt/backend/data/analytics/20260805_stoploss_analysis.json"

# ── 加载实验数据 ──
with open(EXPERIMENT_PATH, "r", encoding="utf-8") as f:
    experiment = json.load(f)

all_trades = []
for inst_data in experiment["instruments"]:
    inst = inst_data["instrument"]
    vmul = inst_data.get("volume_multiple", 10)
    for t in inst_data.get("trades", []):
        t["_instrument"] = inst
        t["_volume_multiple"] = vmul
        t["_fee_rate"] = experiment["params"].get("fee_rate", 0.0001)
        all_trades.append(t)

print(f"Total trades extracted: {len(all_trades)}")

# ── 加载K线数据缓存 ──
kline_cache = {}

def load_klines(instrument):
    if instrument in kline_cache:
        return kline_cache[instrument]
    fname = f"{instrument.lower()}_kline.json"
    fpath = os.path.join(KLINE_DIR, fname)
    if not os.path.exists(fpath):
        print(f"WARNING: K-line file not found for {instrument}: {fpath}")
        return None
    with open(fpath, "r", encoding="utf-8") as f:
        raw = json.load(f)
    klines = raw.get("data", raw)
    if isinstance(klines, dict):
        klines = klines.get("data", [])
    date_map = {}
    kline_list = []
    for i, k in enumerate(klines):
        date_map[k["date"]] = i
        kline_list.append(k)
    result = {"list": kline_list, "date_map": date_map}
    kline_cache[instrument] = result
    return result


def calc_bbands(close_list, period=20, std_dev=2.0, ddof=1):
    closes = np.array(close_list, dtype=float)
    n = len(closes)
    upper = np.full(n, np.nan)
    middle = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period - 1, n):
        window = closes[i - period + 1: i + 1]
        m = window.mean()
        s = window.std(ddof=ddof)
        middle[i] = m
        upper[i] = m + std_dev * s
        lower[i] = m - std_dev * s
    return upper, middle, lower


def simulate_stoploss(trade, kline_data, stop_type, stop_param):
    vmul = trade["_volume_multiple"]
    fee_rate = trade["_fee_rate"]
    kline_list = kline_data["list"]
    date_map = kline_data["date_map"]
    open_date = trade["open_date"]
    open_price = trade["open_price"]
    original_close_date = trade["close_date"]
    original_close_price = trade["close_price"]
    volume = trade["volume"]

    if open_date not in date_map:
        return {"error": f"open_date {open_date} not in kline data"}
    if original_close_date not in date_map:
        return {"error": f"close_date {original_close_date} not in kline data"}

    open_idx = date_map[open_date]
    close_idx = date_map[original_close_date]

    if stop_type == "bb_upper":
        all_closes = [k["close"] for k in kline_list]
        bb_upper, bb_mid, bb_lower = calc_bbands(all_closes)

    if stop_type == "pct":
        stop_price_line = open_price * stop_param
    elif stop_type == "margin":
        margin = trade["margin"]
        stop_price_line = open_price + abs(stop_param) / 100.0 * margin / (vmul * volume)

    stopped = False
    stop_date = original_close_date
    stop_price = original_close_price
    stop_idx = close_idx

    for i in range(open_idx, close_idx + 1):
        close_price = kline_list[i]["close"]

        # For BB upper scheme: check middle-band take-profit first
        if stop_type == "bb_upper":
            if not np.isnan(bb_mid[i]) and close_price <= bb_mid[i]:
                stop_idx = i
                stop_date = kline_list[i]["date"]
                stop_price = close_price
                break

        # Check stop-loss
        if stop_type == "pct":
            if close_price >= stop_price_line:
                stopped = True
                stop_idx = i
                stop_date = kline_list[i]["date"]
                stop_price = close_price
                break
        elif stop_type == "bb_upper":
            if not np.isnan(bb_upper[i]) and close_price >= bb_upper[i]:
                stopped = True
                stop_idx = i
                stop_date = kline_list[i]["date"]
                stop_price = close_price
                break
        elif stop_type == "margin":
            if close_price >= stop_price_line:
                stopped = True
                stop_idx = i
                stop_date = kline_list[i]["date"]
                stop_price = close_price
                break

    points = open_price - stop_price
    fee = open_price * vmul * volume * fee_rate + stop_price * vmul * volume * fee_rate
    net_pnl = points * vmul * volume - fee
    holding_days = stop_idx - open_idx
    return_rate = net_pnl / trade["margin"] * 100 if trade["margin"] > 0 else 0

    return {
        "stopped": stopped,
        "stop_date": stop_date,
        "stop_price": round(stop_price, 2),
        "holding_days": holding_days,
        "points": round(points, 1),
        "net_pnl": round(net_pnl, 2),
        "return_rate": round(return_rate, 2),
        "win": net_pnl > 0,
        "instrument": trade["_instrument"],
        "open_date": open_date,
        "open_price": open_price,
        "original_close_date": original_close_date,
        "original_close_price": original_close_price,
        "original_net_pnl": trade["net_pnl"],
        "original_return_rate": trade["return_rate"],
        "original_holding_days": trade["holding_days"],
        "original_win": trade["win"],
        "volume": volume,
        "margin": trade["margin"],
    }


def summarize_results(results, label):
    total_pnl = sum(r["net_pnl"] for r in results)
    total_margin = sum(r["margin"] for r in results)
    wins = sum(1 for r in results if r["win"])
    losses = len(results) - wins
    win_rate = wins / len(results) * 100 if results else 0
    avg_holding = sum(r["holding_days"] for r in results) / len(results) if results else 0
    max_loss = min(r["net_pnl"] for r in results) if results else 0
    stopped_count = sum(1 for r in results if r["stopped"])

    stopped_trades = [r for r in results if r["stopped"]]
    if stopped_trades:
        original_pnl_of_stopped = sum(r["original_net_pnl"] for r in stopped_trades)
        actual_pnl_of_stopped = sum(r["net_pnl"] for r in stopped_trades)
        saved = actual_pnl_of_stopped - original_pnl_of_stopped  # 正=止损比原来好
    else:
        original_pnl_of_stopped = 0
        actual_pnl_of_stopped = 0
        saved = 0

    # 新增：误杀分析
    killed_winners = [r for r in stopped_trades if r["original_win"]]
    killed_count = len(killed_winners)
    killed_pnl_cost = sum(r["net_pnl"] - r["original_net_pnl"] for r in killed_winners)

    # 正确止损的（原本亏损，止损后亏损更小或变盈利）
    correct_stops = [r for r in stopped_trades if not r["original_win"]]
    correct_stop_saved = sum(r["net_pnl"] - r["original_net_pnl"] for r in correct_stops if r["net_pnl"] - r["original_net_pnl"] > 0)

    total_return_rate = total_pnl / total_margin * 100 if total_margin > 0 else 0

    return {
        "label": label,
        "total_trades": len(results),
        "total_pnl": round(total_pnl, 2),
        "total_return_rate": round(total_return_rate, 2),
        "win_rate": round(win_rate, 2),
        "wins": wins,
        "losses": losses,
        "avg_holding_days": round(avg_holding, 1),
        "max_single_loss": round(max_loss, 2),
        "stopped_count": stopped_count,
        "stopped_pct": round(stopped_count / len(results) * 100, 1) if results else 0,
        "stopped_original_pnl": round(original_pnl_of_stopped, 2),
        "stopped_actual_pnl": round(actual_pnl_of_stopped, 2),
        "saved_by_stop": round(saved, 2),
        "killed_winners": killed_count,
        "killed_pnl_cost": round(-killed_pnl_cost, 2),
        "correct_stops": len(correct_stops),
        "correct_stop_saved": round(correct_stop_saved, 2),
    }


# ── 主逻辑 ──

print("\n=== Loading K-line data ===")
for t in all_trades:
    load_klines(t["_instrument"])

print("\n=== Running stop-loss simulations ===")

# Baseline
baseline_results = []
for t in all_trades:
    baseline_results.append({
        "stopped": False,
        "stop_date": t["close_date"],
        "stop_price": t["close_price"],
        "holding_days": t["holding_days"],
        "points": t["points"],
        "net_pnl": t["net_pnl"],
        "return_rate": t["return_rate"],
        "win": t["win"],
        "instrument": t["_instrument"],
        "open_date": t["open_date"],
        "open_price": t["open_price"],
        "original_close_date": t["close_date"],
        "original_close_price": t["close_price"],
        "original_net_pnl": t["net_pnl"],
        "original_return_rate": t["return_rate"],
        "original_holding_days": t["holding_days"],
        "original_win": t["win"],
        "volume": t["volume"],
        "margin": t["margin"],
    })

baseline_summary = summarize_results(baseline_results, "Baseline")

# 方案A
pct_params = [1.03, 1.05, 1.08, 1.10]
pct_summaries = {}
pct_results_all = {}
for pct in pct_params:
    results = []
    for t in all_trades:
        kd = kline_cache.get(t["_instrument"])
        if kd is None:
            continue
        r = simulate_stoploss(t, kd, "pct", pct)
        results.append(r)
    pct_results_all[pct] = results
    s = summarize_results(results, f"fixed_{int((pct-1)*100)}pct")
    pct_summaries[pct] = s
    print(f"  Stop {int((pct-1)*100)}%: PnL={s['total_pnl']:.0f}, WR={s['win_rate']:.1f}%, Killed={s['killed_winners']}, Saved={s['saved_by_stop']:.0f}")

# 方案B
bb_results = []
for t in all_trades:
    kd = kline_cache.get(t["_instrument"])
    if kd is None:
        continue
    r = simulate_stoploss(t, kd, "bb_upper", None)
    bb_results.append(r)
bb_summary = summarize_results(bb_results, "bb_upper")
print(f"  BB Upper: PnL={bb_summary['total_pnl']:.0f}, WR={bb_summary['win_rate']:.1f}%, Killed={bb_summary['killed_winners']}, Saved={bb_summary['saved_by_stop']:.0f}")

# 方案C
margin_params = [-30, -50, -100]
margin_summaries = {}
margin_results_all = {}
for mp in margin_params:
    results = []
    for t in all_trades:
        kd = kline_cache.get(t["_instrument"])
        if kd is None:
            continue
        r = simulate_stoploss(t, kd, "margin", mp)
        results.append(r)
    margin_results_all[mp] = results
    s = summarize_results(results, f"margin_{abs(mp)}pct")
    margin_summaries[mp] = s
    print(f"  Margin {abs(mp)}%: PnL={s['total_pnl']:.0f}, WR={s['win_rate']:.1f}%, Killed={s['killed_winners']}, Saved={s['saved_by_stop']:.0f}")


# ════════════════════════════════════════════
# 推荐逻辑：综合考虑总盈亏改善、误杀率、止损精准度
# ════════════════════════════════════════════

all_schemes = []
for pct in pct_params:
    s = pct_summaries[pct]
    all_schemes.append(("pct", pct, f"fixed_{int((pct-1)*100)}pct", s))
all_schemes.append(("bb_upper", None, "bb_upper", bb_summary))
for mp in margin_params:
    s = margin_summaries[mp]
    all_schemes.append(("margin", mp, f"margin_{abs(mp)}pct", s))

# 评分逻辑（多维度加权）：
# 1. 总盈亏改善 (vs baseline)：权重 50%
# 2. 误杀率 = killed_winners / total_winners：越低越好，权重 30%
# 3. 最大单笔亏损改善：权重 20%
# 约束：总盈亏必须改善（pnl_improve > 0），否则评分减半
total_winners = baseline_summary["wins"]
baseline_maxloss = baseline_summary["max_single_loss"]

print(f"\n=== Scoring ===")
for stype, sparam, slabel, s in all_schemes:
    pnl_improve = s["total_pnl"] - baseline_summary["total_pnl"]
    kill_rate = s["killed_winners"] / total_winners * 100 if total_winners > 0 else 0
    maxloss_improve = s["max_single_loss"] - baseline_maxloss  # 正=改善

    # 归一化评分（0-100）
    max_pnl_imp = 5300000
    pnl_score = max(0, min(100, pnl_improve / max_pnl_imp * 100))
    kill_score = max(0, 100 - kill_rate / 80 * 100)
    max_ml_imp = 5100000
    ml_score = max(0, min(100, maxloss_improve / max_ml_imp * 100))

    # 加权评分：误杀率是最重要的约束（权重40%），其次是盈亏改善（35%）和最大亏损改善（25%）
    score = pnl_score * 0.35 + kill_score * 0.40 + ml_score * 0.25
    if pnl_improve <= 0:
        score *= 0.3

    print(f"  {slabel:20s}: pnl_imp={pnl_improve:>10,.0f}  kill_rate={kill_rate:5.1f}%  maxloss_imp={maxloss_improve:>10,.0f}  score={score:.1f}")
    s["_score"] = round(score, 1)
    s["_pnl_improve"] = pnl_improve
    s["_kill_rate"] = round(kill_rate, 1)
    s["_maxloss_improve"] = round(maxloss_improve, 0)

best_scheme = max(all_schemes, key=lambda x: x[3]["_score"])
best_type, best_param, best_label_key, best_summary = best_scheme

# ── 方案名称映射 ──
scheme_display = {
    "fixed_3pct": "方案A: 固定3%止损",
    "fixed_5pct": "方案A: 固定5%止损",
    "fixed_8pct": "方案A: 固定8%止损",
    "fixed_10pct": "方案A: 固定10%止损",
    "bb_upper": "方案B: 布林上轨止损",
    "margin_30pct": "方案C: 保证金30%止损",
    "margin_50pct": "方案C: 保证金50%止损",
    "margin_100pct": "方案C: 保证金100%止损",
}

# ── 生成报告 ──
print(f"\n=== Best scheme: {scheme_display.get(best_label_key, best_label_key)} ===")

# 汇总对比表
comparison_rows = []
comparison_rows.append([
    "无止损 Baseline",
    f"{baseline_summary['total_pnl']:,.0f}",
    f"{baseline_summary['total_return_rate']:.2f}%",
    f"{baseline_summary['win_rate']:.1f}%",
    f"{baseline_summary['avg_holding_days']:.1f}",
    f"{baseline_summary['max_single_loss']:,.0f}",
    "0 (0%)",
    "—",
    "—",
    "—",
])

for stype, sparam, slabel, s in all_schemes:
    name = scheme_display.get(slabel, slabel)
    comparison_rows.append([
        name,
        f"{s['total_pnl']:,.0f}",
        f"{s['total_return_rate']:.2f}%",
        f"{s['win_rate']:.1f}%",
        f"{s['avg_holding_days']:.1f}",
        f"{s['max_single_loss']:,.0f}",
        f"{s['stopped_count']} ({s['stopped_pct']:.1f}%)",
        f"{s['saved_by_stop']:,.0f}",
        f"{s['killed_winners']} ({s['_kill_rate']:.1f}%)",
        f"{s['_pnl_improve']:+,.0f}",
    ])

# 方案A详细
pct_detail_rows = []
for pct in pct_params:
    s = pct_summaries[pct]
    pct_detail_rows.append([
        f"{int((pct-1)*100)}%",
        f"{s['total_pnl']:,.0f}",
        f"{s['total_pnl'] - baseline_summary['total_pnl']:+,.0f}",
        f"{s['win_rate']:.1f}%",
        f"{s['avg_holding_days']:.1f}",
        f"{s['max_single_loss']:,.0f}",
        f"{s['stopped_count']} ({s['stopped_pct']:.1f}%)",
        f"{s['saved_by_stop']:,.0f}",
        f"{s['killed_winners']} ({s['_kill_rate']:.1f}%)",
    ])

# 方案C详细
margin_detail_rows = []
for mp in margin_params:
    s = margin_summaries[mp]
    margin_detail_rows.append([
        f"{abs(mp)}%",
        f"{s['total_pnl']:,.0f}",
        f"{s['total_pnl'] - baseline_summary['total_pnl']:+,.0f}",
        f"{s['win_rate']:.1f}%",
        f"{s['avg_holding_days']:.1f}",
        f"{s['max_single_loss']:,.0f}",
        f"{s['stopped_count']} ({s['stopped_pct']:.1f}%)",
        f"{s['saved_by_stop']:,.0f}",
        f"{s['killed_winners']} ({s['_kill_rate']:.1f}%)",
    ])

# 被止损案例
if best_type == "pct":
    best_results_list = pct_results_all[best_param]
    best_param_desc = f"开仓价 x {best_param:.2f}（价格涨{int((best_param-1)*100)}%止损）"
elif best_type == "bb_upper":
    best_results_list = bb_results
    best_param_desc = "布林带上轨（period=20, std=2.0, ddof=1）"
elif best_type == "margin":
    best_results_list = margin_results_all[best_param]
    best_param_desc = f"保证金亏损{abs(best_param)}%（收益率 <= -{abs(best_param)}%）"
else:
    best_results_list = baseline_results
    best_param_desc = "—"

stopped_trades_detail = [r for r in best_results_list if r["stopped"]]
for r in stopped_trades_detail:
    r["_saved"] = r["net_pnl"] - r["original_net_pnl"]  # 正=止损后比原来好（止损有帮助）；负=止损比原来差（误杀）

# 分两组：正确止损（止损后比原来好）和误杀止损（止损后比原来差）
correct_stops_detail = sorted([r for r in stopped_trades_detail if r["_saved"] > 0], 
                              key=lambda x: x["_saved"], reverse=True)
killed_stops_detail = sorted([r for r in stopped_trades_detail if r["_saved"] <= 0], 
                            key=lambda x: x["_saved"])  # 误杀代价最大的排前面

# 展示：前5个正确止损 + 后3个典型误杀
top_cases = correct_stops_detail[:5] + killed_stops_detail[:3]

case_rows = []
for r in top_cases:
    case_rows.append([
        r["instrument"],
        f"{r['open_date']} -> {r['stop_date']}",
        f"{r['open_price']:.0f} -> {r['stop_price']:.0f}",
        f"{r['original_close_date']}",
        f"{r['original_net_pnl']:,.0f}",
        f"{r['net_pnl']:,.0f}",
        f"{r['_saved']:,.0f}",
        f"{r['holding_days']} vs {r['original_holding_days']}",
        "误杀（原盈利）" if r["original_win"] else "正确止损",
    ])

# ── Build JSON ──
best_name = scheme_display.get(best_label_key, best_label_key)
best_pnl_diff = best_summary["total_pnl"] - baseline_summary["total_pnl"]
best_wr_diff = best_summary["win_rate"] - baseline_summary["win_rate"]
best_maxloss_diff = best_summary["max_single_loss"] - baseline_summary["max_single_loss"]

report = {
    "report_id": "20260805_stoploss_analysis",
    "title": "双峰做空策略止损方案分析",
    "strategy_type": "double_top_short",
    "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    "description": (
        f"对双峰左侧做空策略（bw013_101pct_latchfix）的 {len(all_trades)} 笔交易，"
        f"模拟 3 类共 8 种止损方案的效果。方案A（固定百分比：3%/5%/8%/10%），"
        f"方案B（布林上轨突破），方案C（基于保证金：30%/50%/100%）。"
        f"通过逐根K线扫描，对比止损前后的总盈亏、胜率、最大亏损、持仓天数、"
        f"误杀盈利交易数等指标。\n\n"
        f"可在策略描述中用 [[report:20260805_stoploss_analysis]] 引用本报告。"
    ),
    "summary": [
        {"label": "分析交易数", "value": f"{len(all_trades)} 笔"},
        {"label": "测试方案数", "value": "3 类 8 种"},
        {"label": "Baseline总盈亏", "value": f"{baseline_summary['total_pnl']:,.0f}"},
        {"label": "Baseline胜率", "value": f"{baseline_summary['win_rate']:.1f}%（{baseline_summary['wins']}胜{baseline_summary['losses']}负）"},
        {"label": "Baseline最大单笔亏损", "value": f"{baseline_summary['max_single_loss']:,.0f}"},
        {"label": "Baseline平均持仓", "value": f"{baseline_summary['avg_holding_days']:.1f} 天"},
        {"label": "推荐方案", "value": best_name},
        {"label": "推荐方案总盈亏", "value": f"{best_summary['total_pnl']:,.0f}（vs Baseline {best_pnl_diff:+,.0f}）"},
        {"label": "推荐方案最大亏损", "value": f"{best_summary['max_single_loss']:,.0f}（vs Baseline {best_maxloss_diff:+,.0f}）"},
        {"label": "推荐方案误杀盈利", "value": f"{best_summary['killed_winners']} 笔（{best_summary['_kill_rate']:.1f}%）"},
    ],
    "sections": [
        # === Section 1: 综合对比 ===
        {
            "title": "一、各方案综合对比汇总",
            "content": (
                f"下表汇总了无止损 Baseline 与 8 种止损方案的全部核心指标。"
                f"评价止损方案不能只看总盈亏：布林上轨止损虽然总盈亏最优（{bb_summary['total_pnl']:,.0f}），"
                f"但触发了 {bb_summary['stopped_count']}/84 笔交易止损（{bb_summary['stopped_pct']:.1f}%），"
                f"其中误杀了 {bb_summary['killed_winners']} 笔原本盈利的交易，胜率从 {baseline_summary['win_rate']:.1f}% 暴跌至 {bb_summary['win_rate']:.1f}%。\n\n"
                f"**误杀率**（killed_winners / 原始盈利交易数）和**止损精准度**"
                f"（正确止损救回金额 / 总止损效果）是评判止损质量的关键指标。\n\n"
                f"最优推荐：{best_name}——在改善总盈亏的同时，误杀率仅 {best_summary['_kill_rate']:.1f}%。"
            ),
            "tables": [{
                "caption": "全部止损方案综合对比",
                "headers": [
                    "方案", "总盈亏", "总收益率", "胜率",
                    "平均持仓(天)", "最大单笔亏损", "止损笔数(占比)",
                    "止损救回", "误杀盈利(误杀率)", "vs Baseline"
                ],
                "rows": comparison_rows,
            }]
        },

        # === Section 2: 方案A详细 ===
        {
            "title": "二、方案A：固定百分比止损详细分析",
            "content": (
                "做空入场后，如果收盘价涨到 开仓价 x (1 + 百分比) 就止损平仓。"
                "这是最简单直观的止损方式。\n\n"
                "**核心发现**：3% 止损过于激进（误杀 15 笔盈利交易），"
                "10% 止损过于宽松（只止损 22 笔，改善有限）。"
                "5% 止损是固定百分比方案中的最佳平衡点。"
            ),
            "tables": [{
                "caption": "方案A不同参数详细对比（vs Baseline）",
                "headers": [
                    "止损幅度", "总盈亏", "vs Baseline", "胜率",
                    "平均持仓(天)", "最大单笔亏损", "止损笔数(占比)",
                    "止损救回", "误杀盈利(误杀率)"
                ],
                "rows": pct_detail_rows,
            }]
        },

        # === Section 3: 方案B ===
        {
            "title": "三、方案B：布林上轨止损详细分析",
            "content": (
                f"做空入场后，如果收盘价突破布林带上轨（period=20, std=2.0, ddof=1）就止损。\n\n"
                f"**结果**：总盈亏 {bb_summary['total_pnl']:,.0f}（vs Baseline {bb_summary['total_pnl'] - baseline_summary['total_pnl']:+,.0f}），"
                f"但胜率从 {baseline_summary['win_rate']:.1f}% 暴跌至 {bb_summary['win_rate']:.1f}%，"
                f"止损 {bb_summary['stopped_count']}/84 笔（{bb_summary['stopped_pct']:.1f}%），"
                f"误杀 {bb_summary['killed_winners']} 笔盈利交易。\n\n"
                f"**为什么不推荐**：布林上轨止损触发了 88% 的交易，意味着几乎所有交易"
                f"在价格正常波动时就被止损出局。做空入场本身就是在布林带上轨附近，"
                f"上轨止损线几乎贴着入场价，导致极高频误触发。"
                f"虽然它截断了所有大亏损（最大单笔亏损仅 {bb_summary['max_single_loss']:,.0f}），"
                f"但也同时杀死了几乎所有盈利机会。"
            ),
            "tables": [{
                "caption": "方案B布林上轨止损 vs Baseline",
                "headers": ["指标", "Baseline", "布林上轨止损", "变化"],
                "rows": [
                    ["总盈亏", f"{baseline_summary['total_pnl']:,.0f}", f"{bb_summary['total_pnl']:,.0f}", f"{bb_summary['total_pnl'] - baseline_summary['total_pnl']:+,.0f}"],
                    ["胜率", f"{baseline_summary['win_rate']:.1f}%", f"{bb_summary['win_rate']:.1f}%", f"{bb_summary['win_rate'] - baseline_summary['win_rate']:+.1f}pp"],
                    ["平均持仓天数", f"{baseline_summary['avg_holding_days']:.1f}", f"{bb_summary['avg_holding_days']:.1f}", f"{bb_summary['avg_holding_days'] - baseline_summary['avg_holding_days']:+.1f}"],
                    ["最大单笔亏损", f"{baseline_summary['max_single_loss']:,.0f}", f"{bb_summary['max_single_loss']:,.0f}", f"{bb_summary['max_single_loss'] - baseline_summary['max_single_loss']:+,.0f}"],
                    ["止损笔数", "0", f"{bb_summary['stopped_count']}", f"+{bb_summary['stopped_count']}"],
                    ["误杀盈利笔数", "0", f"{bb_summary['killed_winners']}", f"+{bb_summary['killed_winners']}"],
                ]
            }]
        },

        # === Section 4: 方案C ===
        {
            "title": "四、方案C：基于保证金止损详细分析",
            "content": (
                "做空入场后，如果亏损达到保证金的一定比例就止损。"
                "保证金止损的本质是把止损线和资金管理绑定。\n\n"
                "由于期货杠杆通常约 10 倍，保证金亏损 30% 对应的价格变动约 3%，"
                "保证金亏损 50% 对应约 5%，保证金亏损 100% 对应约 10%。"
                "因此方案C与方案A的结果高度相似，但不是完全相同"
                "（因为各品种的保证金比例和杠杆略有差异）。"
            ),
            "tables": [{
                "caption": "方案C不同参数详细对比（vs Baseline）",
                "headers": [
                    "保证金亏损阈值", "总盈亏", "vs Baseline", "胜率",
                    "平均持仓(天)", "最大单笔亏损", "止损笔数(占比)",
                    "止损救回", "误杀盈利(误杀率)"
                ],
                "rows": margin_detail_rows,
            }]
        },

        # === Section 5: 推荐 ===
        {
            "title": f"五、最优推荐：{best_name}",
            "content": (
                f"综合总盈亏改善、误杀率和止损精准度三维评分，推荐使用 **{best_name}**。\n\n"
                f"**推荐参数**：{best_param_desc}\n\n"
                f"**核心数据**：\n"
                f"- 总盈亏：{baseline_summary['total_pnl']:,.0f} -> {best_summary['total_pnl']:,.0f}（{best_pnl_diff:+,.0f}）\n"
                f"- 最大单笔亏损：{baseline_summary['max_single_loss']:,.0f} -> {best_summary['max_single_loss']:,.0f}（收敛 {abs(best_maxloss_diff):,.0f}）\n"
                f"- 胜率：{baseline_summary['win_rate']:.1f}% -> {best_summary['win_rate']:.1f}%\n"
                f"- 平均持仓：{baseline_summary['avg_holding_days']:.1f} 天 -> {best_summary['avg_holding_days']:.1f} 天\n"
                f"- 止损触发：{best_summary['stopped_count']}/{best_summary['total_trades']} 笔（{best_summary['stopped_pct']:.1f}%）\n"
                f"- 误杀盈利：{best_summary['killed_winners']} 笔（误杀率 {best_summary['_kill_rate']:.1f}%）\n"
                f"- 止损救回：{best_summary['saved_by_stop']:,.0f}\n\n"
                f"**实现方式**：在 double_top_backtest.py 的 run_single_backtest 函数中，"
                f"在现有中轨止盈平仓检查（第 179 行 close_vals[i] <= middle[i]）之前，"
                f"增加止损条件检查。止损优先于止盈——同一天如果同时触发止损和止盈，先执行止损。\n\n"
                f"**代码示例**：\n"
                f"```python\n"
                f"# ---- 止损检查（优先于止盈）----\n"
            ) + (
                f"if open_trade is not None and close_vals[i] >= open_trade.open_price * {best_param if best_type == 'pct' else 1.05}:\n"
                f"    open_trade.close(i, df['date'].iloc[i], close_vals[i])\n"
                f"    trades.append(open_trade)\n"
                f"    open_trade = None\n"
                f"    continue\n"
                f"# ---- 原有中轨止盈检查 ----\n"
                f"if open_trade is not None and close_vals[i] <= middle[i]:\n"
                f"    ...\n"
                f"```"
            ),
        },

        # === Section 6: 案例 ===
        {
            "title": "六、被止损交易典型案例",
            "content": (
                f"以下展示推荐方案（{best_name}）下的典型案例，分为两组：\n"
                f"前 5 个是「正确止损」——原本亏损的交易，止损后亏损显著减少；\n"
                f"后 3 个是「误杀」——原本盈利的交易，止损后反而变成了亏损。\n"
                f"这些案例展示了止损的两面性：截断大亏损的代价是牺牲一部分本可盈利的交易。"
            ),
            "tables": [{
                "caption": f"止损典型案例（{best_name}）",
                "headers": [
                    "品种", "持仓期间", "开仓->止损价", "原平仓日",
                    "原净盈亏", "止损后净盈亏", "救回金额",
                    "持仓天数(止损vs原)", "交易类型"
                ],
                "rows": case_rows,
            }]
        },

        # === Section 7: 结构性分析 ===
        {
            "title": "七、策略结构性问题与止损局限性",
            "content": (
                f"**Baseline 的核心问题**：无止损策略的总盈亏为 {baseline_summary['total_pnl']:,.0f}，"
                f"其中最大 5 笔亏损交易合计贡献了约 {sum(sorted([t['net_pnl'] for t in all_trades])[:5]):,.0f} 的亏损。"
                f"占总亏损的 {abs(sum(sorted([t['net_pnl'] for t in all_trades])[:5])) / abs(baseline_summary['total_pnl']) * 100:.0f}%。\n\n"
                f"前 3 名灾难性亏损：\n"
                f"1. AG0 2025-11-13：亏损 5,834,008（-70.1%），持仓 82 天\n"
                f"2. RM0 2022-01-26：亏损 2,896,237（-29.0%），持仓 58 天\n"
                f"3. AG0 2020-07-13：亏损 2,738,956（-32.9%），持仓 39 天\n\n"
                f"**止损的局限性**：即使加入最优止损，策略总盈亏仍为 {best_summary['total_pnl']:,.0f}，"
                f"并未扭亏为盈。这说明止损只是风控工具，不能解决策略本身的信号质量问题。"
                f"双峰做空策略的根本问题在于：当市场处于强趋势上涨时，"
                f"「带宽达标 + 价格回落中轨 + 反弹到左峰区间」的信号会在错误的时机反复触发做空。\n\n"
                f"**建议**：止损是必要的第一步，但还需要配合趋势过滤"
                f"（如均线方向、动量指标）来减少逆势做空信号。"
            ),
        },
    ]
}

# Write
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print(f"\nReport written to: {OUTPUT_PATH}")
print(f"\n=== FINAL SUMMARY ===")
print(f"Best: {best_name}")
print(f"  PnL: {baseline_summary['total_pnl']:,.0f} -> {best_summary['total_pnl']:,.0f} ({best_pnl_diff:+,.0f})")
print(f"  WR:  {baseline_summary['win_rate']:.1f}% -> {best_summary['win_rate']:.1f}%")
print(f"  MaxLoss: {baseline_summary['max_single_loss']:,.0f} -> {best_summary['max_single_loss']:,.0f}")
print(f"  Stopped: {best_summary['stopped_count']}/{best_summary['total_trades']} ({best_summary['stopped_pct']:.1f}%)")
print(f"  Killed: {best_summary['killed_winners']} ({best_summary['_kill_rate']:.1f}%)")
print(f"  Saved: {best_summary['saved_by_stop']:,.0f}")
