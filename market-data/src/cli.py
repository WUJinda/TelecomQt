# -*- coding: utf-8 -*-
"""market-data 统一 CLI 入口。

用法（在 market-data/ 目录下）:
    python -m src.cli fetch-daily --symbol RB2505,SN2506,MA2509   # 批量拉具体合约日K → store
    python -m src.cli export --symbol RB2505 --period D1          # store → exports/D1/rb2505_kline.json
    python -m src.cli validate --symbol RB2505 --period D1
"""
import argparse
import json
import sys

import config
from src.export_kline import export_kline
from src.io_parquet import write_daily
from src.sources.akshare_daily import fetch_daily
from src.validate import validate_daily


def cmd_fetch_daily(args: argparse.Namespace) -> None:
    symbols = [s.strip() for s in args.symbol.split(",") if s.strip()]
    for sym in symbols:
        try:
            df, hit = fetch_daily(sym)
            print(f"{sym}: 命中 {hit}（{config.parse_symbol(hit)['name']}），"
                  f"{len(df)} 根日K（{df['date'].min().date()} ~ {df['date'].max().date()}）")
            write_daily(df, hit)
            print(f"  → store/daily/symbol={hit}/")
        except Exception as e:  # noqa: BLE001
            print(f"{sym}: 失败 - {e}")


def cmd_export(args: argparse.Namespace) -> None:
    try:
        out = export_kline(args.symbol, period=args.period, start=args.start, end=args.end)
        print(f"已导出：{out}")
    except Exception as e:  # noqa: BLE001
        print(f"导出失败：{e}", file=sys.stderr)
        sys.exit(1)


def cmd_validate(args: argparse.Namespace) -> None:
    if args.period != "D1":
        print(f"阶段1 仅支持 D1 校验；{args.period} 校验在后续阶段实现")
        return
    rep = validate_daily(args.symbol)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if rep["errors"]:
        sys.exit(1)


def main() -> None:
    config.ensure_dirs()
    p = argparse.ArgumentParser(prog="market-data")
    sub = p.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("fetch-daily", help="拉 AKShare 日K → store（支持逗号分隔批量）")
    pf.add_argument("--symbol", required=True, help="合约代码，多个用逗号分隔，如 RB2505,SN2506,MA2509")
    pf.set_defaults(func=cmd_fetch_daily)

    pe = sub.add_parser("export", help="store → exports/<period>/<symbol>_kline.json")
    pe.add_argument("--symbol", required=True)
    pe.add_argument("--period", default="D1", choices=list(config.PERIODS))
    pe.add_argument("--start", help="起始日期 YYYY-MM-DD")
    pe.add_argument("--end", help="结束日期 YYYY-MM-DD")
    pe.set_defaults(func=cmd_export)

    pv = sub.add_parser("validate", help="数据质量校验")
    pv.add_argument("--symbol", required=True)
    pv.add_argument("--period", default="D1")
    pv.set_defaults(func=cmd_validate)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
