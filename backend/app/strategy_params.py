"""双峰左侧做空策略 — 参数元信息（前端渲染 + 后端校验共用）。

前端根据 STRATEGY_PARAM_GROUPS 渲染分组表单；
后端用 get_default_params() / validate_params() 进行参数初始化和校验。
"""
from __future__ import annotations

# ============================================================
# 参数分组定义（13 个参数，4 组）
# ============================================================
STRATEGY_PARAM_GROUPS = [
    {
        "name": "入场条件",
        "icon": "📈",
        "params": [
            {
                "key": "bandwidth_min",
                "label": "带宽阈值",
                "type": "float",
                "default": 0.14,
                "min": 0.10,
                "max": 0.25,
                "step": 0.01,
                "description": "布林带带宽最低要求，越大越严格",
            },
            {
                "key": "tilt_threshold",
                "label": "平行度阈值",
                "type": "float",
                "default": 0.008,
                "min": 0.003,
                "max": 0.020,
                "step": 0.001,
                "description": "布林带水平度阈值，total_tilt 小于此值才判定为水平",
            },
            {
                "key": "zone_lower",
                "label": "左峰区域下界",
                "type": "float",
                "default": 0.99,
                "min": 0.95,
                "max": 1.00,
                "step": 0.01,
                "description": "入场区域下界 = H_left × zone_lower",
            },
            {
                "key": "zone_upper",
                "label": "左峰区域上界",
                "type": "float",
                "default": 1.02,
                "min": 1.00,
                "max": 1.05,
                "step": 0.01,
                "description": "入场区域上界 = H_left × zone_upper",
            },
            {
                "key": "left_peak_lookback",
                "label": "左峰回溯天数",
                "type": "int",
                "default": 30,
                "min": 15,
                "max": 60,
                "step": 1,
                "description": "左峰最大回溯周期（安全阀，防止过老的峰）",
            },
        ],
    },
    {
        "name": "布林带",
        "icon": "📊",
        "params": [
            {
                "key": "bb_period",
                "label": "计算周期",
                "type": "int",
                "default": 20,
                "min": 10,
                "max": 30,
                "step": 1,
                "description": "布林带计算周期（天数）",
            },
            {
                "key": "bb_std",
                "label": "标准差倍数",
                "type": "float",
                "default": 2.0,
                "min": 1.5,
                "max": 3.0,
                "step": 0.1,
                "description": "布林带标准差倍数",
            },
            {
                "key": "bb_ddof",
                "label": "自由度",
                "type": "int",
                "default": 1,
                "min": 0,
                "max": 1,
                "step": 1,
                "description": "标准差自由度（1=样本标准差, 0=总体标准差）",
            },
        ],
    },
    {
        "name": "风控",
        "icon": "⏱️",
        "params": [
            {
                "key": "max_holding_days",
                "label": "最大持仓天数",
                "type": "int",
                "default": 0,
                "min": 0,
                "max": 60,
                "step": 1,
                "description": "最大持仓天数（超时强制平仓，0 = 不限制）",
            },
            {
                "key": "fee_rate",
                "label": "手续费率",
                "type": "float",
                "default": 0.0001,
                "min": 0.00005,
                "max": 0.0003,
                "step": 0.00001,
                "description": "手续费率（双边各收一次）",
            },
        ],
    },
    {
        "name": "资金管理",
        "icon": "💰",
        "params": [
            {
                "key": "max_per_trade",
                "label": "单笔上限",
                "type": "int",
                "default": 1000000,
                "min": 500000,
                "max": 2000000,
                "step": 100000,
                "description": "单笔最大保证金",
            },
            {
                "key": "max_total_exposure",
                "label": "总敞口上限",
                "type": "int",
                "default": 6000000,
                "min": 2000000,
                "max": 10000000,
                "step": 500000,
                "description": "跨品种总敞口上限",
            },
            {
                "key": "total_capital",
                "label": "总资金",
                "type": "int",
                "default": 10000000,
                "min": 5000000,
                "max": 20000000,
                "step": 1000000,
                "description": "总资金（仅展示用，不参与计算）",
            },
        ],
    },
]

# 扁平化索引，方便快速查找
_PARAM_INDEX: dict[str, dict] = {}
for _grp in STRATEGY_PARAM_GROUPS:
    for _p in _grp["params"]:
        _PARAM_INDEX[_p["key"]] = _p


def get_default_params() -> dict:
    """返回默认参数 dict。"""
    return {key: meta["default"] for key, meta in _PARAM_INDEX.items()}


def validate_params(params: dict | None) -> dict:
    """校验并修正参数，返回合法参数 dict。

    - 以默认参数为基础，只接受已定义的 key
    - float 参数保留浮点，int 参数取整
    - 截断到 [min, max] 范围内
    """
    base = get_default_params()
    if not params:
        return base

    result = {}
    for key, meta in _PARAM_INDEX.items():
        raw = params.get(key, meta["default"])
        try:
            if meta["type"] == "float":
                val = float(raw)
            else:
                val = int(float(raw))
        except (TypeError, ValueError):
            val = meta["default"]

        # 截断到合法范围
        val = max(meta["min"], min(meta["max"], val))
        result[key] = val

    return result
