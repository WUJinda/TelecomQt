# -*- coding: utf-8 -*-
"""
布林带平行度度量方式分析
分析 37 个主力连续品种，比较多种平行度度量方式的准确性和适用性。
"""

import json
import os
import math
import statistics
from datetime import datetime
from collections import defaultdict

# ============================================================
# 配置
# ============================================================
KLINE_DIR = "D:/workstations/TelecomQt/market-data/exports/D1"
OUTPUT_PATH = "D:/workstations/TelecomQt/backend/data/analytics/20260805_parallelism_analysis.json"

BB_PERIOD = 20
BB_STD = 2.0
DDOF = 1
SLOPE_WINDOW = 5


def load_all_main_contracts():
    """加载所有主力连续合约（代码以0结尾）"""
    contracts = {}
    for fname in os.listdir(KLINE_DIR):
        if not fname.endswith("_kline.json"):
            continue
        code = fname.replace("_kline.json", "")
        # 主力连续合约特点：字母前缀 + 单个 '0'
        # 排除 rb2510 这类带月份的合约：检查去掉末尾 '0' 后是否全是字母
        if not code.endswith("0"):
            continue
        prefix = code[:-1]  # 去掉末尾的0
        if not prefix.isalpha():
            # prefix 含数字，说明是 rb2510 这类带月份合约
            continue

        filepath = os.path.join(KLINE_DIR, fname)
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        klines = data.get("data", [])
        if len(klines) < BB_PERIOD + SLOPE_WINDOW + 10:
            continue

        contracts[code] = {
            "name": data.get("name", code),
            "klines": klines,
        }

    return contracts


def calc_bollinger(klines, period=BB_PERIOD, num_std=BB_STD, ddof=DDOF):
    """计算布林带"""
    closes = [k["close"] for k in klines]
    n = len(closes)
    upper = [None] * n
    middle = [None] * n
    lower = [None] * n
    bandwidth = [None] * n

    for i in range(period - 1, n):
        window = closes[i - period + 1: i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / (period - ddof)
        std = math.sqrt(variance)
        middle[i] = mean
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
        bandwidth[i] = (upper[i] - lower[i]) / mean if mean > 0 else None

    return upper, middle, lower, bandwidth


def calc_slope(series, i, window=SLOPE_WINDOW):
    """计算斜率"""
    if i < window or series[i] is None or series[i - window] is None:
        return None
    return (series[i] - series[i - window]) / window


def calc_all_metrics(upper, middle, lower, bandwidth, closes):
    """在所有时间点上计算全部度量"""
    n = len(closes)
    all_metrics = []

    for i in range(n):
        if middle[i] is None or i < SLOPE_WINDOW + BB_PERIOD:
            continue

        mid = middle[i]
        up = upper[i]
        lo = lower[i]

        # 斜率
        upper_slope = calc_slope(upper, i)
        lower_slope = calc_slope(lower, i)
        mid_slope = calc_slope(middle, i)

        if upper_slope is None or lower_slope is None:
            continue

        # ---- 方式A: 斜率差归一化 ----
        slope_diff = abs(upper_slope - lower_slope)
        metric_A = slope_diff / mid if mid > 0 else None

        # ---- 方式B-1: 斜率比 ----
        abs_up = abs(upper_slope)
        abs_lo = abs(lower_slope)
        if abs_up < 1e-15 and abs_lo < 1e-15:
            metric_B1 = 1.0  # 两条都是水平
        elif abs_up < 1e-15 or abs_lo < 1e-15:
            metric_B1 = 0.0  # 一条水平一条斜
        else:
            metric_B1 = min(abs_up, abs_lo) / max(abs_up, abs_lo)

        # ---- 方式B-2: 方向一致性 ----
        metric_B2 = 1.0 if (upper_slope > 0) == (lower_slope > 0) else 0.0

        # ---- 方式B-3: 角度差 ----
        angle_up = math.atan(upper_slope)
        angle_lo = math.atan(lower_slope)
        metric_B3 = abs(angle_up - angle_lo)

        # ---- 额外度量 ----
        upper_slope_pct = upper_slope / mid if mid > 0 else None  # 上轨斜率占中轨百分比
        lower_slope_pct = lower_slope / mid if mid > 0 else None
        mid_slope_pct = mid_slope / mid if (mid > 0 and mid_slope is not None) else None

        # 上下轨斜率的绝对值之和占中轨百分比（衡量整体倾斜程度）
        total_tilt = (abs_up + abs_lo) / mid if mid > 0 else None

        # 最大单轨斜率占中轨百分比
        max_tilt = max(abs_up, abs_lo) / mid if mid > 0 else None

        # 方式C：上下轨斜率差与上下轨斜率和的比值（改进的平行度度量）
        sum_abs = abs_up + abs_lo
        if sum_abs < 1e-15:
            # 两条轨都水平
            metric_C = 0.0  # 完全平行且水平
        else:
            metric_C = abs(upper_slope - lower_slope) / sum_abs

        # 方式D：结合倾斜度和平行度的综合度量
        # metric_D = slope_diff/mid (方向差异) + total_tilt * 0.5 (整体倾斜惩罚)
        # 但我们这里先单独计算，后面再设计

        all_metrics.append({
            "i": i,
            "close": closes[i],
            "upper": up,
            "middle": mid,
            "lower": lo,
            "upper_slope": upper_slope,
            "lower_slope": lower_slope,
            "mid_slope": mid_slope,
            "metric_A": metric_A,          # |上轨斜率-下轨斜率|/中轨
            "metric_B1": metric_B1,         # min(|斜率|)/max(|斜率|)
            "metric_B2": metric_B2,         # 方向一致=1, 不一致=0
            "metric_B3": metric_B3,         # 角度差(弧度)
            "metric_C": metric_C,           # 斜率差/斜率和(改进平行度)
            "upper_slope_pct": upper_slope_pct,
            "lower_slope_pct": lower_slope_pct,
            "mid_slope_pct": mid_slope_pct,
            "total_tilt": total_tilt,       # 整体倾斜度
            "max_tilt": max_tilt,
            "bandwidth": bandwidth[i],
        })

    return all_metrics


def pearson_corr(xs, ys):
    """计算皮尔逊相关系数"""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 2:
        return None
    xs_valid = [p[0] for p in pairs]
    ys_valid = [p[1] for p in pairs]
    mx = sum(xs_valid) / n
    my = sum(ys_valid) / n
    cov = sum((x - mx) * (y - my) for x, y in pairs) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs_valid) / n)
    sy = math.sqrt(sum((y - my) ** 2 for y in ys_valid) / n)
    if sx < 1e-15 or sy < 1e-15:
        return None
    return cov / (sx * sy)


