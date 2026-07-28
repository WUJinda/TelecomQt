# 来财面板 — 部署总览（DeployREADME）

> **本文档用途**：交代项目全貌和部署背景，拿着这份文档到 NAS 所在网络环境的任何电脑上，都能独立完成部署。

---

## 一、项目是什么

**来财 · 策略协作面板** — 两人（你 + 朋友）异步协作的期货量化策略面板。把"口头 + 截图 + 聊天记录"式的协作，换成"同一个网页面板读写"。

### 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 后端 | FastAPI（Python 3.12） | 读 `experiment.json`，提供 REST API + 托管前端静态文件 |
| 前端 | Alpine.js + ECharts | 单页 H5，依赖已本地 vendored，无需 CDN |
| 数据 | AKShare + LaiCai 适配器 | 从交易所拉真实 K 线行情，LaiCai 回测导出 experiment.json |
| 部署 | Docker + docker-compose | 后端打包为 Docker 镜像，前端由后端托管 |
| 公网入口 | Cloudflare Tunnel | 免公网 IP、自动 HTTPS，通过 `panel.darewin.icu` 访问 |

### 当前进度

- ✅ M1：看回测报告（指标卡、收益曲线、逐笔明细）
- ✅ M1.5：逐笔交易 K 线复盘（真实行情 + 布林带）
- ✅ M2：策略想法 / 交易计划 CRUD
- 🚧 M3：鉴权 / 部署（进行中）
- ⬜ M4：一键回测

完整设计见 `docs/设计方案.md`。

---

## 二、目录结构

```
TelecomQt/
├── backend/                 FastAPI 后端
│   ├── app/                 应用代码（路由、模型、服务）
│   ├── data/                运行时数据（experiment.json、app.db）
│   │   └── experiments/     回测报告数据
│   ├── Dockerfile           后端 Docker 镜像定义
│   └── requirements.txt     Python 依赖
│
├── frontend/                单页 H5 前端
│   └── index.html           主页面（Alpine + ECharts）
│
├── market-data/             行情数据管线
│   ├── config.py            品种配置
│   └── exports/             导出的 JSON 行情数据
│
├── laicai-bridge/           LaiCai → experiment.json 适配器
│   ├── emit_experiment.py   生成实验数据
│   ├── build_chart.py       构建图表数据
│   └── fetch_kline.py       从 AKShare 拉真实 K 线
│
├── deploy/                  部署配置
│   ├── docker-compose.yml   面板 + Cloudflare Tunnel 编排
│   ├── cloudflared/         Tunnel 配置
│   ├── NAS-DEPLOY.md        手动 NAS 部署指南
│   ├── GITEA-DEPLOY.md      Gitea 一键自动部署方案 ← 新增
│   ├── DeployREADME.md      本文档
│   └── gitea/               Gitea 部署配置文件
│       ├── Dockerfile.gitea 自定义 Gitea 镜像
│       ├── docker-compose.yml Gitea 容器编排
│       └── post-receive.sh  自动部署 Git Hook 脚本
│
├── docs/                    设计文档
│   └──设计方案.md
│
└── start.bat                Windows 一键启动脚本
```

---

## 三、NAS 环境信息

| 项 | 值 |
|----|-----|
| NAS 型号 | 绿联 UGREEN DXP4800 PLUS 16GB |
| 操作系统 | UGOS Pro |
| Docker | ✅ 支持（Docker 管理器 / Compose 项目） |
| 网络 | 非本地开发机的局域网（需远程访问） |
| 端口映射 | Lucky（国产反向代理 + 动态 DNS） |
| 公网域名 | `darewin.icu`（Cloudflare 托管） |

---

## 四、部署方式对比

项目有两种部署方式，按需选择：

### 方式 A：手动部署（简单，首次推荐）

适合首次部署或临时更新。

- **文档**：`deploy/NAS-DEPLOY.md`
- **流程**：rsync 代码到 NAS → SSH / 终端执行 `docker compose up -d --build`
- **优点**：零额外基础设施，5 分钟搞定
- **缺点**：每次更新都要手动操作

### 方式 B：Gitea 一键自动部署（推荐）

适合持续开发，频繁更新。

- **文档**：`deploy/GITEA-DEPLOY.md`
- **流程**：本地 `git push nas main` → Gitea 自动触发部署
- **优点**：push 即部署，完整版本管理，可回滚
- **缺点**：需要一次性配置 Gitea（约 30 分钟）

### 决策建议

| 场景 | 推荐方式 |
|------|---------|
| 首次部署到 NAS | 先用 **方式 A** 把面板跑起来 |
| 持续开发，频繁更新 | 切换到 **方式 B** |
| 偶尔更新一次 | **方式 A** 足够 |

---

## 五、快速开始（从零部署）

### 前置条件

- [ ] NAS 已安装 Docker + docker compose
- [ ] 域名 `darewin.icu` 已托管到 Cloudflare
- [ ] Lucky 已安装并可用
- [ ] 本地项目代码可正常 `docker compose up`（先在本地验证）

### 第 1 步：部署面板本身

