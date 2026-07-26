# -*- coding: utf-8 -*-
"""market-data 配置：品种元信息表、symbol 解析、路径、布林带参数。

回测模式：**逐具体合约**（每个 *_kline.json = 一个具体合约，如 RB2505/SN2506/MA2509），
与无限易 ExportH2KLineData.py + _batch_backtest.run_all 的现状一致。无主连复权问题。

合约代码大小写惯例：上期所/大商所用小写前缀(rb/sn/cu/i/m)，郑商所用大写前缀(MA/SR/CF/TA/FG/SA)。
郑商所年月位数不统一（MA509 三位 vs MA2509 四位），新浪接口要试候选。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
STORE_DIR = Path(os.environ.get("STORE_DIR", _ROOT / "store"))
EXPORT_DIR = Path(os.environ.get("EXPORT_DIR", _ROOT / "exports"))

# 布林带参数（与 laicai-bridge/build_chart.py 的 _calc_bbands 单一口径；引擎 ddof 阶段4 统一）
BB_DEFAULTS = {"bb_period": 20, "bb_std": 2.0, "bb_ddof": 1}

# 品种前缀 → 元信息（前缀大小写不敏感匹配；multiplier 与 _batch_backtest.MULTIPLIERS 同口径）
_SYMBOL_META: dict[str, dict] = {
    # 上期所 SHFE（小写前缀）
    "rb": {"exchange": "SHFE", "name": "螺纹钢", "multiplier": 10},
    "hc": {"exchange": "SHFE", "name": "热卷", "multiplier": 10},
    "cu": {"exchange": "SHFE", "name": "铜", "multiplier": 5},
    "al": {"exchange": "SHFE", "name": "铝", "multiplier": 5},
    "zn": {"exchange": "SHFE", "name": "锌", "multiplier": 5},
    "ni": {"exchange": "SHFE", "name": "镍", "multiplier": 1},
    "sn": {"exchange": "SHFE", "name": "锡", "multiplier": 1},   # 锡 1 吨/手
    "au": {"exchange": "SHFE", "name": "黄金", "multiplier": 1000},
    "ag": {"exchange": "SHFE", "name": "白银", "multiplier": 15},
    "bu": {"exchange": "SHFE", "name": "沥青", "multiplier": 10},
    "ru": {"exchange": "SHFE", "name": "橡胶", "multiplier": 10},
    # 大商所 DCE（小写前缀）
    "i":  {"exchange": "DCE", "name": "铁矿石", "multiplier": 100},
    "m":  {"exchange": "DCE", "name": "豆粕", "multiplier": 10},
    "y":  {"exchange": "DCE", "name": "豆油", "multiplier": 10},
    "p":  {"exchange": "DCE", "name": "棕榈油", "multiplier": 10},
    "a":  {"exchange": "DCE", "name": "豆一", "multiplier": 10},
    "c":  {"exchange": "DCE", "name": "玉米", "multiplier": 10},
    "cs": {"exchange": "DCE", "name": "玉米淀粉", "multiplier": 10},
    # 郑商所 CZCE（大写前缀）
    "SR": {"exchange": "CZCE", "name": "白糖", "multiplier": 10},
    "CF": {"exchange": "CZCE", "name": "棉花", "multiplier": 5},
    "RM": {"exchange": "CZCE", "name": "菜粕", "multiplier": 10},
    "MA": {"exchange": "CZCE", "name": "甲醇", "multiplier": 10},
    "TA": {"exchange": "CZCE", "name": "PTA", "multiplier": 5},
    "FG": {"exchange": "CZCE", "name": "玻璃", "multiplier": 20},
    "SA": {"exchange": "CZCE", "name": "纯碱", "multiplier": 20},
}

_DEFAULT_META = {"exchange": "?", "name": None, "multiplier": 10}
_PREFIX_RE = re.compile(r"^([a-zA-Z]+)")


def parse_symbol(symbol: str) -> dict:
    """合约代码 → 品种元信息。

    RB2505 → {prefix:'rb', exchange:'SHFE', name:'螺纹钢', multiplier:10}
    SN2506 → {prefix:'sn', exchange:'SHFE', name:'锡', multiplier:1}
    未知前缀 → {prefix:<原>, exchange:'?', name:None, multiplier:10}
    """
    s = symbol.strip()
    m = _PREFIX_RE.match(s)
    prefix = m.group(1) if m else ""
    for k, v in _SYMBOL_META.items():
        if prefix.lower() == k.lower():
            return {"prefix": k, **v}
    return {"prefix": prefix, **_DEFAULT_META}


def symbol_candidates(symbol: str) -> list[str]:
    """生成新浪接口的候选 symbol（解决郑商所 3/4 位年月不统一）。

    MA509 → ['MA509','MA2509']；MA2509 → ['MA2509','MA509']；RB2505 → ['RB2505']。
    """
    s = symbol.strip()
    m = re.match(r"^([a-zA-Z]+)\s*0*(\d{3,4})$", s)
    if not m:
        return [s.upper()]
    letters, digits = m.group(1).upper(), m.group(2)
    cands = [letters + digits]
    if len(digits) == 3:
        cands.append(letters + "2" + digits)   # MA509 → MA2509
    elif len(digits) == 4:
        cands.append(letters + digits[-3:])     # MA2509 → MA509
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


# 周期 → 导出子目录 / kline_style / date 格式 / 数据来源
PERIODS = {
    "D1": {"subdir": "D1", "kline_style": "D1", "date_fmt": "%Y-%m-%d", "source": "daily"},
    "H2": {"subdir": "H2", "kline_style": "H2", "date_fmt": "%Y-%m-%d %H:%M", "source": "synth", "hours": 2},
    "H4": {"subdir": "H4", "kline_style": "H4", "date_fmt": "%Y-%m-%d %H:%M", "source": "synth", "hours": 4},
}


def ensure_dirs() -> None:
    """确保 store / exports 骨架目录存在。"""
    for d in (
        STORE_DIR / "daily", STORE_DIR / "minute",
        STORE_DIR / "factors", STORE_DIR / "meta",
        EXPORT_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
