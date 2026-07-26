# market-data —— 期货行情数据管线

把**客观真实的历史期货行情**落库，并以无限易同格式 `*_kline.json` 导出，供 LaiCai/无限易回测引擎**零改动**消费，替代手动从无限易导出。

## 数据流

```
[AKShare 主连日K]  ──┐                                  ┌─→ exports/D1/*_kline.json  ──→ 回测引擎(日K)
                     │                                  │
[米筐 rqdatac 1min] ─┼─→ store/ (Parquet+Hive 唯一真相) ─┼─→ 合成 H2/H4 → exports/H2|H4/*_kline.json
 (一次性冷启动)      │   minute/ daily/ factors/ meta/    │
                     │                                  └─→ chart构建(阶段4)
[AKShare 1min] ──────┘
 (每日增量)
```

**核心原则**：store 是唯一真相；只存 1 分钟 + 日K主连；2H/4H 从 1 分钟现合成（trading_day 对齐 + 等交易长度桶）；对外只暴露 `*_kline.json`。

## 快速开始

```bash
# 1. 建独立 venv（用根治过 sqlite3 的 Python 3.12.10）
D:\Programs\Python\Python312\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-data.txt

# 2. 拉日K主连 → 落库 → 导出 D1（阶段1，免费无依赖，立即可用）
.venv\Scripts\python.exe -m src.cli fetch-daily --symbol sn
.venv\Scripts\python.exe -m src.cli export --symbol sn --period D1
.venv\Scripts\python.exe -m src.cli validate --symbol sn --period D1
```

导出的 `exports/D1/sn888_kline.json` 可直接拷进 `~/Desktop/quanda_exports/`，或把回测脚本的 `DATA_DIR` 指向 `exports/D1/`。

## 与回测/面板的关系

- **回测引擎**（`InfiniTrader_.../pyStrategy/self_strategy/backtest/_batch_backtest.py`）直接 `json.load` 读 `*_kline.json`，本管线产出同结构文件，**引擎零改动**。
- **面板**（TelecomQt/backend）只读 `experiment.json`，不读 K线 → store 不挂面板 Docker。
- **chart**（`laicai-bridge/fetch_kline.py`）：阶段4 会改成从 store 读，周期对齐回测周期。

## 已知偏差（重要）

- 合成的 H2/H4 用**等交易长度桶**（按真实成交的 1min 根数计桶），桶边界与无限易内置的钟点 H2 **不一致**。无法逐 H2 bar 对齐无限易回测，只能保证「日聚合 OHLC 一致 + 日线层信号一致」。
- **布林带 ddof**：管线/chart 用 ddof=1，引擎当前用 ddof=0（~2.5% 偏差）。阶段4 统一。

## 阶段路线

| 阶段 | 内容 | 状态 |
|------|------|------|
| 0 | 脚手架 + 独立 venv + 依赖 | 进行中 |
| 1 | 日K管线 MVP（AKShare 主连 → Parquet → D1 导出） | 待做 |
| 2 | 分钟K冷启动（米筐试用）+ AKShare 每日增量 | 待做（需注册米筐） |
| 3 | 主连比例后复权 + 等交易长度桶合成 H2/H4 | 待做 |
| 4 | 校验加固 + 对接回测引擎 + chart 统一 + 接面板 | 待做（需拍板 ddof/主连/sn乘数） |

详见 `.context/plan/` 下的实现计划。
