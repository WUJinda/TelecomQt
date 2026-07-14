# -*- coding: utf-8 -*-
"""
gen_mock_chart.py — 为样例 experiment.json 生成「合成示意」chart 字段。

⚠ 合成示意数据：K 线按真实开/平仓价反推走势，布林带用 rolling(20) 真实计算。
  仅用于验证前端「逐笔复盘」视图的呈现效果。
  真实 chart 数据需由 LaiCai 回测引擎导出（见 ../../../laicai-bridge/build_chart.py）。

用法：
    python gen_mock_chart.py
作用：
    读同目录上层 <exp_id>/experiment.json，给每笔有交易的 trade 塞入 chart 字段，写回。
依赖：仅标准库（random / math）。
"""
import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)

EXPERIMENTS_DIR = Path(__file__).resolve().parents[1]
BB_PERIOD, BB_STD = 20, 2.0


def trading_dates(start, n):
    """从 start 起 n 个工作日（跳过周末），贴近日线节奏。"""
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def rolling_mean_std(a, w=BB_PERIOD):
    """min_periods=1 风格的 rolling 均值/标准差（前 w-1 根用可用数据，示意可接受）。"""
    n = len(a)
    mean = [0.0] * n
    std = [0.0] * n
    for i in range(n):
        seg = a[max(0, i - w + 1):i + 1]
        m = sum(seg) / len(seg)
        var = sum((x - m) ** 2 for x in seg) / len(seg)
        mean[i] = m
        std[i] = math.sqrt(var)
    return mean, std


def linterp(pts, n):
    """pts: {index: value}，返回长度 n 的线性插值列表。"""
    ks = sorted(pts)
    out = [0.0] * n
    for i in range(n):
        if i <= ks[0]:
            out[i] = pts[ks[0]]
        elif i >= ks[-1]:
            out[i] = pts[ks[-1]]
        else:
            for j in range(len(ks) - 1):
                if ks[j] <= i <= ks[j + 1]:
                    x0, x1 = ks[j], ks[j + 1]
                    y0, y1 = pts[x0], pts[x1]
                    out[i] = y0 + (y1 - y0) * (i - x0) / (x1 - x0)
                    break
    return out


def build_closes(pre, holding, post, open_p, close_p, h_left, direction):
    """构造窗口 close 序列：分段线性插值 + 微噪声，关键点强制精确。"""
    n = pre + holding + post
    oi, ci, hi = pre, pre + holding - 1, pre - 3   # 开仓/平仓/左峰索引
    pts = {
        0: open_p * (0.965 if direction == "short" else 1.035),
        hi: h_left,
        oi: open_p,
        ci: close_p,
        n - 1: close_p * (0.975 if direction == "short" else 1.025),
    }
    raw = linterp(pts, n)
    raw = [v + v * random.uniform(-0.004, 0.004) for v in raw]
    raw[oi], raw[ci], raw[hi] = open_p, close_p, h_left
    return [round(v, 2) for v in raw], oi, ci, hi


def closes_to_klines(closes, dates):
    kls = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c * 0.998
        hi = max(o, c) * (1 + abs(random.gauss(0, 0.003)))
        lo = min(o, c) * (1 - abs(random.gauss(0, 0.003)))
        kls.append({
            "date": dates[i].strftime("%Y-%m-%d"),
            "open": round(o, 2), "high": round(hi, 2),
            "low": round(lo, 2), "close": round(c, 2),
            "volume": int(random.uniform(40000, 140000)),
        })
    return kls


def make_chart(trade, instrument, direction):
    open_p = float(trade["open_price"])
    close_p = float(trade["close_price"])
    holding = int(trade["holding_days"])
    pre, post = 20, 5
    h_left = round(open_p * (1.024 if direction == "short" else 0.976), 2)

    closes, oi, ci, hi = build_closes(pre, holding, post, open_p, close_p, h_left, direction)
    n = len(closes)
    open_date = datetime.strptime(trade["open_date"], "%Y-%m-%d")
    start = open_date - timedelta(days=pre + 8)   # 预留周末
    dates = trading_dates(start, n)

    klines = closes_to_klines(closes, dates)
    mid, sd = rolling_mean_std(closes)
    upper = [mid[i] + BB_STD * sd[i] for i in range(n)]
    lower = [mid[i] - BB_STD * sd[i] for i in range(n)]
    bw = [(upper[i] - lower[i]) / mid[i] if mid[i] > 0 else 0.0 for i in range(n)]
    for i, k in enumerate(klines):
        k["bb_upper"] = round(upper[i], 2)
        k["bb_mid"] = round(mid[i], 2)
        k["bb_lower"] = round(lower[i], 2)
        k["bw"] = round(bw[i], 4)

    bb_mid_o = mid[oi]
    bb_low_o = lower[oi]
    trigger = round((bb_mid_o + bb_low_o) / 2, 2)          # 中下轨中间位
    take_profit = round(bb_mid_o, 2)                        # 止盈 = 开仓时中轨
    bw_o = bw[oi]
    bw_pct = int(round(sum(1 for b in bw if b <= bw_o) / len(bw) * 100))

    summary = (
        f"{instrument} 日线 · {'做空' if direction == 'short' else '做多'} {trade['volume']} 手 · "
        f"{trade['open_date']} @{open_p} 开仓 → {trade['close_date']} @{close_p} 平仓 · "
        f"{'+' if trade['net_pnl'] >= 0 else ''}{trade['net_pnl']} ({trade['return_rate']}%) · "
        f"持仓 {holding} 天 · 开仓时带宽分位 {bw_pct}% · "
        f"触发：价格回到左峰 H_left @{h_left} 区间后跌破中下轨中间位 @{trigger}"
    )

    return {
        "window": {"pre": pre, "post": post},
        "klines": klines,
        "markers": {
            "open": {"date": klines[oi]["date"], "price": open_p},
            "close": {"date": klines[ci]["date"], "price": close_p},
            "h_left": {"date": klines[hi]["date"], "price": h_left},
            "trigger_line": trigger,
            "take_profit": take_profit,
            "no_stop_loss": True,
        },
        "bw_pct_at_open": bw_pct,
        "summary": summary,
    }


def main():
    target = next(EXPERIMENTS_DIR.glob("*/experiment.json"), None)
    if not target:
        raise SystemExit("未找到 experiment.json")
    exp = json.loads(target.read_text(encoding="utf-8"))
    direction = exp.get("direction", "short")
    for inst in exp["instruments"]:
        for t in inst.get("trades", []):
            t["chart"] = make_chart(t, inst["instrument"], direction)
            print(f"  + {inst['instrument']} #{t.get('no', 1)}: {len(t['chart']['klines'])} 根K线")
    target.write_text(json.dumps(exp, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写回: {target}")


if __name__ == "__main__":
    main()
