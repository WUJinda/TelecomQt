# -*- coding: utf-8 -*-
"""
build_chart.py — 为 LaiCai 双峰做空回测的每笔交易生成 chart 字段
（K 线窗口 + 布林三轨 + 开平仓/左峰标注 + 触发位 + 止盈 + 带宽分位 + 文字总结）。

把本文件放到 LaiCai 的 pyStrategy/self_strategy/doubleTop/ 下（和 double_top_backtest.py 同级），
在回测拿到 all_results 后、调用 emit_experiment 前，加一行：

    from build_chart import attach_charts
    for r in all_results:
        attach_charts(r, params)        # 给每笔交易塞 chart，并清理 records_raw 等大字段

然后照常 emit_experiment(instruments=all_results, ...)，chart 会自动透传进 experiment.json。

依赖：numpy / pandas（LaiCai 环境本就有）。
字段约定：见面板仓库 docs/experiment-schema.md 的 trades[i].chart。
"""
import numpy as np
import pandas as pd


def _to_date_str(v):
    """numpy datetime64 / Timestamp / str → 'YYYY-MM-DD'。"""
    try:
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    except Exception:
        return str(v)[:10]


def _calc_bbands(close_vals, period=20, std_dev=2.0):
    """与 LaiCai calc_bbands 等价的布林带计算（自包含，不依赖 LaiCai 模块）。"""
    close = pd.Series(np.asarray(close_vals, dtype=float))
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    upper = mid + std_dev * sd
    lower = mid - std_dev * sd
    bw = np.where(mid > 0, (upper - lower) / mid, np.nan)
    return {"upper": upper.values, "middle": mid.values, "lower": lower.values, "bandwidth": bw}


def build_chart(df, bbands, trade, params, instrument="?", holding_days=0, pre=20, post=5):
    """为单笔交易生成 chart 字段。

    参数：
        df           回测用 DataFrame（含 date/open/high/low/close[/volume] 列）
        bbands       _calc_bbands / run_single_backtest 返回的 dict（upper/middle/lower/bandwidth）
        trade        Trade 对象（需 open_idx, close_idx, h_left, h_left_idx,
                     open_price, close_price, volume, open_date, close_date）
        params       策略参数（bb_period/bb_std 已固化进 bbands，此处仅占位）
        instrument   品种代码（写进 summary）
        holding_days 持仓天数（写进 summary）
        pre/post     开仓前 / 平仓后 K 线根数
    返回：chart 字典（结构见 experiment-schema.md）
    """
    upper, mid, lower, bw = bbands["upper"], bbands["middle"], bbands["lower"], bbands["bandwidth"]
    n = len(df)
    oi, ci = int(trade.open_idx), int(trade.close_idx)
    hi = int(getattr(trade, "h_left_idx", -1))

    # 窗口左边界：开仓前 pre 根；若左峰更早则扩到左峰
    left_candidates = [oi - pre] + ([hi] if hi >= 0 else [])
    ws = max(0, min(left_candidates))
    we = min(n - 1, ci + post)

    klines = []
    for idx in range(ws, we + 1):
        row = df.iloc[idx]
        vol = row.get("volume") if "volume" in df.columns else 0
        klines.append({
            "date": _to_date_str(row["date"]),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": int(vol) if not pd.isna(vol) else 0,
            "bb_upper": None if np.isnan(upper[idx]) else round(float(upper[idx]), 2),
            "bb_mid": None if np.isnan(mid[idx]) else round(float(mid[idx]), 2),
            "bb_lower": None if np.isnan(lower[idx]) else round(float(lower[idx]), 2),
            "bw": None if np.isnan(bw[idx]) else round(float(bw[idx]), 4),
        })

    # 开仓时的布林值 → 触发位（中下轨中间）/ 止盈（中轨）
    mid_o = float(mid[oi])
    lower_o = float(lower[oi])
    trigger = round((mid_o + lower_o) / 2, 2)
    take_profit = round(mid_o, 2)
    h_left_price = float(getattr(trade, "h_left", 0) or 0)

    # 开仓时带宽在全序列（非 nan 部分）的历史分位
    valid_bw = bw[~np.isnan(bw)]
    bw_pct = int(round(float((valid_bw <= bw[oi]).mean()) * 100)) if len(valid_bw) else 0

    summary = (
        f"{instrument} · 做空 {trade.volume} 手 · "
        f"{_to_date_str(trade.open_date)} @{trade.open_price} 开仓 → "
        f"{_to_date_str(trade.close_date)} @{trade.close_price} 平仓 · "
        f"持仓 {holding_days} 天 · 开仓时带宽分位 {bw_pct}% · "
        f"触发：带宽达标后跌破中下轨中间位 @{trigger}，回到左峰 H_left @{h_left_price} 区间开空"
    )

    return {
        "window": {"pre": pre, "post": post},
        "klines": klines,
        "markers": {
            "open": {"date": _to_date_str(trade.open_date), "price": round(float(trade.open_price), 2)},
            "close": {"date": _to_date_str(trade.close_date), "price": round(float(trade.close_price), 2)},
            "h_left": {"date": _to_date_str(df["date"].iloc[hi]) if 0 <= hi < n else None,
                       "price": round(h_left_price, 2)},
            "trigger_line": trigger,
            "take_profit": take_profit,
            "no_stop_loss": True,
        },
        "bw_pct_at_open": bw_pct,
        "summary": summary,
    }


