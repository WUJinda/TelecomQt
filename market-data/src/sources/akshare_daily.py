# -*- coding: utf-8 -*-
"""AKShare 期货日K拉取（逐具体合约）。

ak.futures_zh_daily_sina(symbol='RB2505') 返回该合约从上市到现在的日K。
返回列：date/open/high/low/close/volume/hold(=持仓量)/settle(结算价)。
郑商所代码 3/4 位不统一（MA509 vs MA2509），用 config.symbol_candidates 自动试候选。

返回 (标准化DataFrame, 实际命中的 symbol)。
DataFrame 列：date/open/high/low/close/volume(int)/open_interest(int)/symbol。
"""
from __future__ import annotations

import time

import pandas as pd

try:
    import akshare as ak
except ImportError:  # 未装环境时允许 import 本模块（写代码/测试用）
    ak = None

import config


def fetch_daily(symbol: str, retries: int = 3, sleep: float = 2.0) -> tuple[pd.DataFrame, str]:
    """拉具体合约日K（带候选 + 重试 + 限速）。返回 (df, 命中symbol)。"""
    if ak is None:
        raise RuntimeError("akshare 未安装：pip install -r requirements-data.txt")
    cands = config.symbol_candidates(symbol)
    last_err = None
    for sym in cands:
        for _ in range(retries):
            try:
                raw = ak.futures_zh_daily_sina(symbol=sym)
                if raw is None or len(raw) == 0:
                    raise RuntimeError("空数据")
                df = raw.rename(columns={"hold": "open_interest"})
                df["date"] = pd.to_datetime(df["date"])
                for c in ("open", "high", "low", "close"):
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                df = df.dropna(subset=("open", "high", "low", "close"))
                df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0).astype("int64")
                df["open_interest"] = pd.to_numeric(df.get("open_interest", 0), errors="coerce").fillna(0).astype("int64")
                df["symbol"] = sym
                df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
                return (
                    df[["date", "open", "high", "low", "close", "volume", "open_interest", "symbol"]],
                    sym,
                )
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(sleep)
    raise RuntimeError(f"拉取 {symbol} 失败（试过候选 {cands}）：{last_err}")
