# -*- coding: utf-8 -*-
"""批量导出全部主力连续品种的 D1 JSON。"""
from __future__ import annotations

import config
from src.export_kline import export_kline

ALL_MAINS = [k.upper() + "0" for k in config._SYMBOL_META.keys()]


def main():
    ok, fail = [], []
    for sym in ALL_MAINS:
        try:
            out = export_kline(sym, period="D1")
            ok.append((sym, out.name))
        except Exception as e:
            fail.append((sym, str(e)[:80]))

    print(f"导出完成：{len(ok)} 成功 / {len(fail)} 失败")
    for sym, fname in ok:
        print(f"  OK  {sym:6s} -> exports/D1/{fname}")
    if fail:
        for sym, msg in fail:
            print(f"  FAIL {sym:6s} {msg}")


if __name__ == "__main__":
    main()