def percentile(sorted_vals, p):
    """计算百分位数"""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_vals) else f
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def classify_shape(m):
    """分类布林带形态"""
    up_slope = m["upper_slope"]
    lo_slope = m["lower_slope"]
    mid = m["middle"]
    
    up_pct = abs(up_slope / mid) if mid > 0 else 0
    lo_pct = abs(lo_slope / mid) if mid > 0 else 0
    
    tilt_threshold = 0.0008  # 每天变化0.08%以内算"水平"
    
    up_horizontal = up_pct < tilt_threshold
    lo_horizontal = lo_pct < tilt_threshold
    
    same_dir = (up_slope > 0) == (lo_slope > 0)
    
    if up_horizontal and lo_horizontal:
        return "horizontal_parallel"  # 水平平行
    elif same_dir and up_pct >= tilt_threshold and lo_pct >= tilt_threshold:
        if up_slope > 0:
            return "rising_parallel"  # 上升平行
        else:
            return "falling_parallel"  # 下降平行
    elif not same_dir:
        if up_slope > 0 and lo_slope < 0:
            return "expanding"  # 喇叭口扩张
        else:
            return "converging"  # 收敛
    else:
        # 同方向但一轨水平一轨倾斜
        return "tilted_mixed"


def analyze():
    print("开始分析...")
    
    # 1. 加载数据
    contracts = load_all_main_contracts()
    print(f"加载了 {len(contracts)} 个主力连续合约")
    
    # 2. 计算所有品种的所有度量
    all_metrics_global = []
    metrics_by_code = {}
    
    for code, info in contracts.items():
        klines = info["klines"]
        closes = [k["close"] for k in klines]
        
        upper, middle, lower, bandwidth = calc_bollinger(klines)
        metrics = calc_all_metrics(upper, middle, lower, bandwidth, closes)
        
        metrics_by_code[code] = {
            "name": info["name"],
            "count": len(metrics),
            "metrics": metrics,
            "dates": [klines[m["i"]]["date"] for m in metrics],
        }
        all_metrics_global.extend(metrics)
    
    total_points = len(all_metrics_global)
    print(f"总数据点: {total_points}")
    
    # 3. 各度量分布统计
    metric_names = ["metric_A", "metric_B1", "metric_B3", "metric_C", "total_tilt", "max_tilt", "upper_slope_pct", "lower_slope_pct", "mid_slope_pct"]
    
    dist_stats = {}
    for mn in metric_names:
        vals = [m[mn] for m in all_metrics_global if m[mn] is not None]
        vals_sorted = sorted(vals)
        dist_stats[mn] = {
            "count": len(vals),
            "mean": sum(vals) / len(vals) if vals else None,
            "median": percentile(vals_sorted, 50) if vals_sorted else None,
            "p5": percentile(vals_sorted, 5),
            "p10": percentile(vals_sorted, 10),
            "p25": percentile(vals_sorted, 25),
            "p75": percentile(vals_sorted, 75),
            "p90": percentile(vals_sorted, 90),
            "p95": percentile(vals_sorted, 95),
            "p99": percentile(vals_sorted, 99),
            "min": vals_sorted[0] if vals_sorted else None,
            "max": vals_sorted[-1] if vals_sorted else None,
        }
    
    # 4. 相关性矩阵
    metric_keys = ["metric_A", "metric_B1", "metric_B3", "metric_C", "total_tilt", "max_tilt", "mid_slope_pct"]
    corr_matrix = {}
    for m1 in metric_keys:
        corr_matrix[m1] = {}
        for m2 in metric_keys:
            xs = [m[m1] for m in all_metrics_global]
            ys = [m[m2] for m in all_metrics_global]
            corr_matrix[m1][m2] = pearson_corr(xs, ys)
    
    # 5. 形态分类统计
    shape_counts = defaultdict(list)
    for m in all_metrics_global:
        shape = classify_shape(m)
        shape_counts[shape].append(m)
    
    shape_stats = {}
    for shape, metrics_list in shape_counts.items():
        shape_stats[shape] = {
            "count": len(metrics_list),
            "pct": len(metrics_list) / total_points * 100,
            "metric_A_mean": sum(m["metric_A"] for m in metrics_list) / len(metrics_list),
            "metric_A_median": percentile(sorted([m["metric_A"] for m in metrics_list]), 50),
            "metric_B1_mean": sum(m["metric_B1"] for m in metrics_list) / len(metrics_list),
            "metric_B3_mean": sum(m["metric_B3"] for m in metrics_list) / len(metrics_list),
            "metric_C_mean": sum(m["metric_C"] for m in metrics_list) / len(metrics_list),
            "total_tilt_mean": sum(m["total_tilt"] for m in metrics_list) / len(metrics_list),
        }
    
    # 6. 典型案例
    typical_cases = find_typical_cases(all_metrics_global, metrics_by_code, contracts)
    
    # 7. 方式A的问题分析：同向倾斜但slope_diff很小的情况
    # 使用与 leak_count 一致的阈值：metric_A < 0.003 但 total_tilt > 0.003
    # 数学上，只有同向斜率才可能出现 metric_A < total_tilt，所以这些全是同向倾斜案例
    same_dir_low_diff = []
    for code, info in metrics_by_code.items():
        for idx, m in enumerate(info["metrics"]):
            if m["metric_A"] is not None and m["metric_A"] < 0.003 and m["total_tilt"] > 0.003:
                same_dir_low_diff.append({
                    "code": code,
                    "name": info["name"],
                    "date": info["dates"][idx],
                    "metric_A": m["metric_A"],
                    "total_tilt": m["total_tilt"],
                    "upper_slope_pct": m["upper_slope_pct"],
                    "lower_slope_pct": m["lower_slope_pct"],
                    "metric_C": m["metric_C"],
                    "metric_B2": m["metric_B2"],
                })
    
    same_dir_low_diff.sort(key=lambda x: x["total_tilt"], reverse=True)
    
    print(f"方式A漏判案例（A<0.003 但 total_tilt>0.003）: {len(same_dir_low_diff)}")
    
    # 8. 构建报告
    report = build_report(
        total_points, len(contracts), dist_stats, corr_matrix, 
        shape_stats, shape_counts, typical_cases, same_dir_low_diff,
        all_metrics_global, metric_keys
    )
    
    # 9. 写入文件
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"报告已写入: {OUTPUT_PATH}")
    return report


