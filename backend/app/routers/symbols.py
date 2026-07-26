"""品种元信息查询 API。

数据来源：
- 品种目录：market-data/config.py 的 _SYMBOL_META（单一真相源）
- 已有合约：扫描 market-data/store/daily/symbol=* 目录

GET /api/symbols          → 全部品种目录（含已有合约列表）
GET /api/symbols?q=螺纹   → 模糊搜索（中文名称或英文缩写）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import APIRouter, Query

# ---- 从 market-data/config.py 读取品种元信息（单一真相源）----
# 本地开发：parents[3] = TelecomQt 根 → market-data/
# Docker：由 ENV MARKET_DATA_DIR=/app/market-data 指定
_MARKET_DATA_DIR = Path(os.environ.get(
    "MARKET_DATA_DIR",
    Path(__file__).resolve().parents[3] / "market-data",
))
_STORE_DAILY = _MARKET_DATA_DIR / "store" / "daily"
_EXPORT_DIR = _MARKET_DATA_DIR / "exports"

_config = None


def _load_config():
    """延迟加载 market-data/config.py。

    Docker 环境用 ENV MARKET_DATA_DIR 指定路径；market-data 不存在时返回 None（优雅降级）。
    """
    global _config
    if _config is not None:
        return _config
    if not (_MARKET_DATA_DIR / "config.py").exists():
        return None
    if str(_MARKET_DATA_DIR) not in sys.path:
        sys.path.insert(0, str(_MARKET_DATA_DIR))
    import config  # type: ignore[import-not-found]
    _config = config
    return _config


def _scan_contracts(prefix: str) -> list[dict]:
    """扫描 store 目录，返回该品种下所有已有合约。"""
    config = _load_config()
    contracts = []
    if not _STORE_DAILY.exists():
        return contracts

    for sym_dir in sorted(_STORE_DAILY.iterdir()):
        if not sym_dir.is_dir() or not sym_dir.name.startswith("symbol="):
            continue
        symbol = sym_dir.name.replace("symbol=", "")
        meta = config.parse_symbol(symbol)
        # 大小写不敏感匹配品种前缀
        if meta["prefix"].lower() != prefix.lower():
            continue

        # 统计年份分区和数据量（从 parquet 文件名推断年份，记录数从 export JSON 读）
        years = []
        for yd in sorted(sym_dir.iterdir()):
            if yd.is_dir() and yd.name.startswith("year="):
                years.append(int(yd.name.replace("year=", "")))

        contracts.append({
            "symbol": symbol,
            "exchange": meta["exchange"],
            "name": meta["name"] or symbol,
            "years": years,
        })
    return contracts


router = APIRouter()


@router.get("/symbols")
def list_symbols(q: str = Query(default="", description="搜索关键词（中文名称或英文缩写）")):
    """品种目录。可选 q 参数做模糊搜索（同时匹配名称和代码）。

    返回按交易所分组的品种列表，每个品种含已有合约信息。
    """
    config = _load_config()
    if config is None:
        # market-data 不可用（如精简 Docker 部署）——返回空结果而非崩溃
        return {"total": 0, "query": q.strip(), "varieties": [], "note": "market-data 不可用"}

    q_lower = q.strip().lower()

    results = []
    for prefix, meta in config._SYMBOL_META.items():
        # 搜索过滤：q 同时匹配中文名、英文前缀、交易所
        if q_lower:
            haystack = f"{prefix} {meta.get('name', '')} {meta.get('exchange', '')}".lower()
            if q_lower not in haystack:
                continue

        contracts = _scan_contracts(prefix)
        results.append({
            "prefix": prefix,
            "name": meta.get("name"),
            "exchange": meta.get("exchange"),
            "multiplier": meta.get("multiplier"),
            "has_data": len(contracts) > 0,
            "contract_count": len(contracts),
            "contracts": contracts,
        })

    # 有数据的品种排前面
    results.sort(key=lambda r: (not r["has_data"], r["exchange"], r["prefix"]))

    return {
        "total": len(results),
        "query": q.strip(),
        "varieties": results,
    }
