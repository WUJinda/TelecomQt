# NAS 功能适配指南 — 回测中心

> **用途**：本文档面向 NAS 端运维 AI / 操作人员，说明回测中心功能的架构变更、数据存储布局和 NAS 部署操作。

---

## 1. 本次更新内容

在现有策略协作面板基础上，新增「🧪 回测中心」标签页和相关后端能力：

| 功能 | 说明 |
|------|------|
| 交互式回测 | 在面板上直接调整 13 个策略参数、选择品种、一键运行回测 |
| 会话管理 | 按会话组织回测记录（树状结构），支持新建/删除/选择 |
| 结果展示 | 回测完成后展示盈亏/胜率/回撤等指标 + K 线复盘图 |
| 展开/全屏模式 | 右栏可一键展开为全宽，图表自适应 |
| 外部归入会话 | 回测报告 Tab 中可将外部 CLI 推送的实验「归入」回测中心会话 |

**对 NAS 部署的影响**：
- 无破坏性变更，现有 5 个标签页（回测报告/策略想法/交易计划/品种查询/统计报告）完全不变
- 新增了后端文件和数据库表，需要重建 Docker 镜像
- 数据存储路径不变，仍使用现有的 `backend/data/` 挂载

---

## 2. 架构概览

```
┌─────────────────────────────────────────────────┐
│              前端 frontend/index.html            │
│           Alpine.js + ECharts，单文件             │
│  导航栏 6 个标签页：                               │
│  回测报告 | 🧪回测中心 | 策略想法 | 交易计划 |     │
│  品种查询 | 统计报告                               │
└────────────────────┬────────────────────────────┘
                     │ HTTP API
┌────────────────────▼────────────────────────────┐
│            FastAPI 后端 (Docker 内)               │
│                                                  │
│  新增文件：                                       │
│  ├── app/strategy_params.py    13 个策略参数定义  │
│  ├── app/backtest_engine.py    回测引擎封装       │
│  └── app/routers/backtest.py   10 个 API 端点     │
│                                                  │
│  修改文件：                                       │
│  ├── app/main.py               注册新路由         │
│  ├── app/models.py             新增 2 张表        │
│  └── laicai-bridge/            新增 max_holding   │
│      double_top_backtest.py    _days 参数(兼容)   │
│                                                  │
│  ┌──────────────────────────────────────┐       │
│  │  SQLite (app.db)                     │       │
│  │  新增表：                             │       │
│  │  ├── BacktestSession (会话元信息)     │       │
│  │  └── BacktestRecord (回测记录索引)    │       │
│  └──────────────────────────────────────┘       │
└────────────────────┬────────────────────────────┘
                     │ 文件 I/O
┌────────────────────▼────────────────────────────┐
│         NAS 存储布局                               │
│  /volume1/TelecomQt/backend/data/                │
│  ├── experiments/                                │
│  │   ├── 20260712_.../     ← 外部 CLI 推送       │
│  │   ├── _sessions/        ← 面板内回测          │
│  │   │   └─ <会话slug>/                          │
│  │   │       └─ <exp_id>/                        │
│  │   │           └── experiment.json             │
│  │   └── _tools/                                 │
│  ├── app.db               ← SQLite 数据库        │
│  └── analytics/           ← 统计报告             │
└─────────────────────────────────────────────────┘
```

---

## 3. 数据存储说明

### 3.1 两层存储：SQLite 索引 + 文件实体

| 层 | 载体 | 存什么 | 当前大小 |
|---|---|---|---|
| **索引层** | `app.db` (SQLite) | 会话名称/描述、记录的 label/参数快照/摘要指标(盈亏/胜率/笔数/回撤) | ~60 KB |
| **实体层** | `experiments/` 目录 | 每次回测的完整 `experiment.json`（含逐笔交易、K线复盘图 base64、品种明细） | ~21 MB |

**设计原则**：列表渲染只查 SQLite（毫秒级），点开详情才读文件。experiment.json 自包含，不依赖数据库。

### 3.2 experiments 目录结构