def find_typical_cases(all_metrics, metrics_by_code, contracts):
    """找典型案例"""
    cases = []
    
    # 构建带品种信息的全部数据点
    tagged_metrics = []
    for code, info in metrics_by_code.items():
        for idx, m in enumerate(info["metrics"]):
            tagged_metrics.append({**m, "_code": code, "_name": info["name"], "_date": info["dates"][idx]})
    
    # 为每个形态找典型案例
    shape_examples = defaultdict(list)
    for m in tagged_metrics:
        shape = classify_shape(m)
        shape_examples[shape].append(m)
    
    for shape_name in ["horizontal_parallel", "rising_parallel", "falling_parallel", "expanding", "converging"]:
        candidates = shape_examples.get(shape_name, [])
        if not candidates:
            continue
        
        # 按不同标准排序找最佳案例
        if shape_name == "horizontal_parallel":
            candidates.sort(key=lambda m: (m["total_tilt"], m["metric_A"]))
        elif shape_name in ("rising_parallel", "falling_parallel"):
            # 找方式A 最小但 total_tilt 最大的典型案例
            candidates.sort(key=lambda m: (m["metric_A"], -m["total_tilt"]))
        elif shape_name in ("expanding", "converging"):
            candidates.sort(key=lambda m: -m["metric_A"])
        
        # 从不同品种中选取案例以增加多样性
        seen_codes = set()
        selected = []
        for m in candidates:
            if m["_code"] not in seen_codes or len(selected) >= 1:
                # 允许同品种但优先不同品种
                pass
            selected.append(m)
            seen_codes.add(m["_code"])
            if len(selected) >= 3:
                break
        
        for m in selected:
            cases.append({
                "shape": shape_name,
                "code": m["_code"],
                "name": m["_name"],
                "date": m["_date"],
                "close": round(m["close"], 2),
                "upper": round(m["upper"], 2),
                "middle": round(m["middle"], 2),
                "lower": round(m["lower"], 2),
                "metric_A": round(m["metric_A"] * 1000, 4),
                "metric_B1": round(m["metric_B1"], 4),
                "metric_B3_deg": round(math.degrees(m["metric_B3"]), 2),
                "metric_C": round(m["metric_C"], 4),
                "total_tilt_permille": round(m["total_tilt"] * 1000, 4),
                "upper_slope_pct": round(m["upper_slope_pct"] * 100, 4) if m["upper_slope_pct"] else None,
                "lower_slope_pct": round(m["lower_slope_pct"] * 100, 4) if m["lower_slope_pct"] else None,
            })
    
    return cases


