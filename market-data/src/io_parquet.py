# -*- coding: utf-8 -*-
"""Parquet 读写：Hive 分区 + 幂等合并 + 候选回退。

daily/ 按 symbol/year 分区：store/daily/symbol=RB2505/year=2024/part.parquet

写入幂等：与该年分区已有数据 concat → 按 date 去重（新值覆盖旧值）→ 覆盖写。
读取候选回退：用户传 MA509，store 里是 MA2509，自动找到存在的分区。
"""
from __future__ import annotations

import pathlib

import duckdb
import pandas as pd

import config

_DAILY_COLS = ["date", "open", "high", "low", "close", "volume", "open_interest"]


def _normalize_daily(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("volume", "open_interest"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
        else:
            df[c] = 0
    df = df.dropna(subset=("open", "high", "low", "close"))
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return df


def write_daily(df: pd.DataFrame, symbol: str, store_dir=None) -> None:
    """按 year 分区写入 store/daily/symbol=<sym>/year=<YYYY>/part.parquet（幂等）。"""
    store_dir = pathlib.Path(store_dir or config.STORE_DIR)
    df = _normalize_daily(df)
    if df.empty:
        return
    df = df.copy()
    df["year"] = df["date"].dt.year
    for year, grp in df.groupby("year"):
        sub = grp.drop(columns="year")
        out_dir = store_dir / "daily" / f"symbol={symbol}" / f"year={int(year)}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "part.parquet"
        if out_file.exists():
            old = pd.read_parquet(out_file)
            sub = _normalize_daily(pd.concat([old, sub], ignore_index=True))
        sub.to_parquet(out_file, index=False)


def _resolve_base(symbol: str, store_dir: pathlib.Path) -> pathlib.Path | None:
    """找到存在的 symbol 分区目录（候选回退：MA509 → MA2509）。"""
    sym_up = symbol.strip().upper()
    for sym in [sym_up] + [c for c in config.symbol_candidates(symbol) if c != sym_up]:
        b = store_dir / "daily" / f"symbol={sym}"
        if b.exists():
            return b
    return None


def read_daily(symbol: str, start=None, end=None, store_dir=None) -> pd.DataFrame:
    """DuckDB 直查 Parquet，返回按 date 升序的日K。无数据返回空 DataFrame。"""
    store_dir = pathlib.Path(store_dir or config.STORE_DIR)
    base = _resolve_base(symbol, store_dir)
    empty = pd.DataFrame(columns=_DAILY_COLS)
    if base is None:
        return empty
    pattern = str(base).replace("\\", "/") + "/**/*.parquet"
    con = duckdb.connect()
    try:
        df = con.execute(f"SELECT * FROM read_parquet('{pattern}') ORDER BY date").df()
    finally:
        con.close()
    if df.empty:
        return empty
    df["date"] = pd.to_datetime(df["date"])
    if start:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["date"] <= pd.Timestamp(end)]
    return df.reset_index(drop=True)
