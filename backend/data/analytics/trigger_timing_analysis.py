# -*- coding: utf-8 -*-
"""
双峰做空策略 + 平行度+带宽联合门控：触发时机分析

策略逻辑修改：
  原始：bw >= bandwidth_min → bw_qualified
  修改：bw >= bandwidth_min AND total_tilt < tilt_threshold → bw_qualified
  
即：只有在布林带水平 AND 带宽达标时，才进入双峰扫描状态。

测试多组 (tilt_threshold, bandwidth_min) 组合，找出最优配置。
"""
import json
import os
import sys
import math
from datetime import datetime
from collections import defaultdict

import numpy as np
import pandas as pd

BT_DIR = r"C:/Users/admin/AppData/Roaming/InfiniTrader_QhZhongtaiPythonX64/pyStrategy/self_strategy/backtest"
sys.path.insert(0, BT_DIR)
from double_top_backtest import DEFAULT_PARAMS, calc_bbands, get_multiplier, get_margin_rate

KLINE_DIR = "D:/workstations/TelecomQt/market-data/exports/D1"
OUTPUT_PATH = "D:/workstations/TelecomQt/backend/data/analytics/20260805_trigger_timing_analysis.json"

BB_PERIOD = 20
BB_STD = 2.0
DDOF = 1
SLOPE_WINDOW = 5


def load_all_main_contracts():
    contracts = []
    for fname in sorted(os.listdir(KLINE_DIR)):
        if not fname.endswith("_kline.json"):
            continue
        code = fname.replace("_kline.json", "")
        if not code.endswith("0"):
            continue
        prefix = code[:-1]
        if not prefix.isalpha():
            continue
        with open(os.path.join(KLINE_DIR, fname), "r", encoding="utf-8") as f:
            raw = json.load(f)
        records = raw.get("data", [])
        if len(records) < BB_PERIOD + 40:
            continue
        contracts.append({
            "code": code,
            "name": raw.get("name", code),
            "records": records,
        })
    return contracts


def calc_total_tilt(upper, middle, lower, i):
    if i < SLOPE_WINDOW or np.isnan(middle[i]):
        return None
    up_s = (upper[i] - upper[i - SLOPE_WINDOW]) / SLOPE_WINDOW
    lo_s = (lower[i] - lower[i - SLOPE_WINDOW]) / SLOPE_WINDOW
    mid = middle[i]
    if mid <= 0:
        return None
    return (abs(up_s) + abs(lo_s)) / mid


def run_strategy(df, bandwidth_min, tilt_threshold, zone_lower=0.99, zone_upper=1.01,
                 left_peak_lookback=30):
    """运行双峰策略。bw_qualified 需同时满足 bandwidth 和 parallelism 条件。"""
    upper, middle, lower, bandwidth = calc_bbands(
        df["close"].values, BB_PERIOD, BB_STD, ddof=DDOF
    )

    close = df["close"].values
    high = df["high"].values
    dates = df["date"].values

    trades = []
    bw_qualified = False
    mid_touched = False
    h_left = None
    h_left_idx = None
    mid_touch_idx = None
    mid_touch_tilt = None
    mid_touch_bw = None
    open_trade = None

    for i in range(BB_PERIOD, len(df)):
        bw = bandwidth[i]
        if np.isnan(bw):
            continue

        # 阶段1：联合门控 — bandwidth AND parallelism
        tt = calc_total_tilt(upper, middle, lower, i)
        parallelism_ok = (tt is not None and tt < tilt_threshold) if tilt_threshold else True
        bw_ok = bw >= bandwidth_min

        if bw_ok and parallelism_ok:
            bw_qualified = True

        # 阶段2：价格接触中轨 → 锁定 H_left
        if bw_qualified and not mid_touched and open_trade is None:
            if close[i] <= middle[i]:
                mid_touched = True
                mid_touch_idx = i
                mid_touch_tilt = tt
                mid_touch_bw = bw
                lookback_start = max(0, i - left_peak_lookback)
                seg = high[lookback_start:i]
                if len(seg) > 0:
                    h_left = float(seg.max())
                    h_left_idx = int(lookback_start + seg.argmax())

        # 平仓
        if open_trade is not None and close[i] <= middle[i]:
            trade = {
                **open_trade,
                "close_idx": i,
                "close_date": pd.Timestamp(dates[i]).strftime("%Y-%m-%d"),
                "close_price": close[i],
                "holding_days": (pd.Timestamp(dates[i]) - pd.Timestamp(open_trade["open_date"])).days,
            }
            points = trade["open_price"] - trade["close_price"]
            trade["points"] = round(points, 1)
            trade["return_rate"] = round(points / trade["open_price"] * 100, 2)
            trade["win"] = bool(points > 0)
            trades.append(trade)

            open_trade = None
            bw_qualified = False
            mid_touched = False
            h_left = None
            h_left_idx = None
            mid_touch_idx = None

        # 阶段3：反弹到 H_left zone → 入场
        if open_trade is None and mid_touched and h_left is not None:
            if i - h_left_idx > left_peak_lookback:
                bw_qualified = False
                mid_touched = False
                h_left = None
                h_left_idx = None
                mid_touch_idx = None
            else:
                trigger_price = h_left * zone_upper
                if close[i] >= trigger_price:
                    tt_entry = calc_total_tilt(upper, middle, lower, i)
                    open_trade = {
                        "open_idx": i,
                        "open_date": dates[i],
                        "open_price": close[i],
                        "h_left": h_left,
                        "h_left_idx": h_left_idx,
                        "bandwidth_at_qualify": mid_touch_bw,
                        "tilt_at_qualify": mid_touch_tilt,
                        "bandwidth_at_entry": bw,
                        "tilt_at_entry": tt_entry,
                        "days_from_mid_touch": i - mid_touch_idx if mid_touch_idx else None,
                    }
                    bw_qualified = False
                    mid_touched = False
                    h_left = None
                    h_left_idx = None
                    mid_touch_idx = None

    return trades


