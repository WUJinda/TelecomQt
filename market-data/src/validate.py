# -*- coding: utf-8 -*-
"""数据质量校验（阶段1 日K部分；阶段4 补全）。"""
from __future__ import annotations

import config
from src.io_parquet import read_daily


def validate_daily(symbol: str) -> dict:
    """校验日K完整性。返回 {symbol, rows, date_start/end, errors[], warnings[]}。"""
    df = read_daily(symbol)
    hit = str(df["symbol"].iloc[0]) if (not df.empty and "symbol" in df.columns) else symbol
    meta = config.parse_symbol(hit)
    report: dict = {
        "symbol": hit,
        "name": meta["name"],
        "exchange": meta["exchange"],
        "multiplier": meta["multiplier"],
        "rows": len(df),
        "errors": [],
        "warnings": [],
    }
    if df.empty:
        report["errors"].append("无数据")
        return report

    report["date_start"] = str(df["date"].min().date())
    report["date_end"] = str(df["date"].max().date())

    if not df["date"].is_monotonic_increasing:
        report["errors"].append("date 非单调递增")

    dups = int(df["date"].duplicated().sum())
    if dups:
        report["errors"].append(f"{dups} 个重复交易日")

    for c in ("open", "high", "low", "close"):
        if (df[c] <= 0).any():
            report["errors"].append(f"{c} 存在非正值")

    bad_hl = int((df["high"] < df["low"]).sum())
    if bad_hl:
        report["errors"].append(f"{bad_hl} 根 high<low")

    if (df["volume"] < 0).any():
        report["errors"].append("volume 存在负值")

    # 引擎最少需要 bb_period+5 根（默认 25）
    if len(df) < 25:
        report["warnings"].append(f"仅 {len(df)} 根，不足引擎最少 25 根（bb_period=20）")

    return report
