# -*- coding: utf-8 -*-
"""
fetch_kline.py — 用真实期货日K替换 experiment.json 里的合成 chart 数据。

数据源：AKShare futures_zh_daily_sina（新浪财经期货日K，免费、无需 token）。
输出格式：与 laicai-bridge/build_chart.py 的 build_chart() 完全一致
         （klines OHLCV + 布林三轨/bandwidth + markers + bw_pct_at_open + summary），
         保证前端「逐笔复盘」视图零改动直接消费。

为何需要它：
  样例 experiment.json 的 chart 原本由 _tools/gen_mock_chart.py 按开/平仓价反推合成，
  K线走势是假的。本脚本从 AKShare 拉真实日K，重算布林带（口径与 build_chart 一致：
  period/std 从 experiment.params 读，ddof=1），替换 chart.klines/markers/summary。

用法：
    cd laicai-bridge
    python fetch_kline.py                       # 刷新默认 experiments 目录下所有报告
    python fetch_kline.py --exp ../backend/data/experiments/20260712_xxx
    python fetch_kline.py --symbol AG2606 --from 2025-10-01 --to 2025-12-01   # 单独预览某合约

依赖：akshare（pip install akshare）、numpy、pandas（akshare 自带）。
字段约定：见 docs/experiment-schema.md 的 trades[i].chart。
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import akshare as ak

# 复用 build_chart 的布林带计算与日期格式化，确保口径完全一致（单一真相源）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_chart import _calc_bbands, _to_date_str  # noqa: E402


# ---- AKShare 拉数 ----

def _instrument_to_symbol_candidates(instrument: str) -> list[str]:
    """ag2606 -> ['AG2606', 'AG606']：原样大写 + 去/补千年位两个候选。

    新浪期货代码格式不统一：上期所(AG/RB/AU)多用 4 位年月(AG2606)，
    郑商所(TA/MA/CF)历史上常用 3 位(TA609)。逐个尝试，命中即用。
    """
    m = re.match(r"^([a-zA-Z]+)\s*0*(\d{3,4})$", instrument.strip())
    if not m:
        return [instrument.upper()]
    letters, digits = m.group(1).upper(), m.group(2)
    cands = [letters + digits]
    if len(digits) == 4:
        cands.append(letters + digits[-3:])       # AG2606 -> AG606
    elif len(digits) == 3:
        cands.append(letters + "2" + digits)      # TA609  -> TA2609
    return cands


def fetch_klines(instrument: str) -> tuple[pd.DataFrame, str]:
    """拉单合约全量历史日K。

    返回 (标准化 DataFrame, 实际命中的 symbol)。
    DataFrame 列：date / open / high / low / close / volume（date 为 Timestamp）。
    """
    last_err = None
    for sym in _instrument_to_symbol_candidates(instrument):
        try:
            raw = ak.futures_zh_daily_sina(symbol=sym)
        except Exception as e:                     # noqa: BLE001
            last_err = (sym, repr(e)[:160])
            continue
        if raw is None or len(raw) == 0:
            last_err = (sym, RuntimeError("空数据"))
            continue
        df = raw.copy()
        df["date"] = pd.to_datetime(df["date"])
        for c in ("open", "high", "low", "close"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df.sort_values("date").reset_index(drop=True)
        return df[["date", "open", "high", "low", "close", "volume"]], sym
    raise RuntimeError(f"拉取 {instrument} 失败，候选 symbol 均失败：{last_err}")


# ---- chart 构建（输出与 build_chart.build_chart() 对齐）----

def _find_left_peak(df: pd.DataFrame, open_idx: int, lookback: int):
    """开仓前 lookback 根 K 线内的最高点（左峰近似）。

    真实回测里左峰由 LaiCai 策略按双峰形态识别；此处用「开仓前 N 日最高价」近似，
    仅供复盘标注参考，可能与策略实际识别的左峰有出入（OHLCV / 布林带本身是真实的）。
    """
    lo = max(0, open_idx - lookback)
    seg = df.iloc[lo:open_idx]                      # 不含开仓当日
    if len(seg) == 0:
        return None, None
    i = int(seg["high"].idxmax())                   # df 绝对索引
    return i, float(seg.loc[i, "high"])


def _locate_date(df: pd.DataFrame, date_str: str) -> int:
    """按 'YYYY-MM-DD' 定位行索引；找不到则取最近的一天。"""
    target = pd.Timestamp(date_str)
    exact = df.index[df["date"] == target]
    if len(exact):
        return int(exact[0])
    return int((df["date"] - target).abs().idxmin())


def build_chart_from_klines(df, trade, params, instrument, direction, pre=20, post=5):
    """用真实 K 线 df 生成单笔 trade 的 chart 字段。

    输出结构与 laicai-bridge/build_chart.py::build_chart() 完全一致。
    """
    bb = _calc_bbands(
        df["close"].values,
        period=params.get("bb_period", 20),
        std_dev=params.get("bb_std", 2.0),
        ddof=params.get("bb_ddof", 1),
    )
    upper, mid, lower, bw = bb["upper"], bb["middle"], bb["lower"], bb["bandwidth"]
    n = len(df)

    oi = _locate_date(df, trade["open_date"])
    ci = _locate_date(df, trade["close_date"])

    lookback = params.get("left_peak_lookback", 30)
    h_left_idx, h_left_price = _find_left_peak(df, oi, lookback)

    # 窗口左边界：开仓前 pre 根；若左峰更早则扩到左峰。右边界：平仓后 post 根。
    left_candidates = [oi - pre] + ([h_left_idx] if h_left_idx is not None else [])
    ws = max(0, min(left_candidates))
    we = min(n - 1, ci + post)

    klines = []
    for idx in range(ws, we + 1):
        row = df.iloc[idx]
        klines.append({
            "date": _to_date_str(row["date"]),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": int(row["volume"]),
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

    # 开仓时带宽在历史非 nan 部分的分位
    valid_bw = bw[~np.isnan(bw)]
    bw_pct = int(round(float((valid_bw <= bw[oi]).mean()) * 100)) if len(valid_bw) else 0

    verb = "做空" if direction == "short" else "做多"
    h_left_seg = f"，回到左峰 H_left @{h_left_price:.2f} 区间开空" if h_left_price is not None else ""
    summary = (
        f"{instrument} · {verb} {trade['volume']} 手 · "
        f"{trade['open_date']} @{trade['open_price']} 开仓 → "
        f"{trade['close_date']} @{trade['close_price']} 平仓 · "
        f"持仓 {trade['holding_days']} 天 · 开仓时带宽分位 {bw_pct}% · "
        f"触发：带宽达标后跌破中下轨中间位 @{trigger}{h_left_seg}"
    )

    return {
        "window": {"pre": pre, "post": post},
        "klines": klines,
        "markers": {
            "open": {"date": trade["open_date"], "price": round(float(trade["open_price"]), 2)},
            "close": {"date": trade["close_date"], "price": round(float(trade["close_price"]), 2)},
            "h_left": {
                "date": _to_date_str(df["date"].iloc[h_left_idx]) if h_left_idx is not None else None,
                "price": round(h_left_price, 2) if h_left_price is not None else None,
            },
            "trigger_line": trigger,
            "take_profit": take_profit,
            "no_stop_loss": True,
        },
        "bw_pct_at_open": bw_pct,
        "summary": summary,
    }


# ---- 刷新 experiment.json ----

def refresh_experiment(exp_dir: Path, pre=20, post=5, backup=True, verbose=True):
    """读 exp_dir/experiment.json，对每个有交易的 trade 用真实K线重算 chart，写回。

    backup=True 时首次运行另存 experiment.json.synth.bak（不覆盖已存在的备份）。
    """
    f = exp_dir / "experiment.json"
    exp = json.loads(f.read_text(encoding="utf-8"))
    params = exp.get("params", {})
    direction = exp.get("direction", "short")

    if backup:
        bak = f.with_suffix(".json.synth.bak")
        if not bak.exists():
            bak.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            if verbose:
                print(f"  备份合成数据 → {bak.name}")

    sym_cache: dict[str, pd.DataFrame | None] = {}
    for inst in exp.get("instruments", []):
        trades = inst.get("trades", [])
        if not trades:
            continue
        instrument = inst["instrument"]
        if instrument not in sym_cache:
            try:
                df, sym = fetch_klines(instrument)
                sym_cache[instrument] = df
                if verbose:
                    print(f"  {instrument} → symbol={sym}，{len(df)} 根日K"
                          f"（{df['date'].min().date()} ~ {df['date'].max().date()}）")
            except Exception as e:                 # noqa: BLE001
                print(f"  [跳过] {instrument} 拉取失败：{e}")
                sym_cache[instrument] = None
        df = sym_cache[instrument]
        if df is None:
            continue
        for t in trades:
            try:
                t["chart"] = build_chart_from_klines(df, t, params, instrument, direction, pre, post)
                c = t["chart"]
                if verbose:
                    print(f"    + #{t.get('no', 1)}: {len(c['klines'])} 根K线 | "
                          f"bw_pct={c['bw_pct_at_open']} trigger={c['markers']['trigger_line']} "
                          f"tp={c['markers']['take_profit']}")
            except Exception as e:                 # noqa: BLE001
                print(f"    [跳过] {instrument} #{t.get('no', '?')} chart 构建失败：{e}")

    f.write_text(json.dumps(exp, ensure_ascii=False, indent=2), encoding="utf-8")
    if verbose:
        print(f"  已写回：{f}")


# ---- CLI ----

DEFAULT_EXPERIMENTS_DIR = Path(__file__).resolve().parents[1] / "backend" / "data" / "experiments"


def main():
    p = argparse.ArgumentParser(
        description="用 AKShare 真实日K刷新 experiment.json 的 chart 字段（替换合成数据）")
    p.add_argument("--exp", help="单个 experiment 目录路径；缺省则刷新默认 experiments 目录下所有报告")
    p.add_argument("--pre", type=int, default=20, help="开仓前 K 线根数（默认 20）")
    p.add_argument("--post", type=int, default=5, help="平仓后 K 线根数（默认 5）")
    p.add_argument("--no-backup", action="store_true", help="不另存 .synth.bak 备份")
    p.add_argument("--symbol", help="仅预览：拉单个合约并打印 OHLCV（不写文件）")
    p.add_argument("--from", dest="frm", help="预览起始日期 YYYY-MM-DD")
    p.add_argument("--to", dest="to", help="预览结束日期 YYYY-MM-DD")
    args = p.parse_args()

    if args.symbol:
        df, sym = fetch_klines(args.symbol)
        print(f"命中 symbol={sym}，共 {len(df)} 行（{df['date'].min().date()} ~ {df['date'].max().date()}）")
        lo = pd.Timestamp(args.frm) if args.frm else df["date"].min()
        hi = pd.Timestamp(args.to) if args.to else df["date"].max()
        print(df[(df["date"] >= lo) & (df["date"] <= hi)].to_string(index=False))
        return

    if args.exp:
        ep = Path(args.exp)
        exp_dir = ep if ep.is_dir() else ep.parent
        targets = [exp_dir]
    else:
        targets = [f.parent for f in sorted(DEFAULT_EXPERIMENTS_DIR.glob("*/experiment.json"))]
    targets = [d for d in targets if (d / "experiment.json").exists()]
    if not targets:
        raise SystemExit(f"未找到 experiment.json：{args.exp or DEFAULT_EXPERIMENTS_DIR}")

    for d in targets:
        print(f"刷新 {d.name} …")
        refresh_experiment(d, pre=args.pre, post=args.post, backup=not args.no_backup)


if __name__ == "__main__":
    main()
