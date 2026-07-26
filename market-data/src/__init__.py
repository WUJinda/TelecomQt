# -*- coding: utf-8 -*-
"""market-data 行情数据管线。

入口：`python -m src.cli`（在 market-data/ 目录下运行）。
本 __init__ 把项目根（market-data/）加入 sys.path，使包内模块可直接 `import config`。
"""
import pathlib
import sys

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