按 `deploy/NAS-DEPLOY.md` 的步骤 1 完成面板部署：

```bash
# 把项目代码放到 NAS 上（通过文件管理器 / rsync / 网盘同步）
# 假设项目放在 /share/TelecomQt/

cd /share/TelecomQt/deploy
docker compose up -d --build panel

# 验证
curl http://localhost:8000/api/health
# 应返回 {"ok": true, "version": "0.1.0"}
```

### 第 2 步：配置 Cloudflare Tunnel（公网访问）

按 `deploy/NAS-DEPLOY.md` 的步骤 2-5 完成公网访问配置。

如果你已经有了 Cloudflare Tunnel（`panel.darewin.icu` 已经能访问），跳过此步。

### 第 3 步（可选）：配置 Gitea 自动部署

按 `deploy/GITEA-DEPLOY.md` 的步骤 1-7 完成 Gitea 配置。

配置完成后，日常开发只需要：

```bash
git push nas main   # 自动部署
```

---

## 六、端口分配

| 端口 | 服务 | 说明 |
|------|------|------|
| 3000 | Gitea | 代码仓库 Web UI |
| 2222 | Gitea SSH | Git SSH（可选） |
| 8000 | 来财面板 | FastAPI 后端 + 前端 |
| 9000 | （预留） | Webhook 接收器（如需要） |

### Lucky 映射规划

| 内部地址 | 外部域名 | 用途 |
|----------|---------|------|
| `NAS_IP:8000` | Cloudflare Tunnel 已处理 | 面板公网访问 |
| `NAS_IP:3000` | `git.darewin.icu` | Gitea 代码仓库 |

---

## 七、数据说明

### 需要挂载的数据目录

| 容器内路径 | 宿主机路径 | 用途 |
|-----------|-----------|------|
| `/data/experiments/` | `backend/data/experiments/` | 回测报告（experiment.json） |
| `/data/analytics/` | `backend/data/analytics/` | 统计报告 |
| `/app/market-data/exports/` | `market-data/exports/` | 行情数据（品种日K JSON） |

这些目录通过 `docker-compose.yml` 的 volume 挂载，**不受镜像重建影响**。更新代码后数据不会丢失。

### 首次部署需要准备的数据

- 至少一份 `experiment.json` 回测报告（放在 `backend/data/experiments/` 下）
- 行情数据 JSON（放在 `market-data/exports/` 下，项目已内置 25 品种主力连续日K）

---

## 八、常用命令速查

### 面板运维

```bash
# 查看面板状态
cd /share/TelecomQt/deploy
docker compose ps

# 查看日志
docker compose logs -f panel          # 实时日志
docker compose logs --tail=100 panel   # 最近 100 行

# 重启面板
docker compose restart panel

# 重建面板（更新代码后）
docker compose up -d --build panel

# 停止所有服务
docker compose down
```

### Gitea 运维

```bash
# 查看 Gitea 状态
cd /share/TelecomQt/deploy/gitea
docker compose ps

# 查看 Gitea 日志
docker compose logs -f gitea

# 重启 Gitea
docker compose restart gitea

# 更新 Gitea 镜像
docker compose pull && docker compose up -d --build
```

### 本地开发

```bash
# 本地启动（无需 Docker）
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 浏览器打开
# http://localhost:8000        → 面板
# http://localhost:8000/docs   → API 文档
```

---

## 九、文档索引

| 文档 | 路径 | 内容 |
|------|------|------|
| 项目介绍 + 本地启动 | `README.md` | 项目概述、目录结构、本地开发指南 |
| 设计方案 | `docs/设计方案.md` | 完整的产品设计和技术架构 |
| 手动 NAS 部署 | `deploy/NAS-DEPLOY.md` | 面板手动部署到 NAS 的完整步骤 |
| Gitea 自动部署 | `deploy/GITEA-DEPLOY.md` | Gitea + post-receive hook 一键部署方案 |
| 部署总览 | `deploy/DeployREADME.md` | 本文档 — 项目全貌和部署背景 |

---

## 十、常见问题

**Q: 本地电脑和 NAS 不在同一局域网怎么办？**

A: 这就是 Gitea 方案的核心价值。通过 Lucky 把 Gitea 的端口映射到公网域名，本地通过 HTTPS push 代码即可，不需要 SSH 到 NAS。

**Q: 推送代码后多久能生效？**

A: push 后 Gitea 立即触发 post-receive hook，Docker 重建通常 30-60 秒（取决于代码变更量和网络速度）。

**Q: 部署失败怎么办？**

A: 查看 Gitea 的 push 输出日志（hook 脚本的输出会显示在 push 结果中），或 SSH/终端到 NAS 执行 `docker compose logs panel` 检查面板日志。

**Q: 数据会丢失吗？**

A: 不会。实验数据、行情数据都通过 volume 挂载，Docker 重建只更新代码，不碰数据目录。

**Q: 朋友怎么访问面板？**

A: 通过 `https://panel.darewin.icu`，已配置 Cloudflare Zero Trust Access 邮箱验证。详见 `deploy/NAS-DEPLOY.md` 第 5 步。
