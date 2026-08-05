# -*- coding: utf-8 -*-
"""
双峰左侧做空策略 — 独立回测引擎

策略逻辑：
  1. 计算布林带(bb_period, bb_std)，带宽 bandwidth = (upper - lower) / middle
  2. 扩张期(Phase 1)：bandwidth >= bandwidth_min 期间，追踪最高价 high 作为候选 H_left
  3. 收缩期(Phase 2)：bandwidth < bandwidth_min，扩张期结束，H_left 固定为此前最高点
  4. 入场(Phase 3)：价格反弹回到 H_left 区域 [H_left*zone_lower, H_left*zone_upper] → 做空
  5. 平仓：价格回到布林中轨止盈（无止损）

设计参考：_batch_backtest.py 的布林带突破做空引擎、build_chart.py 的策略参数、
        experiment.json 中实际交易数据的逆向验证。

与 emit_experiment.py / build_chart.py / fetch_kline.py 配合使用：
    from double_top_backtest import run_all
    from build_chart import attach_charts
    from emit_experiment import emit_experiment

    all_results = run_all(data_dir, DEFAULT_PARAMS)
    for r in all_results:
        attach_charts(r, DEFAULT_PARAMS)
    emit_experiment(instruments=all_results, ...)
"""

import json
import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ============================================================
# 默认参数（与 experiment.json baseline 一致）
# ============================================================
DEFAULT_PARAMS = {
    "bb_period": 20,
    "bb_std": 2.0,
    "bandwidth_min": 0.14,        # 布林带带宽最低要求（扩张期阈值）
    "tilt_threshold": 0.008,      # 布林带平行度阈值 total_tilt < 此值时判定为水平
    "slope_window": 5,            # 斜率计算窗口（天）
    "left_peak_lookback": 30,     # 左峰最大回溯周期（安全阀，防止过老的峰）
    "zone_lower": 0.99,           # 左峰区域下界 = H_left * zone_lower
    "zone_upper": 1.02,           # 左峰区域上界 = H_left * zone_upper
    "bb_ddof": 1,                 # 标准差自由度（1=样本标准差，与 pandas 默认一致）
    "fee_rate": 0.0001,           # 手续费率（双边各收一次）
}

# 品种保证金率（各交易所实际标准；用于资金管理的手数计算）
MARGIN_RATES = {
    # 上期所 SHFE
    "au": 0.10, "ag": 0.12, "cu": 0.13, "al": 0.13, "zn": 0.13,
    "rb": 0.13, "hc": 0.13, "ni": 0.15, "sn": 0.15, "bu": 0.14, "ru": 0.14,
    # 大商所 DCE
    "i": 0.13, "m": 0.12, "y": 0.12, "p": 0.12, "a": 0.12, "c": 0.12, "cs": 0.12,
    # 郑商所 CZCE
    "SR": 0.12, "CF": 0.10, "RM": 0.10, "MA": 0.12, "TA": 0.10, "FG": 0.13, "SA": 0.13,
    # 中金所 CFFEX
    "IC": 0.14, "IF": 0.14, "IH": 0.14, "IM": 0.14,
    "T": 0.02, "TF": 0.02, "TS": 0.02,
}

# 资金管理（与 _batch_backtest.py 一致）
TOTAL_CAPITAL = 10_000_000
MAX_PER_TRADE = 1_000_000
MAX_TOTAL_EXPOSURE = 6_000_000

# 合约乘数表
MULTIPLIERS = {
    "rb": 10, "hc": 10, "cu": 5, "al": 5, "zn": 5,
    "ni": 1, "au": 1000, "ag": 15, "bu": 10, "ru": 10, "sn": 1,
    "i": 100, "m": 10, "y": 10, "p": 10, "a": 10, "c": 10, "cs": 10,
    "SR": 10, "CF": 5, "RM": 10, "MA": 10, "TA": 5, "FG": 20, "SA": 20,
    "IC": 200, "IF": 300, "IH": 300, "IM": 200,
    "T": 10000, "TF": 10000, "TS": 20000,
}


def get_multiplier(instrument_id: str) -> int:
    for prefix, mult in MULTIPLIERS.items():
        if instrument_id.upper().startswith(prefix.upper()):
            return mult
    return 10


def get_margin_rate(instrument_id: str) -> float:
    """返回品种的保证金率。"""
    for prefix, rate in MARGIN_RATES.items():
        if instrument_id.upper().startswith(prefix.upper()):
            return rate
    return 0.13  # 默认 13%