def attach_charts(result, params, pre=20, post=5, clean=True):
    """给一个品种的回测结果 result 的每笔交易塞 chart 字段。

    要求 result 含 double_top_backtest.backtest_one() 的输出：
        records_raw（原始 K 线 list[dict]）、trades_raw（Trade 对象 list）、trades（盈亏 dict list）。
    流程：重建 DataFrame → 重算布林带 → 对每笔 trade 切窗口生成 chart → 塞进 result["trades"][i]["chart"]。

    clean=True 时完成后删除 records_raw / trades_raw / state_log 等大字段
    （chart 已提取所需信息，避免 experiment.json 体积过大）。
    """
    records = result.get("records_raw")
    trades_raw = result.get("trades_raw", [])
    trades = result.get("trades", [])
    if not records or not trades_raw or len(trades_raw) != len(trades):
        return False  # 无交易或结构不符，跳过

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    bbands = _calc_bbands(df["close"].values, params.get("bb_period", 20), params.get("bb_std", 2.0))

    instrument = result.get("instrument", "?")
    for td, tr in zip(trades, trades_raw):
        td["chart"] = build_chart(
            df, bbands, tr, params,
            instrument=instrument,
            holding_days=td.get("holding_days", 0),
            pre=pre, post=post,
        )

    if clean:
        for k in ("records_raw", "trades_raw", "state_log"):
            result.pop(k, None)
    return True


if __name__ == "__main__":
    # 自测：用 double_top_backtest 跑一个品种并打印 chart 摘要（演示用法）
    import sys
    import os
    import argparse

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from double_top_backtest import backtest_one, DEFAULT_PARAMS
    except ImportError:
        print("自测需要 double_top_backtest.py 同目录；跳过。")
        raise SystemExit(0)

    p = argparse.ArgumentParser(description="跑一个品种并打印 chart 摘要（演示）")
    p.add_argument("--data", required=True, help="单个 _kline.json 文件路径")
    args = p.parse_args()

    r = backtest_one(args.data, DEFAULT_PARAMS)
    if r and r["trade_count"] > 0:
        attach_charts(r, DEFAULT_PARAMS)
        for t in r["trades"]:
            c = t["chart"]
            print(f"{r['instrument']} #{t.get('no', 1)}: {len(c['klines'])} 根K线 | "
                  f"bw_pct={c['bw_pct_at_open']} trigger={c['markers']['trigger_line']} "
                  f"tp={c['markers']['take_profit']}")
            print("  " + c["summary"])
    else:
        print("该品种无交易。")