```
experiments/
│
├── 20260712_210518_double_top_short_baseline/   ← 外部 CLI 推送（扁平目录）
│   └── experiment.json                           report_loader 只扫描这一层
├── 20260729_220432_double_top_short_baseline/
├── ...                                           （共 15+ 个旧实验）
│
├── _sessions/                                    ← 面板内回测（会话组织）
│   ├── 带宽敏感度分析/
│   │   └── 20260810_212030_double_top_short_web/
│   │       └── experiment.json
│   └── Zone调优/
│       └── ...
│
└── _tools/                                       ← 辅助工具脚本
```

**两条路径互不干扰**：
- `report_loader.py` 只扫描扁平目录的直接子文件夹（不递归进 `_sessions/`）
- `backtest_engine.py` 读写 `_sessions/<session_slug>/<experiment_id>/`
- 「归入会话」= 在 SQLite 建一条 `source="external"` 的索引行，指向扁平目录，**不移动文件**

### 3.3 SQLite 注意事项

SQLite 通过文件锁工作。**必须确保 `app.db` 在 NAS 本地磁盘（ext4/btrfs）上**，不要放在 NFS/SMB 网络共享上。
当前 Docker 部署方案中 `app.db` 在 Docker Volume `/data/` 下，由 NAS 本地磁盘承载，没有问题。

---

## 4. 新增 API 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/backtest/strategy-params` | 返回 13 个策略参数定义（4 组） |
| GET | `/api/backtest/datasets` | 扫描 D1 目录，返回可回测品种列表 |
| GET | `/api/backtest/sessions` | 会话列表（含 record_count） |
| POST | `/api/backtest/sessions` | 创建会话 |
| PATCH | `/api/backtest/sessions/{id}` | 更新会话 |
| DELETE | `/api/backtest/sessions/{id}` | 删除会话（级联删除记录和文件） |
| GET | `/api/backtest/sessions/{id}/records` | 会话下回测记录列表 |
| GET | `/api/backtest/records/{id}` | 单条记录详情（含 experiment.json） |
| DELETE | `/api/backtest/records/{id}` | 删除记录 |
| POST | `/api/backtest/run` | 执行回测 |
| POST | `/api/backtest/sessions/{id}/import` | 外部实验归入会话 |

所有端点均在 `/api/backtest` 前缀下，无需额外鉴权（依赖 Cloudflare Access 统一鉴权）。

---

## 5. NAS 部署操作

### 5.1 更新代码

```bash
# 在 NAS 上拉取最新代码
cd /volume1/TelecomQt
git pull origin main
```

### 5.2 重建 Docker 镜像

```bash
cd /volume1/TelecomQt/deploy
docker compose up -d --build panel
```

**为什么需要 rebuild**：新增了 3 个 Python 文件（`strategy_params.py`、`backtest_engine.py`、`routers/backtest.py`），需要重新 COPY 进镜像。前端 `index.html` 也打包在镜像内。

### 5.3 数据库迁移

**首次启动时自动建表**。`app/main.py` 的 `init_db()` 调用 `SQLModel.metadata.create_all(engine)`，幂等操作。

如果 NAS 上已有旧版 `app.db`，新增的 `BacktestSession` 和 `BacktestRecord` 表会自动创建，不影响现有数据。

**唯一需要手动操作的情况**：如果旧 `app.db` 中 `BacktestRecord` 表缺少 `source` 字段（在本地开发环境出现过），手动执行：

```bash
# 进入容器执行
docker exec -it laicai-panel python -c "
import sqlite3
db = sqlite3.connect('/data/app.db')
cursor = db.cursor()
cursor.execute('PRAGMA table_info(backtestrecord)')
cols = [row[1] for row in cursor.fetchall()]
if 'source' not in cols:
    cursor.execute('ALTER TABLE backtestrecord ADD COLUMN source TEXT DEFAULT \"web\"')
    db.commit()
    print('Added source column')
else:
    print('source column already exists')
db.close()
"
```

> 注：`init_db()` 的 `create_all` 只创建不存在的表，不会给已存在的表添加新字段。上述命令是安全的一次性操作。

### 5.4 验证

