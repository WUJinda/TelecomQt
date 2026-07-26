# -*- coding: utf-8 -*-
"""store → *_kline.json（无限易格式，_batch_backtest 零改动消费）。

格式严格对齐 ExportKLineData.py / ExportH2KLineData.py：
    顶层 {export_time, exchange, instrument, name, kline_style, total_records, data:[]}
    data[] {date(str), code, open(float), high(float), low(float), close(float),
            volume(int), open_interest(int)}

日线 date='YYYY-MM-DD'（ExportKLineData.py 的 [:10]），小时 date='YYYY-MM-DD HH:MM'（[:16]）。
instrument/exchange/name 从 read_daily 命中的 symbol 反推（config.parse_symbol）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import config
from src.io_parquet import read_daily


def export_kline(symbol: str, period: str = "D1", start=None, end=None, out_dir=None) -> Path:
    """从 store 读日K → 写 exports/D1/<symbol>_kline.json。返回写出路径。"""
    if period not in config.PERIODS:
        raise ValueError(f"未知周期 {period}，可选 {list(config.PERIODS)}")
    pinfo = config.PERIODS[period]
    if pinfo["source"] != "daily":
        raise NotImplementedError(f"周期 {period} 的导出在阶段3实现（需分钟合成）")

    df = read_daily(symbol, start=start, end=end)
    if df.empty:
        raise RuntimeError(
            f"store 无 {symbol} 日K数据，请先运行：python -m src.cli fetch-daily --symbol {symbol}"
        )

    # 命中 symbol：df 带 symbol 列（fetch 时写入）；用它做 instrument/文件名/反推元信息
    hit_symbol = str(df["symbol"].iloc[0]) if "symbol" in df.columns else symbol.strip().upper()
    meta = config.parse_symbol(hit_symbol)
    date_fmt = pinfo["date_fmt"]
    data = [
        {
            "date": row["date"].strftime(date_fmt),
            "code": hit_symbol,
            "open": round(float(row["open"]), 4),
            "high": round(float(row["high"]), 4),
            "low": round(float(row["low"]), 4),
            "close": round(float(row["close"]), 4),
            "volume": int(row["volume"]),
            "open_interest": int(row.get("open_interest", 0)),
        }
        for _, row in df.iterrows()
    ]

    out_dir = Path(out_dir or config.EXPORT_DIR) / pinfo["subdir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{hit_symbol.lower()}_kline.json"

    envelope = {
        "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "exchange": meta["exchange"],
        "instrument": hit_symbol,
        "name": meta["name"] or hit_symbol,
        "kline_style": pinfo["kline_style"],
        "total_records": len(data),
        "data": data,
    }
    out_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
