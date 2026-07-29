# 行情数据同步配置指南

> **目标**：本地电脑跑 AKShare 采集行情数据 → 一键推送到 NAS 面板，数据实时生效。

---

## 当前状态

代码层面的改动已经完成（已推送到 GitHub）：
- ✅ 后端新增了 `/api/data/sync` 数据上传 API
- ✅ 本地新增了 `sync_to_nas.py` 推送脚本

**还需要你在 NAS 和 Cloudflare 上完成以下配置**，整条链路才能跑通。

---

## 需要完成的操作（按顺序）

### 第 1 步：Cloudflare Tunnel 映射 Gitea（让外网能 push 代码到 NAS）

你的 Gitea 在 NAS 内网 `localhost:3003`，开发电脑在外网无法直接访问。
通过已有的 Cloudflare Tunnel 加一条路由即可。

1. 登录 [Cloudflare Zero Trust](https://one.dash.cloudflare.com)
2. **Networks → Tunnels** → 点击你现有的隧道（就是跑 `panel.darewin.icu` 的那条）
3. 切到 **Public Hostname** 标签 → **Add a public hostname**
4. 填写：

   | 字段 | 值 |
   |------|-----|
   | Subdomain | `git` |
   | Domain | `darewin.icu` |
   | Type | `HTTP` |
   | URL | `localhost:3003` |

5. Save

完成后，`https://git.darewin.icu` 就能打开你的 Gitea 了。

> **关于 Access 鉴权**：如果你的 Tunnel 全局配了 Cloudflare Access 邮箱验证，`git push` 时可能会被拦截。两个办法：
> - 在 Access → Applications 中给 `git.darewin.icu` 添加 bypass 策略（仅 Gitea 放行）
> - 或用 Gitea 自带的基本认证（push 时弹账号密码），跳过 Access
>
> **简单做法**：在 Access 中把 `git.darewin.icu` 排除在保护范围外（Gitea 本身有账号密码保护）。

### 第 2 步：本地配置 git remote（添加 Gitea 远程仓库）

> 这一步 Proma Agent 可以帮你执行，你只需要告诉我第 1 步完成了。

```bash
cd D:\workstations\TelecomQt

# 添加 Gitea 远程（外网通过 Cloudflare Tunnel）
git remote add gitea https://git.darewin.icu/admin/TelecomQt.git

# 验证
git remote -v
# origin → https://github.com/WUJinda/TelecomQt.git   (GitHub，代码备份)
# gitea  → https://git.darewin.icu/admin/TelecomQt.git (NAS，触发自动部署)
```

> **仓库路径确认**：上面的 `admin/TelecomQt` 取决于你在 Gitea 中创建仓库时的用户名和仓库名。如果你用的是其他用户名，改成实际的。
> **首次 push 需要输入 Gitea 的用户名和密码**（或访问令牌）。

### 第 3 步：推送代码到 NAS（部署新的数据同步 API）

```bash
# 推送到 NAS Gitea，触发自动部署
git push gitea main
```

push 输出中应看到：
```
remote: Deploying main branch...
remote: Docker rebuilt
remote: Health check PASSED - deploy OK!
```

> 这一推会重建 Docker 镜像（因为 requirements.txt 新增了 `python-multipart` 依赖），大约 1-2 分钟。

### 第 4 步：NAS 上配置 DEPLOY_TOKEN（保护数据同步 API）

数据同步 API 需要一个密码（DEPLOY_TOKEN）才能写入，两端配同一个。

**4.1 生成一个随机密钥**

随便在哪台电脑上运行：
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# 输出类似：k7Xm2pN9rTqZ3vB8wY4kLjH6fD...
```

**4.2 在 NAS 上修改 docker-compose.yml**

通过绿联 NAS 文件管理器：
1. 打开 UGOS Pro → 文件管理
2. 进入 `docker` → `telecomqt` → `deploy`
3. 右键编辑 `docker-compose.yml`

找到 `panel` 服务的 `environment`，加一行：

```yaml
    environment:
      - EXPERIMENTS_DIR=/data/experiments
      - MARKET_DATA_DIR=/app/market-data
      - DEPLOY_TOKEN=k7Xm2pN9rTqZ3vB8wY4kLjH6fD...    # ← 新增
```

> 完整路径：`/volume2/docker/telecomqt/deploy/docker-compose.yml`

**4.3 重启面板使配置生效**

在 NAS 终端（或通过 Gitea 容器终端）执行：
```bash
cd /volume2/docker/telecomqt/deploy
docker compose up -d panel
```

### 第 5 步：本地配置 .env（填入同一把密钥）

```bash
cd D:\workstations\TelecomQt\market-data
```

创建 `.env` 文件（**与第 4 步的 DEPLOY_TOKEN 完全一致**）：

```ini
# 推送目标地址
# 公网：https://panel.darewin.icu
# 内网（如果在 NAS 局域网内操作，更快）：http://192.168.5.8:8000
PANEL_HOST=https://panel.darewin.icu

# 与 NAS docker-compose.yml 中的 DEPLOY_TOKEN 完全一致
DEPLOY_TOKEN=k7Xm2pN9rTqZ3vB8wY4kLjH6fD...
```

### 第 6 步：验证完整链路

```bash
cd D:\workstations\TelecomQt\market-data

# 1. 先确认 exports 目录有数据（之前已经跑过 batch_export_all.py）
ls exports/D1/    # 应该有 25+ 个 *_kline.json 文件

# 2. 推送到 NAS
python sync_to_nas.py

# 期望输出：
# 准备推送 25 个文件到 https://panel.darewin.icu
# 上传中...
# ✅ 推送成功！上传文件：25 个
```

也可以通过 API 检查 NAS 上的数据状态：
```bash
# 浏览器打开（无需 token）
https://panel.darewin.icu/api/data/status
# 应返回 exports 目录下的文件清单
```

---

## 日常使用流程

全链路三条命令（本地 AKShare 采集 → 推送 NAS）：

```bash
cd D:\workstations\TelecomQt\market-data

# 1. 拉取最新日K（AKShare，免费无 token）
.venv\Scripts\python.exe batch_fetch_all.py

# 2. 导出为 JSON
.venv\Scripts\python.exe batch_export_all.py

# 3. 推送到 NAS（面板实时生效，无需重启）
python sync_to_nas.py
```

### 只推送部分品种

```bash
python sync_to_nas.py --files rb0 ag0 cu0
```

### 推送到内网（在 NAS 局域网时更快）

```bash
python sync_to_nas.py --host http://192.168.5.8:8000
```

### 推送实验数据（experiment.json）

```bash
python sync_to_nas.py --experiment ../backend/data/experiments/20260712_xxx/experiment.json
```

---

## 架构图

```
本地开发电脑                                NAS (192.168.5.8)
──────────────                             ─────────────────────

代码部署链路（已有）:
  git push gitea main ──────────────────→ Gitea (3003)
    │                                        │ post-receive hook
    │                                        ↓
    │                                     docker compose up --build
    │                                        ↓
    │                                     laicai-panel (8000)
    │                                        │
    │                                        ↓
    │ ┌───────────────────────────────────── Cloudflare Tunnel
    │ ↓                                        ↓
    │ git.darewin.icu                     panel.darewin.icu
    └─ HTTPS

数据同步链路（本次新增）:
  batch_fetch_all.py
    → store/daily/ (Parquet)
  batch_export_all.py
    → exports/D1/*.json
  sync_to_nas.py ────HTTP POST───→  /api/data/sync
    (Bearer Token)                    → 写入 exports/D1/
                                      → 面板实时读取（无需重建）
```

---

## 故障排查

### push 到 Gitea 失败

```
fatal: unable to access 'https://git.darewin.icu/...': ...
```

检查：
1. Cloudflare Tunnel 的 Public Hostname 是否配了 `git.darewin.icu → localhost:3003`
2. Gitea 容器是否在运行：NAS 上 `docker ps | grep gitea`
3. Cloudflare Access 是否拦截了请求（参考第 1 步的说明）

### sync_to_nas.py 报 403

```
❌ 认证失败（403）
   NAS 端未配置 DEPLOY_TOKEN 环境变量
```

检查：
1. NAS 的 docker-compose.yml 中 panel 服务是否加了 `DEPLOY_TOKEN`
2. 修改后是否重启了面板：`docker compose up -d panel`

### sync_to_nas.py 报 Token 无效

```
❌ 认证失败（403）
   Token 不正确
```

两边的 DEPLOY_TOKEN 不一致，确认本地 `.env` 和 NAS `docker-compose.yml` 中的值完全相同。

### sync_to_nas.py 报连接超时

```bash
# 测试面板是否可达
curl https://panel.darewin.icu/api/data/status

# 如果公网不通，换内网地址（需在 NAS 局域网内）
python sync_to_nas.py --host http://192.168.5.8:8000
```

### 推送成功但面板数据没更新

面板读取数据是实时的（每次 API 请求都读文件），不需要重启。如果没更新：
1. 检查 `/api/data/status` 确认文件确实写入了
2. 清浏览器缓存后刷新
