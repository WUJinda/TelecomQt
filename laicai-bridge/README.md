# laicai-bridge —— LaiCai → 协作面板的数据桥

把 LaiCai 回测结果导出成面板能读的 `experiment.json`。

## 一步接入

1. 把本目录的 `emit_experiment.py` 复制到 LaiCai 的 `pyStrategy/self_strategy/` 下
   （和 `_batch_backtest.py` 同级）。

2. 在你的回测脚本末尾（已经拿到 `all_results` 之后）加两行：

   ```python
   from emit_experiment import emit_experiment

   emit_experiment(
       instruments=all_results,
       strategy_name="日线双峰左侧做空",
       strategy_type="double_top_short",
       direction="short",
       params={                                 # 本次参数快照
           "bb_period": 20, "bb_std": 2.0,
           "bandwidth_min": 0.15, "fee_rate": 0.0001,
       },
       capital={
           "total_capital": 10_000_000,
           "max_per_trade": 1_000_000,
           "max_total_exposure": 6_000_000,
       },
       out_dir="~/Desktop/experiments",         # 导出根目录
       mode="baseline",
   )
   ```

3. 跑完回测后，`out_dir/<experiment_id>/experiment.json` 就是面板要的文件。

## 为每笔交易生成 K 线复盘图（chart，可选但推荐）

让面板的「逐笔复盘」视图能显示每笔交易的 K 线 + 开平仓标注 + 布林三轨 + 左峰/触发位：

1. 把本目录的 `build_chart.py` 复制到 LaiCai 的 `pyStrategy/self_strategy/doubleTop/` 下
   （和 `double_top_backtest.py` 同级）。

2. 在回测脚本里、调用 `emit_experiment` **之前**，给每个品种结果附上 chart：

   ```python
   from build_chart import attach_charts

   # all_results 来自 backtest_all() —— 每个结果含 records_raw / trades_raw / trades
   for r in all_results:
       attach_charts(r, params)      # 给每笔交易塞 chart，并清理 records_raw 等大字段
   ```

3. 然后照常 `emit_experiment(instruments=all_results, ...)`，chart 会自动透传进 experiment.json。

> `attach_charts` 假设 result 是 `double_top_backtest.backtest_one()` 的输出（含 `records_raw`/`trades_raw`）。
> 它会重建 DataFrame、重算布林带、按每笔交易的 `open_idx/close_idx/h_left_idx` 切窗口。
> 若你的回测脚本结构不同，参考 `build_chart.py` 里的 `build_chart()` 自行适配。
> 字段约定见面板仓库 `docs/experiment-schema.md` 的 `trades[i].chart`。

## 同步到面板

把生成的 `experiment.json`（整个 `<experiment_id>` 目录）放到面板后端的：

```
TelecomQt/backend/data/experiments/<experiment_id>/experiment.json
```

后端启动时会自动扫描该目录，朋友刷新页面就能看到新报告。

> 未来 v0.2 加「一键回测」后，这一步可由后端自动完成；现在先手动同步（rsync / 网盘 / 拷贝都行）。

## 命令行演示

`emit_experiment.py` 也可直接跑（用 boll 引擎做演示，验证管道通不通）：

```bash
cd pyStrategy/self_strategy
python emit_experiment.py --data ~/Desktop/quanda_exports_h2 --out ~/Desktop/experiments
```

字段约定见面板仓库 `docs/experiment-schema.md`。