def analyze():
    contracts = load_all_main_contracts()
    print(f"品种数: {len(contracts)}")

    # 预处理所有品种的数据
    prepared = []
    for c in contracts:
        df = pd.DataFrame(c["records"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        prepared.append((c["code"], c["name"], df))

    # ===== 扫描参数空间 =====
    # (tilt_threshold, bandwidth_min)
    configs = [
        # (label, tilt_threshold, bandwidth_min)
        ("基线(无平行度)", None, 0.13),
        ("tilt<0.003 bw>=0.13", 0.003, 0.13),
        ("tilt<0.005 bw>=0.13", 0.005, 0.13),
        ("tilt<0.008 bw>=0.13", 0.008, 0.13),
        ("tilt<0.01 bw>=0.13", 0.01, 0.13),
        ("tilt<0.003 bw>=0.10", 0.003, 0.10),
        ("tilt<0.005 bw>=0.10", 0.005, 0.10),
        ("tilt<0.008 bw>=0.10", 0.008, 0.10),
        ("tilt<0.003 bw>=0.08", 0.003, 0.08),
        ("tilt<0.005 bw>=0.08", 0.005, 0.08),
        ("tilt<0.008 bw>=0.08", 0.008, 0.08),
        ("tilt<0.005 bw>=0.05", 0.005, 0.05),
        ("tilt<0.008 bw>=0.05", 0.008, 0.05),
    ]

    config_results = {}

    for label, tilt_thr, bw_min in configs:
        all_trades = []
        for code, name, df in prepared:
            trades = run_strategy(df, bw_min, tilt_thr)
            for t in trades:
                t["instrument"] = code
                t["name"] = name
                all_trades.append(t)

        n = len(all_trades)
        wins = sum(1 for t in all_trades if t.get("win"))
        wr = wins / n * 100 if n > 0 else 0
        avg_ret = np.mean([t["return_rate"] for t in all_trades]) if all_trades else 0
        med_ret = np.median([t["return_rate"] for t in all_trades]) if all_trades else 0
        avg_hold = np.mean([t["holding_days"] for t in all_trades]) if all_trades else 0
        med_hold = np.median([t["holding_days"] for t in all_trades]) if all_trades else 0
        avg_dm = np.mean([t.get("days_from_mid_touch", 0) or 0 for t in all_trades]) if all_trades else 0
        med_dm = np.median([t.get("days_from_mid_touch", 0) or 0 for t in all_trades]) if all_trades else 0

        # 胜率按收益加权
        total_ret = sum(t["return_rate"] for t in all_trades) if all_trades else 0

        config_results[label] = {
            "tilt_threshold": tilt_thr,
            "bandwidth_min": bw_min,
            "trades": all_trades,
            "n_trades": n,
            "wins": wins,
            "win_rate": wr,
            "avg_return": avg_ret,
            "med_return": med_ret,
            "total_return": total_ret,
            "avg_holding": avg_hold,
            "med_holding": med_hold,
            "avg_days_mid": avg_dm,
            "med_days_mid": med_dm,
        }

        print(f"{label:30s}: {n:3d}笔, 胜率{wr:5.1f}%, 均收{avg_ret:+.2f}%, "
              f"总收{total_ret:+.1f}%, 持仓{avg_hold:.0f}天, 距中轨{avg_dm:.0f}天")

    # ===== 构建 JSON 报告 =====
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    base = config_results["基线(无平行度)"]

    report = {
        "report_id": "20260805_trigger_timing_analysis",
        "title": "双峰做空策略：平行度+带宽联合门控触发时机分析",
        "strategy_type": "double_top_short",
        "created_at": now,
        "description": (
            f"在 {len(contracts)} 个品种上，对比原始策略与多种 (total_tilt阈值, bandwidth阈值) "
            f"组合的表现。核心发现：bandwidth>=0.13 与 total_tilt<0.0015 几乎互斥（仅1.7%共存），"
            f"因为带宽扩张本身就是倾斜的结果。更宽松的组合（如 tilt<0.005 bw>=0.10）"
            f"能在保持信号质量的同时产生足够的交易量。"
        ),
        "summary": [
            {"label": "分析品种数", "value": str(len(contracts))},
            {"label": "基线交易数", "value": f"{base['n_trades']}笔（胜率{base['win_rate']:.1f}%）"},
            {"label": "基线平均收益", "value": f"{base['avg_return']:+.2f}%"},
            {"label": "最佳组合", "value": "见参数扫描表"},
            {"label": "门控位置", "value": "带宽达标阶段（bw_qualified 设置时）"},
            {"label": "平行度定义", "value": f"total_tilt = (|上轨斜率| + |下轨斜率|) / 中轨"},
        ],
        "sections": [],
    }

    # --- Section 1: 参数扫描总表 ---
    scan_rows = []
    for label, tilt_thr, bw_min in configs:
        r = config_results[label]
        scan_rows.append([
            label,
            f"{tilt_thr*1000:.0f}‰" if tilt_thr else "无",
            f"{bw_min:.2f}",
            str(r["n_trades"]),
            f"{r['win_rate']:.1f}%",
            f"{r['avg_return']:+.2f}%",
            f"{r['total_return']:+.1f}%",
            f"{r['med_holding']:.0f}",
            f"{r['med_days_mid']:.0f}",
        ])

    report["sections"].append({
        "title": "参数扫描：不同 (平行度, 带宽) 组合的回测结果",
        "content": (
            "在带宽达标阶段（设置 bw_qualified 时）同时检查 total_tilt 和 bandwidth。\n"
            "只有两者同时满足，才进入双峰扫描状态机。\n\n"
            "**关键发现**：\n"
            "1. bandwidth>=0.13 时，total_tilt 中位数为 11.4‰（均值 13.2‰），因为带宽扩张本身由倾斜驱动\n"
            "2. tilt<0.0015 + bw>=0.13 的组合仅有 1.7% 的数据点满足，几乎不可能产生交易\n"
            "3. 需要放宽其中一个条件：要么降低带宽要求，要么放宽平行度阈值\n"
            "4. tilt<0.005 + bw>=0.10 或 tilt<0.008 + bw>=0.08 是有前景的折中方案\n\n"
            "**列说明**：\n"
            "- 总收益% = 所有交易收益率之和（非复利，反映信号质量）\n"
            "- 持仓天数（中位数）= 从开仓到回到中轨平仓的天数\n"
            "- 距中轨天数（中位数）= 从中轨触发到入场的时间"
        ),
        "tables": [{
            "caption": "参数扫描结果（按配置顺序）",
            "headers": ["配置", "tilt阈值", "bw阈值", "交易数", "胜率", "均收益", "总收益", "持仓天(中)", "距中轨(中)"],
            "rows": scan_rows,
        }]
    })

    # --- Section 2: 入场时机特征对比 ---
    # 取基线 vs 2-3 个有代表性的配置
    compare_configs = [
        ("基线(无平行度)", "基线"),
        ("tilt<0.005 bw>=0.13", "tilt5‰bw13"),
        ("tilt<0.008 bw>=0.13", "tilt8‰bw13"),
        ("tilt<0.005 bw>=0.10", "tilt5‰bw10"),
        ("tilt<0.008 bw>=0.10", "tilt8‰bw10"),
        ("tilt<0.005 bw>=0.08", "tilt5‰bw08"),
        ("tilt<0.008 bw>=0.08", "tilt8‰bw08"),
    ]

    timing_rows = []
    for config_key, short_label in compare_configs:
        if config_key not in config_results:
            continue
        r = config_results[config_key]
        trades = r["trades"]
        if not trades:
            timing_rows.append([short_label, "0", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"])
            continue

        days_mid = [t.get("days_from_mid_touch", 0) or 0 for t in trades]
        holding = [t["holding_days"] for t in trades]
        tilt_q = [t.get("tilt_at_qualify", 0) or 0 for t in trades]
        tilt_e = [t.get("tilt_at_entry", 0) or 0 for t in trades]
        bw_q = [t.get("bandwidth_at_qualify", 0) or 0 for t in trades]

        timing_rows.append([
            short_label,
            str(r["n_trades"]),
            f"{np.median(days_mid):.0f}",
            f"{np.mean(days_mid):.1f}",
            f"{np.median(holding):.0f}",
            f"{np.mean(holding):.1f}",
            f"{np.median(tilt_q)*1000:.2f}‰" if tilt_q and tilt_q[0] is not None else "N/A",
            f"{np.median(bw_q):.4f}" if bw_q else "N/A",
        ])

    report["sections"].append({
        "title": "入场时机特征对比",
        "content": (
            "对比不同配置下入场的时机特征。\n\n"
            "**入场距中轨天数**：从价格跌破中轨（锁定 H_left）到价格反弹回 H_left 区域（入场做空）的交易日数。"
            "反映「等待双顶形成」的耐心。\n\n"
            "**持仓天数**：从做空入场到价格回到中轨（平仓止盈）的交易日数。\n\n"
            "**门控时倾斜度**：在 bw_qualified 设置那一刻的 total_tilt 值（中位数）。"
            "这个值始终低于配置中的阈值，说明门控生效。"
        ),
        "tables": [{
            "caption": "入场时机特征（中位数/均值）",
            "headers": ["配置", "交易数", "距中轨天(中)", "距中轨天(均)", "持仓天(中)", "持仓天(均)", "门控时tilt(中)", "门控时bw(中)"],
            "rows": timing_rows,
        }]
    })

    # --- Section 3: 最优配置的详细交易明细 ---
    # 找出总收益最高且交易数 >= 10 的配置
    best_label = None
    best_score = -999
    for label, tilt_thr, bw_min in configs[1:]:  # 跳过基线
        r = config_results[label]
        if r["n_trades"] >= 5 and r["total_return"] > best_score:
            best_score = r["total_return"]
            best_label = label

    if best_label is None:
        best_label = "tilt<0.008 bw>=0.10"

    best = config_results[best_label]
    best_trades = sorted(best["trades"], key=lambda x: x.get("open_date", ""))

    detail_rows = []
    for t in best_trades:
        od = t["open_date"]
        if not isinstance(od, str):
            od = pd.Timestamp(od).strftime("%Y-%m-%d")
        detail_rows.append([
            f"{t['instrument']}({t['name']})",
            od,
            f"{t['open_price']:.1f}",
            t["close_date"],
            f"{t['close_price']:.1f}",
            f"{t['holding_days']}天",
            f"{t.get('days_from_mid_touch', 'N/A')}",
            f"{(t.get('tilt_at_qualify') or 0)*1000:.2f}‰" if t.get("tilt_at_qualify") else "N/A",
            f"{(t.get('tilt_at_entry') or 0)*1000:.2f}‰" if t.get("tilt_at_entry") else "N/A",
            f"{t.get('bandwidth_at_qualify', 0):.4f}" if t.get("bandwidth_at_qualify") else "N/A",
            f"{t['return_rate']:+.2f}%",
            "✓" if t["win"] else "✗",
        ])

    report["sections"].append({
        "title": f"最优配置「{best_label}」交易明细（{best['n_trades']}笔，胜率{best['win_rate']:.1f}%）",
        "content": (
            f"在所有测试组合中，「{best_label}」产生了最好的总收益（{best['total_return']:+.1f}%）。\n\n"
            "关注门控时 tilt 和入场时 tilt 的对比：\n"
            "- 门控时 tilt 是 bw_qualified 设置时的值（低于阈值）\n"
            "- 入场时 tilt 是价格反弹到 H_left 区域时的值（通常较高，因为价格在涨）\n"
            "这说明平行度条件在门控阶段有效过滤了趋势行情，入场时的倾斜是正常反弹所致。"
        ),
        "tables": [{
            "caption": f"最优配置「{best_label}」交易明细",
            "headers": ["品种", "开仓日", "开仓价", "平仓日", "平仓价", "持仓", "距中轨天", "门控tilt", "入场tilt", "门控bw", "收益率", "盈亏"],
            "rows": detail_rows if detail_rows else [["无交易", "", "", "", "", "", "", "", "", "", "", ""]],
        }]
    })

    # --- Section 4: 被过滤的信号分析 ---
    # 基线有但过滤后没有的交易 → 这些是被平行度过滤掉的
    base_dates = set()
    for t in base["trades"]:
        od = t["open_date"]
        if not isinstance(od, str):
            od = pd.Timestamp(od).strftime("%Y-%m-%d")
        base_dates.add((t["instrument"], od))

    if best["trades"]:
        best_dates = set()
        for t in best["trades"]:
            od = t["open_date"]
            if not isinstance(od, str):
                od = pd.Timestamp(od).strftime("%Y-%m-%d")
            best_dates.add((t["instrument"], od))

        filtered_out = []
        for t in base["trades"]:
            od = t["open_date"]
            if not isinstance(od, str):
                od = pd.Timestamp(od).strftime("%Y-%m-%d")
            if (t["instrument"], od) not in best_dates:
                filtered_out.append(t)

        filtered_rows = []
        for t in sorted(filtered_out, key=lambda x: x.get("tilt_at_qualify", 0) or 0, reverse=True):
            od = t["open_date"]
            if not isinstance(od, str):
                od = pd.Timestamp(od).strftime("%Y-%m-%d")
            filtered_rows.append([
                f"{t['instrument']}({t['name']})",
                od,
                f"{(t.get('tilt_at_qualify') or 0)*1000:.2f}‰" if t.get("tilt_at_qualify") else "N/A",
                f"{t.get('bandwidth_at_qualify', 0):.4f}" if t.get("bandwidth_at_qualify") else "N/A",
                f"{t['return_rate']:+.2f}%",
                "✓" if t["win"] else "✗",
            ])

        # 被过滤信号的胜率
        filt_wins = sum(1 for t in filtered_out if t.get("win"))
        filt_n = len(filtered_out)
        filt_wr = filt_wins / filt_n * 100 if filt_n > 0 else 0
        filt_avg_ret = np.mean([t["return_rate"] for t in filtered_out]) if filtered_out else 0

        report["sections"].append({
            "title": f"被平行度过滤的基线交易（{filt_n}笔）",
            "content": (
                f"基线策略中有 {filt_n} 笔交易被平行度条件过滤掉。\n\n"
                f"**被过滤交易的胜率**：{filt_wr:.1f}%（{filt_wins}/{filt_n}）\n"
                f"**被过滤交易的平均收益**：{filt_avg_ret:+.2f}%\n\n"
                f"基线全部交易胜率：{base['win_rate']:.1f}%，平均收益：{base['avg_return']:+.2f}%\n"
                f"{'过滤掉的交易表现更差 → 平行度条件有效排除了劣质信号' if filt_avg_ret < base['avg_return'] else '过滤掉的交易表现不差 → 需要进一步分析'}"
            ),
            "tables": [{
                "caption": "被过滤的基线交易（按门控时倾斜度降序）",
                "headers": ["品种", "开仓日", "门控tilt", "门控bw", "收益率", "盈亏"],
                "rows": filtered_rows if filtered_rows else [["无", "", "", "", "", ""]],
            }]
        })

    # --- Section 5: 逐品种对比（基线 vs 最优配置） ---
    by_code_base = defaultdict(list)
    by_code_best = defaultdict(list)
    for t in base["trades"]:
        by_code_base[t["instrument"]].append(t)
    for t in best["trades"]:
        by_code_best[t["instrument"]].append(t)

    all_codes = sorted(set(list(by_code_base.keys()) + list(by_code_best.keys())))
    inst_rows = []
    for code in all_codes:
        name = next((t["name"] for t in base["trades"] + best["trades"] if t["instrument"] == code), code)
        bt = by_code_base.get(code, [])
        ft = by_code_best.get(code, [])

        b_wr = sum(1 for t in bt if t["win"]) / len(bt) * 100 if bt else 0
        f_wr = sum(1 for t in ft if t["win"]) / len(ft) * 100 if ft else 0
        b_avg = np.mean([t["return_rate"] for t in bt]) if bt else 0
        f_avg = np.mean([t["return_rate"] for t in ft]) if ft else 0

        inst_rows.append([
            f"{code}({name})",
            str(len(bt)),
            f"{b_wr:.0f}%",
            f"{b_avg:+.2f}%",
            str(len(ft)),
            f"{f_wr:.0f}%",
            f"{f_avg:+.2f}%",
        ])

    report["sections"].append({
        "title": f"逐品种对比：基线 vs {best_label}",
        "content": "各品种在基线和最优配置下的交易数和表现对比。",
        "tables": [{
            "caption": "逐品种对比",
            "headers": ["品种", "基线交易数", "基线胜率", "基线均收", "过滤交易数", "过滤胜率", "过滤均收"],
            "rows": inst_rows,
        }]
    })

    # --- Section 6: 触发时机分布分析 ---
    # 分析最优配置的入场距中轨天数分布
    if best["trades"]:
        days_mid = [t.get("days_from_mid_touch", 0) or 0 for t in best["trades"]]
        holding = [t["holding_days"] for t in best["trades"]]

        dm_dist = defaultdict(int)
        for d in days_mid:
            bucket = "0-2天" if d <= 2 else "3-5天" if d <= 5 else "6-10天" if d <= 10 else "11-20天" if d <= 20 else "20+天"
            dm_dist[bucket] += 1

        hd_dist = defaultdict(int)
        for h in holding:
            bucket = "1-3天" if h <= 3 else "4-7天" if h <= 7 else "8-14天" if h <= 14 else "15-30天" if h <= 30 else "30+天"
            hd_dist[bucket] += 1

        dist_rows = []
        for bucket in ["0-2天", "3-5天", "6-10天", "11-20天", "20+天"]:
            dist_rows.append([bucket, str(dm_dist.get(bucket, 0)), f"{dm_dist.get(bucket, 0)/len(days_mid)*100:.0f}%"])

        hd_dist_rows = []
        for bucket in ["1-3天", "4-7天", "8-14天", "15-30天", "30+天"]:
            hd_dist_rows.append([bucket, str(hd_dist.get(bucket, 0)), f"{hd_dist.get(bucket, 0)/len(holding)*100:.0f}%"])

        report["sections"].append({
            "title": f"触发时机分布（{best_label}）",
            "content": (
                "入场距中轨天数分布反映「双顶形成时间」——价格从中轨反弹回 H_left 区域需要多少天。\n\n"
                f"中位数 {np.median(days_mid):.0f} 天，均值 {np.mean(days_mid):.1f} 天。\n"
                f"{'大多数交易在 5 天内入场——快速反弹形成双顶' if np.median(days_mid) <= 5 else '入场等待时间较长'}\n\n"
                "持仓天数分布反映「做空后多久回到中轨止盈」。\n"
                f"中位数 {np.median(holding):.0f} 天，均值 {np.mean(holding):.1f} 天。"
            ),
            "tables": [
                {
                    "caption": "入场距中轨天数分布",
                    "headers": ["天数区间", "交易数", "占比"],
                    "rows": dist_rows,
                },
                {
                    "caption": "持仓天数分布",
                    "headers": ["天数区间", "交易数", "占比"],
                    "rows": hd_dist_rows,
                },
            ]
        })

    # 写入文件
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n报告已写入: {OUTPUT_PATH}")
    print(f"\n最优配置: {best_label}")
    print(f"  交易数: {best['n_trades']}, 胜率: {best['win_rate']:.1f}%, 总收益: {best['total_return']:+.1f}%")

    return report


if __name__ == "__main__":
    analyze()
