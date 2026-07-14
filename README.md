# 来财 · 策略协作面板

两人（你 + 朋友）异步协作的期货量化策略面板。把"口头 + 截图 + 聊天记录"式的协作，换成"同一个网页面板读写"。

> **当前进度：M1（看回测报告）+ M1.5（逐笔交易 K 线复盘）已完成。** 想法/计划页在 M2，鉴权/部署在 M3，一键回测在 M4。
> 完整设计见 [`docs/设计方案.md`](docs/设计方案.md)。

## 目录结构

```
TelecomQt/
├── docs/                     设计方案、experiment.json 字段约定
├── backend/                  FastAPI 后端（读 experiment.json，提供 API + 托管前端）
│   ├── app/
│   ├── data/experiments/     实验数据（LaiCai 导出的 experiment.json 放这里）
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                 单页 H5（Alpine + ECharts，CDN 依赖）
│   └── index.html
├── laicai-bridge/            LaiCai → experiment.json 的导出适配器
└── deploy/                   docker-compose + Cloudflare Tunnel 配置
```

## 一、本地启动（3 步，先看效果）

```bash
# 1. 装后端依赖
cd backend
pip install -r requirements.txt

# 2. 启动后端（同时托管前端）
uvicorn app.main:app --reload --port 8000

# 3. 浏览器打开
#    http://localhost:8000          → 前端面板（已带一份样例报告）
#    http://localhost:8000/docs     → API 文档（FastAPI 自带）
```

样例报告在 `backend/data/experiments/20260712_210518_double_top_short_baseline/experiment.json`，
盈亏数字来自你 LaiCai 仓库的真实回测（ag2606 / TA2609 那两笔）；「逐笔复盘」里的 K 线 + 布林带是**合成示意数据**（`backend/data/experiments/_tools/gen_mock_chart.py` 按真实开/平仓价反推），真实 K 线需用 LaiCai 的 `build_chart.py` 导出。打开就能看到指标卡、收益曲线、逐笔明细、逐笔 K 线复盘。

## 二、接入 LaiCai（让面板显示你的真实回测）

1. 把 [`laicai-bridge/emit_experiment.py`](laicai-bridge/emit_experiment.py) 复制到 LaiCai 的 `pyStrategy/self_strategy/`。
2. 在你的回测脚本末尾加两行（详见 [`laicai-bridge/README.md`](laicai-bridge/README.md)）：
   ```python
   from emit_experiment import emit_experiment
   emit_experiment(instruments=all_results, strategy_name="...",
                   strategy_type="...", direction="short",
                   params={...}, out_dir="~/Desktop/experiments")
   ```
3. 跑完回测后，把生成的 `<experiment_id>/experiment.json` 整个目录拷到 `backend/data/experiments/` 下，刷新面板即可。

字段约定见 [`docs/experiment-schema.md`](docs/experiment-schema.md)。

## 三、部署到 NAS（随时随地手机访问）

```bash
cd deploy
docker compose up -d --build       # 面板跑在 NAS 的 8000 端口，自动重启
```

公网访问用 Cloudflare Tunnel（免费、自动 HTTPS、不用公网 IP），步骤见
[`deploy/cloudflared/README.md`](deploy/cloudflared/README.md)。鉴权推荐用 Cloudflare Access（免费零信任，邮箱白名单即可）。

## 路线图

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 报告列表 + 详情（指标卡 / 收益曲线 / 逐笔交易明细） | ✅ |
| M1.5 | 逐笔交易 K 线复盘（交易选择器 + K线 + 布林三轨 + 开平仓/左峰标注 + 触发位 + 总结） | ✅ |
| M2 | 策略想法 + 交易计划 CRUD（SQLite） | 待做 |
| M3 | PWA + Cloudflare Access 鉴权 + NAS 上线 | 待做 |
| M4 | 一键回测（面板按钮调 LaiCai） | 待做 |