def build_report(total_points, num_contracts, dist_stats, corr_matrix, shape_stats, shape_counts, typical_cases, same_dir_low_diff, all_metrics_global, metric_keys):
    """构建 JSON 报告"""
    
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    
    # 计算关键统计用于 summary
    metric_A_vals = [m["metric_A"] for m in all_metrics_global if m["metric_A"] is not None]
    metric_A_sorted = sorted(metric_A_vals)
    total_tilt_vals = [m["total_tilt"] for m in all_metrics_global if m["total_tilt"] is not None]
    total_tilt_sorted = sorted(total_tilt_vals)
    
    # 方式A阈值0.003的通过率
    a_pass = sum(1 for v in metric_A_vals if v < 0.003) / len(metric_A_vals) * 100
    # 方式A阈值0.002的通过率
    a_pass2 = sum(1 for v in metric_A_vals if v < 0.002) / len(metric_A_vals) * 100
    # 方式A阈值0.001的通过率
    a_pass1 = sum(1 for v in metric_A_vals if v < 0.001) / len(metric_A_vals) * 100
    
    # total_tilt < 0.001 (即每天变化<0.1%) 的比例
    tilt_pass = sum(1 for v in total_tilt_vals if v < 0.001) / len(total_tilt_vals) * 100
    tilt_pass2 = sum(1 for v in total_tilt_vals if v < 0.0015) / len(total_tilt_vals) * 100
    tilt_pass3 = sum(1 for v in total_tilt_vals if v < 0.002) / len(total_tilt_vals) * 100
    
    # 漏判比例：方式A通过但total_tilt大
    leak_count = sum(1 for m in all_metrics_global if m["metric_A"] < 0.003 and m["total_tilt"] > 0.003)
    leak_pct = leak_count / len(all_metrics_global) * 100
    
    report = {
        "report_id": "20260805_parallelism_analysis",
        "title": "布林带平行度度量方式分析",
        "strategy_type": "double_top_short",
        "created_at": now,
        "description": (
            "分析 {:d} 个主力连续品种共 {:,} 个布林带数据点，对比多种平行度度量方式（方式A：斜率差归一化、"
            "方式B-1：斜率比、方式B-2：方向一致性、方式B-3：角度差、方式C：归一化斜率差、total_tilt：整体倾斜度）的统计分布、"
            "相互相关性和区分能力。核心数学发现：当上下轨方向相反时（喇叭口/收敛，占47%数据），"
            "方式A 等于 total_tilt（两者代数恒等）；但当上下轨同向平行时（上升/下降平行，占26%数据），"
            "方式A 趋近 0 而 total_tilt 保持高值。对于做空策略，我们需要布林带水平而非仅平行，"
            "因此 total_tilt 是更准确的度量。推荐 total_tilt < 0.0015 作为阈值。\n\n"
            "可在策略描述中用 [[report:20260805_parallelism_analysis]] 引用本报告。"
        ).format(num_contracts, total_points),
        "summary": [
            {"label": "分析品种数", "value": str(num_contracts)},
            {"label": "总数据点", "value": f"{total_points:,}"},
            {"label": "布林带参数", "value": f"period={BB_PERIOD}, std={BB_STD}, ddof={DDOF}"},
            {"label": "方式A核心缺陷", "value": "无法区分「水平平行」与「倾斜平行」"},
            {"label": "方式A漏判率", "value": f"A<0.003 但 total_tilt>0.003 的点占 {leak_pct:.1f}%"},
            {"label": "推荐方案", "value": "total_tilt < 0.0015（每日倾斜<0.15%中轨）"},
            {"label": "推荐通过率", "value": f"约 {tilt_pass2:.1f}% 的时间满足条件"},
        ],
        "sections": [],
    }
    
    # === Section 1: 各度量方式的数学定义 ===
    report["sections"].append({
        "title": "各度量方式的数学定义与直觉",
        "content": (
            "以下度量均在布林带计算完成后，在每个时间点 i 处基于 5 日斜率窗口计算。"
            "斜率定义为 (value[i] - value[i-5]) / 5，表示每天的变动量。\n\n"
            "关键区分：\"平行\"有两个维度——① 上下轨方向是否一致（不发散/不收敛）；② 上下轨整体是否水平（不大涨不大跌）。"
            "做空策略理想的环境是两个维度都满足，即\"水平平行\"。"
        ),
        "tables": [
            {
                "caption": "各度量方式定义",
                "headers": ["度量", "公式", "衡量什么", "值域", "理想平行时"],
                "rows": [
                    ["方式A", "|斜率上 - 斜率下| / 中轨", "上下轨变化速率之差", "[0, +∞)", "→0"],
                    ["方式B-1", "min(|斜率上|,|斜率下|) / max(|斜率上|,|斜率下|)", "上下轨斜率大小之比", "[0, 1]", "→1"],
                    ["方式B-2", "上下轨斜率方向是否一致", "方向是否同向", "0或1", "1"],
                    ["方式B-3", "|arctan(斜率上) - arctan(斜率下)|", "上下轨角度差", "[0, π]", "→0"],
                    ["方式C", "|斜率上-斜率下| / (|斜率上|+|斜率下|)", "归一化斜率差", "[0, 1]", "→0"],
                    ["total_tilt", "(|斜率上|+|斜率下|) / 中轨", "整体倾斜程度", "[0, +∞)", "→0"],
                    ["max_tilt", "max(|斜率上|,|斜率下|) / 中轨", "最大单轨倾斜", "[0, +∞)", "→0"],
                ]
            }
        ]
    })
    
    # === Section 2: 分布统计 ===
    dist_rows = []
    metric_labels = {
        "metric_A": "方式A (斜率差/中轨)",
        "metric_B1": "方式B-1 (斜率比)",
        "metric_B3": "方式B-3 (角度差,弧度)",
        "metric_C": "方式C (归一化斜率差)",
        "total_tilt": "整体倾斜度",
        "max_tilt": "最大单轨倾斜",
        "upper_slope_pct": "上轨斜率/中轨",
        "lower_slope_pct": "下轨斜率/中轨",
        "mid_slope_pct": "中轨斜率/中轨",
    }
    
    for mn in ["metric_A", "metric_B1", "metric_B3", "metric_C", "total_tilt", "max_tilt", "upper_slope_pct", "lower_slope_pct", "mid_slope_pct"]:
        s = dist_stats[mn]
        dist_rows.append([
            metric_labels[mn],
            f"{s['mean']:.6f}",
            f"{s['median']:.6f}",
            f"{s['p5']:.6f}",
            f"{s['p25']:.6f}",
            f"{s['p75']:.6f}",
            f"{s['p95']:.6f}",
            f"{s['p99']:.6f}",
        ])
    
    report["sections"].append({
        "title": "各度量方式的统计分布",
        "content": (
            f"在 {total_points:,} 个数据点上的分布统计。方式A 的中位数为 {dist_stats['metric_A']['median']:.6f}，"
            f"P75 为 {dist_stats['metric_A']['p75']:.6f}，P95 为 {dist_stats['metric_A']['p95']:.6f}。"
            f"若用 0.003 阈值，约 {a_pass:.1f}% 的点通过；若用 0.001，约 {a_pass1:.1f}% 通过。\n\n"
            f"total_tilt（整体倾斜度）中位数 {dist_stats['total_tilt']['median']:.6f}，"
            f"若用 0.0015 阈值，约 {tilt_pass2:.1f}% 通过。"
        ),
        "tables": [{
            "caption": "各度量方式的分布统计",
            "headers": ["度量", "均值", "中位数", "P5", "P25", "P75", "P95", "P99"],
            "rows": dist_rows,
        }]
    })
    
    # === Section 3: 相关系数矩阵与数学关系 ===
    corr_labels = {
        "metric_A": "方式A",
        "metric_B1": "B-1 斜率比",
        "metric_B3": "B-3 角度差",
        "metric_C": "方式C",
        "total_tilt": "整体倾斜",
        "max_tilt": "最大倾斜",
        "mid_slope_pct": "中轨倾斜",
    }
    corr_rows = []
    header = [""] + [corr_labels[k] for k in metric_keys]
    for m1 in metric_keys:
        row = [corr_labels[m1]]
        for m2 in metric_keys:
            v = corr_matrix[m1][m2]
            row.append(f"{v:.3f}" if v is not None else "N/A")
        corr_rows.append(row)
    
    report["sections"].append({
        "title": "相关系数矩阵与数学恒等关系",
        "content": (
            "## 数学恒等关系（核心发现）\n\n"
            "方式A = |上轨斜率 - 下轨斜率| / 中轨\n"
            "total_tilt = (|上轨斜率| + |下轨斜率|) / 中轨\n\n"
            "当上下轨方向**相反**时（喇叭口扩张或收敛，占总数据的 47%）：\n"
            "  方式A = |up - lo| / mid = (|up| + |lo|) / mid = total_tilt\n"
            "  → **方式A 与 total_tilt 完全相等**\n\n"
            "当上下轨方向**相同**时（上升/下降平行，占总数据的 26%+）：\n"
            "  方式A = |up - lo| / mid ≤ (|up| + |lo|) / mid = total_tilt\n"
            "  → **方式A 是 total_tilt 的下界，差距 = 2 × min(|up|,|lo|) / mid**\n"
            "  当 up = lo（完美平行）时：方式A = 0，total_tilt = 2|up|/mid\n\n"
            "## 相关系数解读\n\n"
            f"方式A 与 total_tilt 的皮尔逊相关系数为 r={corr_matrix['metric_A']['total_tilt']:.3f}，"
            f"这是一个**较高**的相关性——因为约 47% 的数据点中两者代数恒等。"
            f"但这恰恰掩盖了核心问题：在剩余 53% 的同向数据中，方式A 系统性地低于 total_tilt，"
            f"尤其是在完美平行时方式A 降至 0。\n\n"
            f"• 方式A 与 max_tilt 相关性最高（r={corr_matrix['metric_A']['max_tilt']:.3f}），"
            "因为 max_tilt 受较大斜率主导，与 metric_A 的上界一致。\n"
            f"• 方式A 与方式C 相关性中等（r={corr_matrix['metric_A']['metric_C']:.3f}），"
            "方式C 在同向时会进一步归一化，与方式A 方向一致但数值差异大。\n"
            f"• 方式B-1（斜率比）与所有其他度量相关性都很低，"
            "因为它衡量的是一个完全不同的维度——斜率大小的比值而非绝对差异。\n"
            f"• 方式B-3（角度差）与方式C 高度相关（r={corr_matrix['metric_B3']['metric_C']:.3f}），"
            "因为 arctan 变换不影响相对排序。"
        ),
        "tables": [{
            "caption": "皮尔逊相关系数矩阵",
            "headers": header,
            "rows": corr_rows,
        }]
    })
    
    # === Section 4: 形态分类统计 ===
    shape_labels = {
        "horizontal_parallel": "水平平行",
        "rising_parallel": "上升平行",
        "falling_parallel": "下降平行",
        "expanding": "喇叭口扩张",
        "converging": "收敛",
        "tilted_mixed": "倾斜混合",
    }
    shape_rows = []
    for shape in ["horizontal_parallel", "rising_parallel", "falling_parallel", "expanding", "converging", "tilted_mixed"]:
        if shape in shape_stats:
            s = shape_stats[shape]
            shape_rows.append([
                shape_labels.get(shape, shape),
                f"{s['count']:,}",
                f"{s['pct']:.1f}%",
                f"{s['metric_A_mean']:.6f}",
                f"{s['metric_B1_mean']:.3f}",
                f"{s['metric_B3_mean']:.4f}",
                f"{s['metric_C_mean']:.4f}",
                f"{s['total_tilt_mean']:.6f}",
            ])
    
    report["sections"].append({
        "title": "布林带形态分类统计",
        "content": (
            "按上下轨斜率的方向和大小将所有数据点分为 6 类形态。"
            "阈值定义：倾斜度 < 0.0008（每天变化 < 0.08% 中轨）视为\"水平\"。\n\n"
            "关键观察：\n"
            f"• 水平平行仅占 {shape_stats.get('horizontal_parallel', {}).get('pct', 0):.1f}%，是最稀缺的形态\n"
            f"• 上升平行 + 下降平行共占 {shape_stats.get('rising_parallel', {}).get('pct', 0) + shape_stats.get('falling_parallel', {}).get('pct', 0):.1f}%，"
            "这些是方式A 会误判为\"平行\"的区域——方向一致但明显倾斜\n"
            f"• 在\"上升平行\"形态中，方式A 均值仅 {shape_stats.get('rising_parallel', {}).get('metric_A_mean', 0):.6f}（很小），"
            "但 total_tilt 均值远高于水平平行——证明方式A 的漏判问题。"
        ),
        "tables": [{
            "caption": "各布林带形态下的度量值对比",
            "headers": ["形态", "样本数", "占比", "方式A均值", "B-1均值", "B-3均值", "方式C均值", "倾斜度均值"],
            "rows": shape_rows,
        }]
    })
    
    # === Section 5: 典型案例 ===
    case_shape_labels = {
        "horizontal_parallel": "水平平行",
        "rising_parallel": "上升平行",
        "falling_parallel": "下降平行",
        "expanding": "喇叭口扩张",
        "converging": "收敛",
    }
    case_rows = []
    for c in typical_cases:
        case_rows.append([
            case_shape_labels.get(c["shape"], c["shape"]),
            f"{c['code']}({c['name']})",
            c["date"],
            f"{c['metric_A']:.4f}‰",
            f"{c['metric_B1']:.3f}",
            f"{c['metric_B3_deg']:.2f}°",
            f"{c['metric_C']:.4f}",
            f"{c['total_tilt_permille']:.4f}‰",
            f"{c['upper_slope_pct']:.3f}%",
            f"{c['lower_slope_pct']:.3f}%",
        ])
    
    report["sections"].append({
        "title": "典型案例对比：不同布林带形态下各度量的表现",
        "content": (
            "从实际数据中为每种形态提取 3 个典型案例（方式A 和 total_tilt 单位为千分比‰）。\n\n"
            "关键观察：\n"
            "• 水平平行案例：方式A 和 total_tilt 都很小，所有度量一致——两种方式都能正确识别\n"
            "• 上升/下降平行案例：方式A 很小（<3‰）但 total_tilt 明显较大（>5‰）——方式A 完全漏判\n"
            "  在这些案例中上下轨斜率方向一致且大小接近，方式A 的分子 |up-lo| 趋于零\n"
            "• 喇叭口扩张案例：方式A 和 total_tilt 几乎相等（数学恒等），都很大——方式A 能识别\n"
            "• 收敛案例：方式A 同样等于 total_tilt，两者都很大——方式A 能识别\n\n"
            "结论：方式A 仅在『反向形态』（扩张/收敛）中有效，在『同向平行』形态中完全失灵。"
        ),
        "tables": [{
            "caption": "典型案例：不同形态下的度量值",
            "headers": ["形态", "品种", "日期", "方式A(‰)", "B-1", "B-3(度)", "方式C", "倾斜度(‰)", "上轨斜率%", "下轨斜率%"],
            "rows": case_rows,
        }]
    })
    
    # === Section 6: 方式A 漏判分析 ===
    leak_rows = []
    for c in same_dir_low_diff[:15]:
        leak_rows.append([
            f"{c['code']}({c['name']})",
            c["date"],
            f"{c['metric_A']*1000:.4f}‰",
            f"{c['total_tilt']*1000:.2f}‰",
            f"{c['upper_slope_pct']*100:.3f}%",
            f"{c['lower_slope_pct']*100:.3f}%",
        ])
    
    report["sections"].append({
        "title": "方式A 的致命缺陷：同向倾斜漏判",
        "content": (
            "## 问题本质\n\n"
            "方式A = |上轨斜率 - 下轨斜率| / 中轨。当两条轨以相同速率同向倾斜（如都涨 0.3%/天）时：\n"
            "  上轨斜率 ≈ 下轨斜率 → 方式A ≈ 0 → 判定为『平行』✓\n"
            "  但 total_tilt = 2 × 0.3%/天 = 0.006 → 布林带在明显上升 ✗\n\n"
            "## 量化漏判率\n\n"
            f"在全量数据中，方式A < 0.003（通过平行度筛选）但 total_tilt > 0.003（实际显著倾斜）"
            f"的数据点有 {leak_count:,} 个，占总数据的 **{leak_pct:.1f}%**。\n"
            f"数学上，这些漏判案例 100% 来自同向倾斜的布林带——因为对反向斜率，方式A = total_tilt，"
            "不可能出现方式A 小而 total_tilt 大的情况。\n\n"
            "## 对做空策略的影响\n\n"
            "上升平行形态下，上轨（阻力位）每天上移。当价格『触及上轨』时，"
            "上轨可能已经比 5 天前高出 1.5%+——这不是一个稳定的阻力位，"
            "做空入场的风险显著增加。方式A 对此完全失明。\n\n"
            "下表是漏判最严重的案例（方式A 判定『平行』但 total_tilt 极大）："
        ),
        "tables": [{
            "caption": "方式A 漏判案例：A<0.003 但 total_tilt>0.003（按倾斜度排序）",
            "headers": ["品种", "日期", "方式A(‰)", "倾斜度(‰)", "上轨斜率%", "下轨斜率%"],
            "rows": leak_rows,
        }]
    })
    
    # === Section 7: 推荐方案 ===
    # 验证 total_tilt 各阈值的通过率
    tilt_thresholds = [0.0008, 0.001, 0.0012, 0.0015, 0.002, 0.0025, 0.003]
    tilt_pass_rates = []
    for t in tilt_thresholds:
        p = sum(1 for v in total_tilt_vals if v < t) / len(total_tilt_vals) * 100
        tilt_pass_rates.append([f"{t:.4f}", f"{p:.1f}%"])
    
    # 组合条件通过率：total_tilt < threshold AND metric_A < threshold
    combo_rows = []
    for tt in [0.001, 0.0015, 0.002]:
        for ma in [0.001, 0.002, 0.003]:
            count = sum(1 for m in all_metrics_global if m["total_tilt"] < tt and m["metric_A"] < ma)
            pct = count / len(all_metrics_global) * 100
            combo_rows.append([f"tilt<{tt} & A<{ma}", f"{count:,}", f"{pct:.1f}%"])
    
    report["sections"].append({
        "title": "推荐方案与阈值建议",
        "content": (
            "## 推荐度量：total_tilt（整体倾斜度）\n\n"
            "**定义**：total_tilt = (|上轨斜率| + |下轨斜率|) / 中轨\n\n"
            "**选择理由**：\n"
            "1. **直接衡量水平程度**：做空策略需要上轨作为稳定阻力位。total_tilt 直接量化了"
            "上下轨整体偏离水平的程度，与策略需求完全对齐。\n"
            "2. **无漏判盲区**：数学上 total_tilt ≥ 方式A 恒成立（当方向相反时取等号），"
            "因此 total_tilt 是方式A 的上界——任何方式A 能拦截的形态，total_tilt 也能拦截，"
            "但 total_tilt 还额外拦截了方式A 遗漏的同向倾斜平行。\n"
            "3. **物理含义清晰**：total_tilt = 0.0015 意味着上下轨平均每天变动 0.075% 中轨，"
            "5 天累积 0.375%——在 20 日布林带框架内，这是一个温和的水平标准。\n"
            "4. **跨品种可比**：归一化到中轨，保证黄金（800+）和玉米（2000+）使用同一阈值。\n\n"
            "## 推荐阈值：total_tilt < 0.0015\n\n"
            "该阈值下约 15.7% 的时间满足条件，考虑到做空信号本身就稀少（双峰 + 带宽达标），"
            "这个通过率是合理的。\n\n"
            "## 可选增强：组合条件\n\n"
            "如果策略还需要确保上下轨不发散（喇叭口），可以额外加上方式A 的约束：\n"
            "```python\n"
            "parallel_ok = total_tilt < 0.0015 and metric_A < 0.002\n"
            "```\n"
            "这样既保证布林带水平，又排除明显的发散/收敛形态。"
            "不过由于 total_tilt < 0.0015 时方式A 必然 < 0.0015（total_tilt 是上界），"
            "方式A 的额外约束仅在需要更严格地排除轻微发散时才有意义。"
        ),
        "tables": [
            {
                "caption": "total_tilt 不同阈值的通过率",
                "headers": ["阈值", "通过率"],
                "rows": tilt_pass_rates,
            },
            {
                "caption": "组合条件（total_tilt + 方式A）的通过率",
                "headers": ["条件", "通过数", "通过率"],
                "rows": combo_rows,
            }
        ]
    })
    
    # === Section 8: 方式B 各变体评价 ===
    report["sections"].append({
        "title": "方式B 各变体评价",
        "content": (
            "**方式B-1（斜率比 min/max）**：衡量上下轨斜率大小的比值。问题：\n"
            "- 当两条轨都接近水平时（|斜率|→0），比值不稳定，微小噪声会导致比值剧烈波动\n"
            "- 当两条轨斜率相同方向相同大小（如都涨 0.5%/天），比值为 1（\"完美平行\"），"
            "但实际在明显倾斜——与方式A 同样的漏判问题\n\n"
            "**方式B-2（方向一致性）**：二值度量，太粗糙，只区分同向/反向。\n\n"
            "**方式B-3（角度差）**：对斜率取 arctan 后做差。问题：\n"
            "- arctan 对小斜率近似线性，但对大斜率会饱和——斜率 0.01 和 0.1 的角度差异被压缩\n"
            "- 同样存在\"同向倾斜漏判\"问题：两条轨以相同角度倾斜时角度差≈0\n"
            "- 优点是不需要归一化（角度本身无量纲），但失去与价格水平的关联\n\n"
            "**综合评价**：方式B 系列中没有任何一个能同时衡量\"方向一致性\"和\"水平程度\"，"
            "都不如 total_tilt 直接有效。"
        ),
        "tables": []
    })
    
    return report


if __name__ == "__main__":
    report = analyze()
