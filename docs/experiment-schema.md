# experiment.json 字段约定

> 这是 LaiCai 回测 ↔ 协作面板之间的**唯一契约**。LaiCai 负责产出，面板负责消费。
> 字段直接照搬 LaiCai `_batch_backtest.py::run_all()` 的 result 结构，包一层 experiment envelope。

## 顶层结构

```json
{
  "experiment_id": "20260712_210518_double_top_short_baseline",
  "generated_at": "2026-07-12T21:05:18",
  "strategy_name": "日线双峰左侧做空",
  "strategy_type": "double_top_short",
  "direction": "short",
  "mode": "baseline",
  "params": { },
  "capital": { },
  "summary": { },
  "instruments": [ ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| experiment_id | string | 时间戳+策略类型(+mode)，唯一；同时是目录名 |
| generated_at | ISO8601 | 生成时间，面板按此倒序 |
| strategy_name | string | 中文展示名 |
| strategy_type | string | 机器标识（double_top_short / boll_breakout_short …） |
| direction | `short`\|`long` | 方向，决定交易明细表头标注 |
| mode | string | strict/relaxed/baseline 等，可空 |
| params | object | 本次回测参数快照 |
| capital | object | 资金管理规则 |
| summary | object | 汇总指标（见下） |
| instruments | array | 每个品种的结果（见下） |

## summary

```json
{
  "instruments_tested": 5,
  "instruments_with_trades": 2,
  "total_trades": 2,
  "total_pnl": 940639,
  "total_margin": 1975864,
  "total_return_rate": 47.61,
  "win_rate": 100.0,
  "max_drawdown": 0,
  "avg_holding_days": 9.5,
  "best_instrument": "ag2606",
  "worst_instrument": "TA2609"
}
```

## instruments[i]（有交易时）

```json
{
  "instrument": "ag2606",
  "exchange": "SHFE",
  "kline_style": "D1",
  "date_start": "2025-06-17",
  "date_end": "2026-04-28",
  "records": 211,
  "volume_multiple": 15,
  "margin_rate": 0.12,
  "max_bandwidth": 0.644,
  "bw_above_pct": 54,
  "trade_count": 1,
  "total_pnl": 631823,
  "win_rate": 100.0,
  "max_drawdown": 0,
  "avg_holding_days": 8,
  "trades": [ ]
}
```

> 无交易的品种：`trade_count: 0`、`trades: []`，且**不含** total_pnl/win_rate/max_drawdown/avg_holding_days（面板用 `??` 兜底）。

## trades[i]

```json
{
  "no": 1,
  "open_date": "2025-11-13",
  "open_price": 12640,
  "close_date": "2025-11-21",
  "close_price": 11658,
  "volume": 43,
  "holding_days": 8,
  "margin": 978336,
  "points": 982,
  "fee": 1567.22,
  "net_pnl": 631823,
  "return_rate": 64.6,
  "win": true
}
```

> 字段定义与 LaiCai `calc_trade_pnl()` 完全一致；`no` 为面板展示序号（可选，缺失时前端按顺序补）。

## trades[i].chart（可选 · 单笔交易复盘图）

> 由 LaiCai 回测脚本构建（参考 `laicai-bridge/build_chart.py`），`emit_experiment` 原样透传。
> 缺失时前端复盘区回退为"该笔无 K 线数据"。

```json
{
  "window": { "pre": 20, "post": 5 },
  "klines": [
    { "date": "2025-10-15", "open": 12200, "high": 12310, "low": 12150,
      "close": 12260, "volume": 82000,
      "bb_upper": 12580, "bb_mid": 12240, "bb_lower": 11900, "bw": 0.054 }
  ],
  "markers": {
    "open":   { "date": "2025-11-13", "price": 12640 },
    "close":  { "date": "2025-11-21", "price": 11658 },
    "h_left": { "date": "2025-11-10", "price": 12900 },
    "trigger_line": 12280,
    "take_profit": 12050,
    "no_stop_loss": true
  },
  "bw_pct_at_open": 85,
  "summary": "ag2606 日线 · 做空 43 手 · 11/13 @12640 开仓 → 11/21 @11658 平仓 · …"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| window.pre/post | int | 开仓前 / 平仓后 K 线根数 |
| klines[] | array | 窗口内每根 K 线 OHLCV + 同步布林三轨 / bandwidth |
| klines[].bb_upper/mid/lower | float | 布林上/中/下轨（per-kline，与 LaiCai 引擎 `bbands_data` 一致） |
| klines[].bw | float | 该根带宽 = (上轨 − 下轨) / 中轨 |
| markers.open/close | {date,price} | 开 / 平仓标点，定位到 K 线 |
| markers.h_left | {date,price} | 左峰高点标点（本策略只有左峰，无右峰/颈线） |
| markers.trigger_line | float | 水平参考线：中下轨中间位（代码真正触发开仓的位置） |
| markers.take_profit | float | 止盈水平线（开仓时中轨；本策略止盈 = 布林中轨） |
| markers.no_stop_loss | bool | true 时前端显示「本策略不设止损」风险提示 |
| bw_pct_at_open | int | 开仓时带宽的历史分位（0–100） |
| summary | string | 一句话复盘（导出生成；前端缺失时用 trades 字段兜底拼接） |

> ⚠ 样例 `experiment.json` 里的 chart 是**合成示意数据**（`_tools/gen_mock_chart.py` 按真实开/平仓价反推走势），仅用于验证前端呈现。真实 chart 需由 LaiCai 引擎导出。
