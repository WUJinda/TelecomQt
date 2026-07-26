# -*- coding: utf-8 -*-
"""批量拉取 config 里全部 25 个品种的主力连续日K（symbol=前缀大写+0）。

用法：.venv/Scripts/python.exe batch_fetch_all.py
"""
from __future__ import annotations

import sys
import time

import config
from src.io_parquet import write_daily
from src.sources.akshare_daily import fetch_daily

# 25 个品种前缀 → 主力连续代码（大写 + "0"）
ALL_PREFIXES = [k for k in config._SYMBOL_META.keys()]


def main():
    config.ensure_dirs()
    ok, fail = [], []

    for prefix in ALL_PREFIXES:
        main_code = prefix.upper() + "0"
        meta = config._SYMBOL_META[prefix]
        t0 = time.time()
        try:
            df, hit = fetch_daily(main_code, retries=2, sleep=1.0)
            write_daily(df, hit)
            elapsed = time.time() - t0
            print(
                f"  OK  {hit:6s} ({meta['name'][:4]:4s} {meta['exchange']:4s}) "
                f"{len(df):5d} 根  {df['date'].min().date()} ~ {df['date'].max().date()}  "
                f"{elapsed:.1f}s"
            )
            ok.append(hit)
        except Exception as e:
            elapsed = time.time() - t0
            msg = str(e)[:80]
            print(f"  FAIL {main_code:6s} ({meta['name'][:4]:4s})  {elapsed:.1f}s  {msg}")
            fail.append((main_code, msg))

    print(f"\n=== 完成：{len(ok)} 成功 / {len(fail)} 失败 ===")
    if fail:
        print("失败列表：")
        for code, msg in fail:
            print(f"  {code}: {msg}")
    return ok, fail


if __name__ == "__main__":
    main()