```bash
# 1. 健康检查
curl http://localhost:8000/api/health
# 期望: {"ok":true,"version":"0.1.0"}

# 2. 策略参数
curl http://localhost:8000/api/backtest/strategy-params | python -m json.tool | head -20
# 期望: 返回 4 组参数定义

# 3. 品种列表
curl http://localhost:8000/api/backtest/datasets | python -m json.tool | head -10
# 期望: 返回 37 个品种

# 4. 会话列表
curl http://localhost:8000/api/backtest/sessions
# 期望: [] (空列表，或已有会话)

# 5. 打开面板
# 浏览器访问 https://panel.darewin.icu
# 导航栏应有 6 个标签页，点击「🧪 回测中心」可见三栏布局
```

### 5.5 验证回测功能（端到端）

```bash
# 创建会话
curl -X POST http://localhost:8000/api/backtest/sessions \
  -H "Content-Type: application/json" \
  -d '{"name": "NAS验证测试"}'

# 执行回测（用返回的 session_id）
curl -X POST http://localhost:8000/api/backtest/run \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": 1,
    "label": "nas_test",
    "instruments": ["ag0"],
    "params": {"bandwidth_min": 0.14}
  }' | python -m json.tool | head -30

# 期望: 返回 record_id、summary（含 total_pnl、win_rate 等）、experiment 数据

# 查看会话记录
curl http://localhost:8000/api/backtest/sessions/1/records | python -m json.tool

# 清理
curl -X DELETE http://localhost:8000/api/backtest/sessions/1
```

---

## 6. 容量与性能参考

| 指标 | 数值 |
|------|------|
| 单次全品种回测 (37品种) | ~6.6 MB |
| 单次少量品种 (5品种) | ~0.5–1 MB |
| 日均回测估算 (5–20次) | 3–130 MB/天 |
| 月增长 | 100–400 MB |
| 年增长 | 1–5 GB |
| 列表查询延迟 (SQLite) | <1ms |
| 详情打开延迟 (读 JSON) | 10–500ms (取决于文件大小) |

---

## 7. 回测引擎依赖说明

回测引擎复用 `laicai-bridge/double_top_backtest.py`，该文件已新增 `max_holding_days` 参数（默认 0 = 不限制，向后兼容）。

引擎在执行回测时需要读取 K 线数据：
```
market-data/exports/D1/*_kline.json
```

NAS 上这些文件通过 docker-compose volume 挂载：
```yaml
volumes:
  - ../market-data/exports:/app/market-data/exports
```

**如果 K 线数据未更新**，回测会返回 0 笔交易。需先通过 `data_sync` API 或 rsync 推送最新行情数据。

---

## 8. 已知限制与后续规划

| 项目 | 状态 | 说明 |
|------|------|------|
| 策略类型 | 仅 `double_top_short` | 后续可扩展多策略 |
| 数据库迁移 | 手动 ALTER | 后续可引入 alembic |
| 定期归档 | 未实现 | experiment.json 可压缩归档 (10:1) |
| 多用户并发 | SQLite WAL 模式 | 两人协作足够 |
| 环境变量 | 全部已预留 | `EXPERIMENTS_DIR` / `MARKET_DATA_DIR` / `DATABASE_PATH` |

---

## 9. 故障排查

**Q: 重建后打开回测中心白屏？**
A: 检查 `docker compose logs panel`，确认无 import 错误。前端是单文件，清浏览器缓存后重试。

**Q: 回测执行返回 500？**
A: 大概率是 K 线数据未挂载或为空。检查容器内 `/app/market-data/exports/D1/` 是否有 `*_kline.json` 文件：
```bash
docker exec laicai-panel ls /app/market-data/exports/D1/ | head -5
```

**Q: 创建会话报错 "no such column: source"？**
A: 数据库缺少 `source` 字段。执行 5.3 节的迁移命令。

**Q: 外部推送的实验在回测报告 Tab 看不到？**
A: `report_loader.py` 只扫描 `experiments/` 下的直接子目录。确保推送的实验在 `experiments/<exp_id>/experiment.json` 路径，不要嵌套在 `_sessions/` 里。