def calc_bbands(close_array, period=20, std_dev=2.0, ddof=1):
    close = pd.Series(close_array.astype(float))
    middle = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=ddof)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    bandwidth = np.where(middle > 0, (upper - lower) / middle, 0)
    return upper.values, middle.values, lower.values, bandwidth


@dataclass
class Trade:
    """一笔完整交易（开仓→平仓）。"""
    open_idx: int
    open_date: object
    open_price: float
    volume: int
    h_left: float
    h_left_idx: int
    close_idx: int = None
    close_date: object = None
    close_price: float = None

    def close(self, close_idx, close_date, close_price):
        self.close_idx = close_idx
        self.close_date = close_date
        self.close_price = close_price


def run_single_backtest(df, params):
    """对单个 DataFrame 运行双峰做空回测。

    返回 (trades, bbands_data)。
    trades: list[Trade]（已平仓的交易）
    bbands_data: dict(upper, middle, lower, bandwidth)
    """
    bb_period = params["bb_period"]
    bb_std = params["bb_std"]
    volume_multiple = params["volume_multiple"]
    margin_rate = params.get("margin_rate", 0.13)
    bandwidth_min = params["bandwidth_min"]
    tilt_threshold = params.get("tilt_threshold", 0.008)
    slope_window = params.get("slope_window", 5)
    left_peak_lookback = params["left_peak_lookback"]
    zone_lower = params["zone_lower"]
    zone_upper = params["zone_upper"]

    upper, middle, lower, bandwidth = calc_bbands(
        df["close"].values, bb_period, bb_std, ddof=params.get("bb_ddof", 1)
    )

    trades = []
    open_trade = None

    # 双峰做空三阶段状态机：
    #   bw_qualified → mid_touched → 入场 → 平仓 → 回到初始
    #
    #   1) 联合门控(bw >= bandwidth_min AND total_tilt < tilt_threshold)：标记有资格交易
    #      total_tilt = (|上轨斜率| + |下轨斜率|) / 中轨，衡量布林带水平程度
    #   2) 价格回落接触中轨(close <= middle)：触发扫描，往左找 H_left
    #   3) 价格反弹到 H_left zone：做空入场（第二峰 ≈ 左峰 = 双顶）
    #   4) 价格跌回中轨：平仓止盈
    bw_qualified = False
    mid_touched = False
    h_left = None
    h_left_idx = None

    close_vals = df["close"].values
    high_vals = df["high"].values

    for i in range(bb_period, len(df)):
        bw = bandwidth[i]
        if np.isnan(bw):
            continue

        # ---- 阶段1：联合门控（带宽 + 平行度）----
        # total_tilt = (|上轨斜率| + |下轨斜率|) / 中轨
        # 只有带宽达标且布林带水平（不倾斜）时才进入双峰扫描
        if i >= slope_window + bb_period:
            up_slope = (upper[i] - upper[i - slope_window]) / slope_window
            lo_slope = (lower[i] - lower[i - slope_window]) / slope_window
            mid_val = middle[i]
            total_tilt = (abs(up_slope) + abs(lo_slope)) / mid_val if mid_val > 0 else 999
        else:
            total_tilt = 999

        if bw >= bandwidth_min and total_tilt < tilt_threshold:
            bw_qualified = True

        # ---- 阶段2：价格接触中轨 → 触发扫描，锁定 H_left ----
        if bw_qualified and not mid_touched and open_trade is None:
            if close_vals[i] <= middle[i]:
                mid_touched = True
                lookback_start = max(0, i - left_peak_lookback)
                seg = high_vals[lookback_start:i]
                if len(seg) > 0:
                    h_left = float(seg.max())
                    h_left_idx = int(lookback_start + seg.argmax())

        # ---- 平仓检查（优先于开仓）----
        if open_trade is not None and close_vals[i] <= middle[i]:
            open_trade.close(i, df["date"].iloc[i], close_vals[i])
            trades.append(open_trade)
            open_trade = None

        # ---- 阶段3：价格反弹到 H_left zone → 做空入场 ----
        if open_trade is None and mid_touched and h_left is not None:
            # 左峰过期检查：距锁定时已超过 lookback 窗口 → 作废，重新走状态机
            if i - h_left_idx > left_peak_lookback:
                mid_touched = False
                h_left = None
                h_left_idx = None
            else:
                zone_lo = h_left * zone_lower
                zone_hi = h_left * zone_upper
                if zone_lo <= close_vals[i] <= zone_hi:
                    price = close_vals[i]
                    # 保证金口径：每手保证金 = price * multiplier * margin_rate
                    margin_per_lot = price * volume_multiple * margin_rate
                    max_by_trade = int(MAX_PER_TRADE // margin_per_lot) if margin_per_lot > 0 else 0
                    max_by_total = int(MAX_TOTAL_EXPOSURE // margin_per_lot) if margin_per_lot > 0 else 0
                    volume = min(max_by_trade, max_by_total)

                    if volume > 0:
                        open_trade = Trade(
                            open_idx=i,
                            open_date=df["date"].iloc[i],
                            open_price=price,
                            volume=volume,
                            h_left=h_left,
                            h_left_idx=h_left_idx,
                        )
                        # 交易后全部重置，需重新走完三阶段才能再次入场
                        bw_qualified = False
                        mid_touched = False
                        h_left = None
                        h_left_idx = None

    bbands_data = {"upper": upper, "middle": middle, "lower": lower, "bandwidth": bandwidth}
    return trades, bbands_data


def calc_trade_pnl(trades, volume_multiple, fee_rate, margin_rate=0.13):
    """计算交易盈亏明细（含 return_rate）。"""
    results = []
    for i, t in enumerate(trades):
        points = t.open_price - t.close_price
        gross = points * t.volume * volume_multiple
        fee = (t.open_price + t.close_price) * t.volume * volume_multiple * fee_rate
        net = gross - fee
        margin = t.open_price * t.volume * volume_multiple * margin_rate
        holding_days = (pd.Timestamp(t.close_date) - pd.Timestamp(t.open_date)).days
        return_rate = round(points / t.open_price * 100, 1) if t.open_price else 0.0

        results.append({
            "no": i + 1,
            "open_date": pd.Timestamp(t.open_date).strftime("%Y-%m-%d"),
            "open_price": t.open_price,
            "close_date": pd.Timestamp(t.close_date).strftime("%Y-%m-%d"),
            "close_price": t.close_price,
            "volume": t.volume,
            "holding_days": holding_days,
            "margin": round(margin, 2),
            "points": round(points, 2),
            "fee": round(fee, 2),
            "net_pnl": round(net, 2),
            "return_rate": return_rate,
            "win": bool(net > 0),
            # build_chart.py 需要的额外字段
            "records_raw": None,    # 由 backtest_one / run_all 填充
            "trades_raw": None,     # ← Trade 对象列表，供 attach_charts 使用
        })
    return results


def backtest_one(filepath, params):
    """对单个 _kline.json 文件运行回测，返回单品种结果 dict。

    结果结构与 _batch_backtest.run_all 的输出一致，额外包含 records_raw / trades_raw
    供 build_chart.attach_charts 使用。
    """
    with open(filepath, "r", encoding="utf-8") as f:
        raw = json.load(f)

    records = raw.get("data", [])
    if len(records) < params["bb_period"] + 5:
        return None

    instrument = raw.get("instrument", os.path.basename(filepath).replace("_kline.json", ""))
    exchange = raw.get("exchange", "?")
    kline_style = raw.get("kline_style", "?")

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    vm = get_multiplier(instrument)
    mr = get_margin_rate(instrument)
    params_with_vm = {**params, "volume_multiple": vm, "margin_rate": mr}

    trades, bbands = run_single_backtest(df, params_with_vm)
    trade_details = calc_trade_pnl(trades, vm, params["fee_rate"], margin_rate=mr)

    # 填充 records_raw 和 trades_raw 到 result 级别（供 attach_charts 使用）
    for td, tr in zip(trade_details, trades):
        td.pop("records_raw", None)
        td.pop("trades_raw", None)

    result = {
        "instrument": instrument,
        "exchange": exchange,
        "kline_style": kline_style,
        "records": len(df),
        "date_start": df["date"].iloc[0].strftime("%Y-%m-%d"),
        "date_end": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "volume_multiple": vm,
        "trade_count": len(trade_details),
        "trades": trade_details,
        # 供 build_chart.attach_charts 使用（导出后由 clean=True 自动删除）
        "records_raw": records,
        "trades_raw": trades,
    }

    if trade_details:
        pnls = [t["net_pnl"] for t in trade_details]
        wins = sum(1 for t in trade_details if t["win"])
        result["total_pnl"] = round(sum(pnls), 2)
        result["win_count"] = wins
        result["loss_count"] = len(trade_details) - wins
        result["win_rate"] = round(wins / len(trade_details) * 100, 1)
        result["max_win"] = round(max(pnls), 2)
        result["max_loss"] = round(min(pnls), 2)
        result["avg_holding_days"] = round(
            sum(t["holding_days"] for t in trade_details) / len(trade_details), 1
        )
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        drawdown = cumulative - peak
        result["max_drawdown"] = round(float(drawdown.min()), 2)

    return result


def apply_global_exposure_limit(results, max_total_exposure=MAX_TOTAL_EXPOSURE,
                                fee_rate=0.0001):
    """两遍扫描法 — 第二遍：跨品种总敞口控制。

    第一遍各品种独立回测（已完成），第二遍按开仓时间排序，逐笔检查重叠持仓。
    如果新开仓位与已有持仓的保证金之和超过 max_total_exposure，则削减新手数；
    削减后手数 ≤ 0 则跳过该笔交易。

    原则：先开的仓位优先占额度（模拟实盘 FIFO）。
    """
    from datetime import datetime as _dt

    # ---- 收集所有交易，附上品种级元数据 ----
    all_trades = []
    for inst_idx, inst in enumerate(results):
        vm = inst["volume_multiple"]
        mr = get_margin_rate(inst["instrument"])
        for ti, t in enumerate(inst["trades"]):
            all_trades.append({
                "inst_idx": inst_idx, "trade_idx": ti,
                "instrument": inst["instrument"], "vm": vm, "mr": mr,
                "open_date": _dt.strptime(t["open_date"], "%Y-%m-%d"),
                "close_date": _dt.strptime(t["close_date"], "%Y-%m-%d"),
                "open_price": t["open_price"],
                "close_price": t["close_price"],
                "orig_volume": t["volume"],
            })

    all_trades.sort(key=lambda x: x["open_date"])

    # ---- 逐笔检查，维护当前持仓列表 ----
    open_positions = []          # [{close_date, current_margin}, ...]
    new_volumes = {}              # (inst_idx, trade_idx) -> 调整后手数

    for tr in all_trades:
        key = (tr["inst_idx"], tr["trade_idx"])

        # 清理已平仓的持仓（平仓日 ≤ 本笔开仓日 → 不再占用额度）
        open_positions = [p for p in open_positions if p["close_date"] > tr["open_date"]]

        used_margin = sum(p["current_margin"] for p in open_positions)
        margin_per_lot = tr["open_price"] * tr["vm"] * tr["mr"]
        available = max_total_exposure - used_margin

        if margin_per_lot <= 0 or available < margin_per_lot:
            new_vol = 0
        else:
            new_vol = min(tr["orig_volume"], int(available // margin_per_lot))

        new_volumes[key] = new_vol

        if new_vol > 0:
            open_positions.append({
                "close_date": tr["close_date"],
                "current_margin": new_vol * margin_per_lot,
            })

    # ---- 应用调整，重算盈亏 ----
    n_adjusted = 0
    n_skipped = 0

    for (inst_idx, ti), new_vol in new_volumes.items():
        inst = results[inst_idx]
        t = inst["trades"][ti]
        vm = inst["volume_multiple"]
        mr = get_margin_rate(inst["instrument"])

        if new_vol == 0:
            t["volume"] = 0
            t["margin"] = 0
            t["fee"] = 0
            t["net_pnl"] = 0
            t["return_rate"] = 0
            t["win"] = False
            n_skipped += 1
            continue

        if new_vol == t["volume"]:
            continue

        # 手数被削减，重算
        t["volume"] = new_vol
        points = t["open_price"] - t["close_price"]
        gross = points * new_vol * vm
        fee = (t["open_price"] + t["close_price"]) * new_vol * vm * fee_rate
        net = gross - fee
        margin = t["open_price"] * new_vol * vm * mr

        t["margin"] = round(margin, 2)
        t["fee"] = round(fee, 2)
        t["net_pnl"] = round(net, 2)
        t["return_rate"] = round(points / t["open_price"] * 100, 1) if t["open_price"] else 0.0
        t["win"] = bool(net > 0)
        n_adjusted += 1

    # ---- 清理被跳过的交易（volume=0），更新品种级汇总 ----
    for inst in results:
        inst["trades"] = [t for t in inst["trades"] if t.get("volume", 0) > 0]
        inst["trade_count"] = len(inst["trades"])

        for i, t in enumerate(inst["trades"]):
            t["no"] = i + 1

        if inst["trades"]:
            pnls = [t["net_pnl"] for t in inst["trades"]]
            wins = sum(1 for t in inst["trades"] if t["win"])
            inst["total_pnl"] = round(sum(pnls), 2)
            inst["win_count"] = wins
            inst["loss_count"] = len(inst["trades"]) - wins
            inst["win_rate"] = round(wins / len(inst["trades"]) * 100, 1)
            inst["max_win"] = round(max(pnls), 2)
            inst["max_loss"] = round(min(pnls), 2)
            inst["avg_holding_days"] = round(
                sum(t["holding_days"] for t in inst["trades"]) / len(inst["trades"]), 1
            )
            cumulative = np.cumsum(pnls)
            peak = np.maximum.accumulate(cumulative)
            inst["max_drawdown"] = round(float((cumulative - peak).min()), 2)
        else:
            for k in ("total_pnl", "win_count", "loss_count", "win_rate",
                      "max_win", "max_loss", "avg_holding_days", "max_drawdown"):
                inst.pop(k, None)

    if n_adjusted or n_skipped:
        print(f"[global_exposure] 削减 {n_adjusted} 笔手数, 跳过 {n_skipped} 笔 "
              f"(总敞口上限 {max_total_exposure:,.0f})")

    return results


def run_all(data_dir, params, enforce_global_exposure=True):
    """批量运行所有品种的回测。

    data_dir: 包含 *_kline.json 的目录
    params: 策略参数 dict
    enforce_global_exposure: 是否在第二遍扫描中强制执行跨品种总敞口上限
    返回: list[dict]，每个 dict 是一个品种的回测结果。
    """
    all_results = []

    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith("_kline.json"):
            continue

        filepath = os.path.join(data_dir, fname)
        result = backtest_one(filepath, params)
        if result is None:
            continue

        all_results.append(result)

    # ---- 第二遍：跨品种总敞口控制 ----
    if enforce_global_exposure:
        all_results = apply_global_exposure_limit(
            all_results,
            max_total_exposure=MAX_TOTAL_EXPOSURE,
            fee_rate=params.get("fee_rate", 0.0001),
        )

    return all_results


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="双峰左侧做空策略 — 批量回测")
    p.add_argument("--data", required=True, help="K线 JSON 所在目录")
    p.add_argument("--bw-min", type=float, default=0.15, help="带宽最低要求（默认 0.15）")
    p.add_argument("--zone-lower", type=float, default=0.99, help="左峰区域下界（默认 0.99）")
    p.add_argument("--zone-upper", type=float, default=1.02, help="左峰区域上界（默认 1.02）")
    p.add_argument("--bb-period", type=int, default=20, help="布林带周期（默认 20）")
    p.add_argument("--bb-std", type=float, default=2.0, help="标准差倍数（默认 2.0）")
    args = p.parse_args()

    params = {
        **DEFAULT_PARAMS,
        "bandwidth_min": args.bw_min,
        "zone_lower": args.zone_lower,
        "zone_upper": args.zone_upper,
        "bb_period": args.bb_period,
        "bb_std": args.bb_std,
    }

    results = run_all(args.data, params)

    print(f"\n{'='*70}")
    print(f"双峰左侧做空策略 — 回测报告")
    print(f"{'='*70}")
    print(f"参数: bb={params['bb_period']}/{params['bb_std']}, bw_min={params['bandwidth_min']}, "
          f"zone=[{params['zone_lower']}, {params['zone_upper']}]")

    for r in results:
        tc = r["trade_count"]
        pnl = r.get("total_pnl", 0)
        tag = f"+{pnl:,.0f}" if pnl >= 0 else f"{pnl:,.0f}"
        print(f"  {r['instrument']:8s}  {r['date_start']}~{r['date_end']}  "
              f"records={r['records']:3d}  trades={tc}  pnl={tag}")

    traded = [r for r in results if r["trade_count"] > 0]
    all_trades = [t for r in traded for t in r["trades"]]
    n = len(all_trades)
    if n:
        total_pnl = sum(t["net_pnl"] for t in all_trades)
        total_margin = sum(t["margin"] for t in all_trades)
        wins = sum(1 for t in all_trades if t["win"])
        print(f"\n汇总: {n} 笔交易, 胜率 {wins}/{n} ({wins/n*100:.0f}%), "
              f"总盈亏 {total_pnl:+,.0f}, 总保证金 {total_margin:,.0f}, "
              f"收益率 {total_pnl/total_margin*100:.1f}%")
    else:
        print("\n无交易信号。")
