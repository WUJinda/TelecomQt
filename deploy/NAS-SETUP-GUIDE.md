# NAS 部署与自动化运维指南

> **本文档记录了 TelecomQt 面板在绿联 NAS 上的实际部署情况，包括 Gitea 代码仓库配置和 push 即部署的自动化链路。**
> 拿着这份文档，在任何一台电脑上都能理解整套架构并执行代码更新与部署。

---

## 一、架构总览

```text
                    ┌──────────────────────────────────────────────────────┐
                    │              绿联 NAS (192.168.5.8)                   │
                    │                                                      │
  任意电脑 ──push──▶│  Gitea (3003)  ──post-receive hook──▶  Docker Build   │
                    │       │                                      │        │
                    │       │                               laicai-panel     │
                    │       │                                  :8000         │
                    │  MySQL (3306)                            │             │
                    │                                    laicai-tunnel        │
                    └────────────────────────────┬─────────────────────────┘
                                                 │
                                          Cloudflare Tunnel
                                                 │
                                    https://panel.darewin.icu
```

**核心流程**：开发者从任何电脑 `git push` → Gitea 收到推送 → 触发 post-receive hook → 自动 checkout 代码 → docker compose 重建面板容器 → 部署完成。

---

## 二、环境信息

### NAS 硬件与系统

| 项 | 值 |
|----|-----|
| NAS 型号 | 绿联 UGREEN DXP4800 PLUS 16GB |
| 操作系统 | UGOS Pro (Linux 6.12.30 x86_64) |
| Docker | 26.1.0 |
| Docker Compose | v2.26.1 |
| 存储 | `/volume1` (3.6TB) + `/volume2` (443G) |
| Docker data-root | `/volume2/@docker` |

### 网络与域名

| 项 | 值 |
|----|-----|
| NAS 内网 IP | `192.168.5.8` |
| 面板公网域名 | `https://panel.darewin.icu` |
| 域名托管 | Cloudflare |
| 公网入口 | Cloudflare Tunnel（LAX 节点） |
| 访问鉴权 | Cloudflare Zero Trust Access（邮箱 OTP） |

### Docker 网络拓扑

| 网络 | 说明 | 关键容器 |
|------|------|---------|
| `deploy_default` | 面板 + 隧道网络 | `laicai-panel` (172.19.0.2:8000), `laicai-tunnel` (172.19.0.3) |
| `mysql_default` | MySQL 网络（外部） | `mysql` (172.18.0.2:3306), `gitea` |

---

## 三、服务清单与端口

### 运行中的容器

| 容器名 | 镜像 | 端口 | 用途 |
|--------|------|------|------|
| `laicai-panel` | `deploy-panel`（本地构建） | 8000:8000 | FastAPI 后端 + 前端静态文件 |
| `laicai-tunnel` | `cloudflare/cloudflared:latest` | — | Cloudflare Tunnel，公网入口 |
| `gitea` | `gitea-gitea`（本地构建） | 3003:3000, 3004:22 | Gitea 代码仓库 + 自动部署 |
| `mysql` | MySQL | 3306 | Gitea 数据库 |

> NAS 上还运行其他无关容器（qbittorrent, jellyfin, homeassistant 等），不受本项目影响。

---

## 四、目录结构（NAS 实际路径）

```text
/volume2/docker/
├── telecomqt/                      # 面板部署目录
│   ├── backend/
│   │   ├── Dockerfile              # 面板镜像定义
│   │   ├── app/                    # FastAPI 应用代码
│   │   ├── data/                   # 运行时数据（volume 挂载，不受重建影响）
│   │   │   ├── experiments/        # 回测报告 (experiment.json)
│   │   │   └── analytics/          # 统计分析报告
│   │   └── requirements.txt
│   ├── frontend/                   # 单页 H5 前端
│   ├── market-data/
│   │   ├── config.py               # 品种配置
│   │   └── exports/                # 行情数据 JSON
│   ├── deploy/
│   │   ├── docker-compose.yml      # 面板 + 隧道编排
│   │   └── .env                    # TUNNEL_TOKEN（不入 git）
│   └── ...
│
└── gitea/                          # Gitea 配置目录
    ├── docker-compose.yaml         # Gitea 容器编排
    ├── Dockerfile.gitea            # 自定义 Gitea 镜像
    └── data/                       # Gitea 持久化数据
        ├── git/repositories/       # Git 仓库
        │   └── admin/telecomqt.git/
        │       └── hooks/post-receive  # 自动部署 hook
        └── gitea/conf/app.ini      # Gitea 配置
```

> **注意**：部署目录是 `/volume2/docker/telecomqt/`（volume2，不是 volume1）。

---

## 五、配置文件详解

### 5.1 面板 docker-compose.yml

**位置**：`/volume2/docker/telecomqt/deploy/docker-compose.yml`

```yaml
services:
  panel:
    build:
      context: ..
      dockerfile: backend/Dockerfile
    container_name: laicai-panel
    volumes:
      - ../backend/data:/data          # 实验数据、分析报告、app.db
      - ../market-data/exports:/app/market-data/exports  # 行情数据
    ports:
      - "8000:8000"
    restart: always
    environment:
      - EXPERIMENTS_DIR=/data/experiments
      - MARKET_DATA_DIR=/app/market-data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=4).status==200 else 1)"]
      interval: 30s
      timeout: 5s
      start_period: 10s
      retries: 3

  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: laicai-tunnel
    restart: always
    command: tunnel run
    environment:
      - TUNNEL_TOKEN=${TUNNEL_TOKEN:-REPLACE_WITH_YOUR_TOKEN}
    depends_on:
      panel:
        condition: service_healthy
```

**关键说明**：
- `TUNNEL_TOKEN` 从同目录下的 `.env` 文件读取，该文件不入 git
- `backend/data` 通过 volume 挂载，镜像重建不会丢失实验数据
- `market-data/exports` 同步挂载，行情数据可独立更新

### 5.2 面板 Dockerfile

**位置**：`/volume2/docker/telecomqt/backend/Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app /app/app
COPY frontend /app/frontend
COPY market-data/config.py /app/market-data/config.py
COPY market-data/exports /app/market-data/exports

ENV FRONTEND_DIR=/app/frontend
ENV EXPERIMENTS_DIR=/data/experiments
ENV ANALYTICS_DIR=/data/analytics
ENV MARKET_DATA_DIR=/app/market-data
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

> **国内网络适配**：如果 `pip install` 超时，在 Dockerfile 的 `RUN pip install` 行末尾追加：
> ```
> -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120
> ```

### 5.3 Gitea docker-compose.yaml

**位置**：`/volume2/docker/gitea/docker-compose.yaml`

```yaml
services:
  gitea:
    build:
      context: .
      dockerfile: Dockerfile.gitea
    container_name: gitea
    environment:
      - USER_UID=1000
      - USER_GID=1000
      - GITEA__database__DB_TYPE=mysql
      - GITEA__database__HOST=mysql:3306        # 通过容器名连接 MySQL
      - GITEA__database__NAME=gitea
      - GITEA__database__USER=gitea
      - GITEA__database__PASSWD=<你的数据库密码>
    group_add:
      - "121"       # 加入宿主机 docker 组，让 hook 能访问 docker.sock
    restart: always
    volumes:
      - ./data:/data
      - /var/run/docker.sock:/var/run/docker.sock       # Docker socket
      - /volume2/docker/telecomqt:/volume2/docker/telecomqt  # 部署目录（容器内外路径必须一致）
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    ports:
      - "3003:3000"   # Gitea Web UI
      - "3004:22"     # Git SSH（可选）
    networks:
      - mysql_net     # 外部网络，连接 MySQL
      - default

networks:
  mysql_net:
    external: true
    name: mysql_default
```

**关键设计**：
1. **加入 `mysql_default` 网络**：让 Gitea 通过容器名 `mysql:3306` 连接数据库，而不是写死 IP
2. **挂载 docker.sock**：让 Gitea 容器内的 hook 脚本能控制宿主机 Docker
3. **挂载部署目录**：hook checkout 出来的代码直接落到 `/volume2/docker/telecomqt/`，路径容器内外完全一致
4. **`group_add: ["121"]`**：宿主机 docker 组的 GID 是 121，加入此组让 git 用户有权访问 docker.sock

### 5.4 Gitea 自定义 Dockerfile

**位置**：`/volume2/docker/gitea/Dockerfile.gitea`

```dockerfile
FROM gitea/gitea:latest
USER root

# 用清华 Alpine 源安装 docker-cli（1.6 秒完成）
RUN sed -i "s|dl-cdn.alpinelinux.org|mirrors.tuna.tsinghua.edu.cn|g" /etc/apk/repositories && \
    apk add --no-cache docker-cli docker-cli-compose

# 容器启动时自动设置 docker.sock 权限
RUN mkdir -p /etc/cont-init.d && \
    printf "#!/usr/bin/with-contenv bash\nchmod 666 /var/run/docker.sock 2>/dev/null || true\n" > /etc/cont-init.d/zz-docker-sock && \
    chmod +x /etc/cont-init.d/zz-docker-sock
```

**为什么需要自定义镜像**：
- Gitea 官方镜像（Alpine）不含 docker 命令，无法在 hook 中执行 `docker compose`
- 安装 `docker-cli` + `docker-cli-compose` 让容器能通过挂载的 docker.sock 控制宿主机
- s6 初始化脚本在容器启动时自动 chmod docker.sock，解决 Gitea 子进程的 supplementary groups 问题

### 5.5 post-receive Hook（自动部署核心）

**位置**：Gitea bare 仓库内 `/data/git/repositories/admin/telecomqt.git/hooks/post-receive`

> 也在项目源码中有副本：`deploy/gitea/post-receive.sh`

```bash
#!/bin/bash
set -e

DEPLOY_DIR=/volume2/docker/telecomqt

while read oldrev newrev refname; do
    if [ "$refname" = "refs/heads/main" ]; then
        echo ""
        echo "========================================"
        echo "Deploying main branch..."
        echo "========================================"

        # 1. Checkout 最新代码到部署目录
        mkdir -p "$DEPLOY_DIR"
        git --work-tree="$DEPLOY_DIR" --git-dir="$GIT_DIR" checkout -f main
        echo "Code updated"

        # 2. 重建面板容器
        cd "$DEPLOY_DIR/deploy"
        docker compose up -d --build panel
        echo "Docker rebuilt"

        # 3. 健康检查
        echo "Waiting for health check..."
        sleep 8
        if docker compose exec -T panel curl -sf http://localhost:8000/api/health >/dev/null 2>&1; then
            echo "Health check PASSED - deploy OK!"
        else
            echo "Health check FAILED"
        fi

        echo "========================================"
        echo "Deploy done $(date)"
        echo "========================================"
    else
        echo "Non-main branch ($refname), skip deploy"
    fi
done
```

**工作原理**：
1. Gitea 收到 `main` 分支的 push 后执行此 hook
2. `git --work-tree` 把最新代码 checkout 到部署目录（覆盖旧文件）
3. `docker compose up -d --build panel` 重建镜像并重启容器（Docker 层缓存使无变更时秒级完成）
4. 等待 8 秒后做健康检查

---

## 六、在其他电脑上使用

### 6.1 克隆仓库

```bash
# HTTP 方式（需输入 Gitea 用户名密码）
git clone http://192.168.5.8:3003/admin/TelecomQt.git

# 或如果配置了 SSH
git clone ssh://git@192.168.5.8:3004/admin/TelecomQt.git
```

**Gitea 登录凭据**：
- Web UI 地址：`http://192.168.5.8:3003`
- 用户名 / 密码：联系仓库管理员获取

### 6.2 配置远程仓库

如果已从 GitHub 克隆，添加 Gitea 作为部署远程：

```bash
cd TelecomQt

# 添加 NAS Gitea 远程
git remote add gitea http://192.168.5.8:3003/admin/TelecomQt.git

# 查看远程列表
git remote -v
# origin  → https://github.com/WUJinda/TelecomQt.git  (GitHub, 代码备份)
# gitea   → http://192.168.5.8:3003/admin/TelecomQt.git (NAS, 自动部署)
```

### 6.3 日常开发流程

```bash
# 1. 正常开发、提交代码
git add .
git commit -m "feat: 添加新功能"

# 2. 推送到 GitHub（版本备份）
git push origin main

# 3. 推送到 NAS Gitea（触发自动部署）
git push gitea main
# push 输出中会显示部署日志
```

> **同时推送到两个远程的快捷方式**：
> ```bash
> # 配置一次
> git remote set-url --add --push origin gitea
> git remote set-url --add --push origin https://github.com/WUJinda/TelecomQt.git
> # 之后只需
> git push origin main
> ```

### 6.4 验证部署

```bash
# 方法 1：查看 push 输出
# hook 的 echo 输出会显示在 git push 的 remote: 行中
# 看到 "Deploy done" 即为成功

# 方法 2：检查面板 API
curl http://192.168.5.8:8000/api/health
# 期望返回 {"ok":true,"version":"0.1.0"}

# 方法 3：浏览器访问
# 内网：http://192.168.5.8:8000
# 公网：https://panel.darewin.icu
```

---

## 七、数据管理

### 实验数据

回测报告放在 `backend/data/experiments/` 目录，通过 Docker volume 挂载到容器内 `/data/experiments/`。

```bash
# 上传新的实验数据到 NAS
scp experiment.json DAREWIN@192.168.5.8:/volume2/docker/telecomqt/backend/data/experiments/

# 或通过 SFTP（绿联 SFTP 的 docker 目录映射到 /volume2/docker/）
# 上传到 docker/telecomqt/backend/data/experiments/
```

### 分析报告

统计分析报告放在 `backend/data/analytics/`，同样通过 volume 挂载。

### 行情数据

行情 JSON 文件在 `market-data/exports/`，通过 volume 挂载。更新行情数据后无需重启容器（代码在请求时实时读取文件）。

> **重要**：以上三个数据目录都通过 `docker-compose.yml` 的 volume 挂载，镜像重建**不会丢失数据**。

---

## 八、运维操作

### 面板运维

```bash
# SSH 到 NAS 后
cd /volume2/docker/telecomqt/deploy

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f panel          # 实时跟随
docker compose logs --tail=100 panel   # 最近 100 行

# 手动重建（不通过 git push）
docker compose up -d --build panel

# 重启面板
docker compose restart panel

# 停止所有服务
docker compose down
```

### Gitea 运维

```bash
cd /volume2/docker/gitea

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f gitea

# 重启 Gitea
docker compose restart gitea

# 重新构建 Gitea 镜像（修改 Dockerfile.gitea 后）
docker compose up -d --build
```

### Cloudflare Tunnel 运维

Cloudflare Tunnel 配置在 [Zero Trust Dashboard](https://one.dash.cloudflare.com)：

1. **Networks → Tunnels**：查看隧道状态、连接节点
2. **Public Hostnames**：确认 `panel.darewin.icu` → `HTTP` → `panel:8000`
3. **Access → Applications**：管理邮箱白名单

### 更新 post-receive Hook

如果需要修改自动部署脚本：

```bash
# 方法 1：通过 Gitea Web UI
# 仓库 → Settings → Git Hooks → post-receive → 编辑 → Save

# 方法 2：直接在 NAS 上修改文件
sudo docker exec -u git gitea vi /data/git/repositories/admin/telecomqt.git/hooks/post-receive
```

---

## 九、故障排查

### push 后部署失败

```bash
# 1. 查看 push 输出中的 hook 日志
#    如果看到 "permission denied" → docker.sock 权限问题

# 2. SSH 到 NAS 检查
sudo docker exec -u git gitea docker ps   # Gitea 能否访问 Docker?
ls -la /var/run/docker.sock               # sock 权限应为 srw-rw-rw-

# 3. 如果 sock 权限不对
sudo chmod 666 /var/run/docker.sock
# 或重启 Gitea 容器（init 脚本会自动 chmod）
cd /volume2/docker/gitea && sudo docker compose restart gitea
```

### 面板无法访问

```bash
# 1. 检查容器状态
docker compose ps    # laicai-panel 应为 Up (healthy)

# 2. 检查日志
docker compose logs --tail=50 panel

# 3. 检查 Cloudflare Tunnel
docker compose logs --tail=20 cloudflared
# 应看到 "Registered tunnel connection" 而非错误

# 4. 公网访问需要邮箱验证（Cloudflare Access）
#    用白名单中的邮箱接收验证码
```

### Gitea 无法连接 MySQL

```bash
# 确认 MySQL 在运行
docker ps | grep mysql

# 确认 Gitea 在 mysql_default 网络上
docker network inspect mysql_default | grep gitea

# 如果 Gitea 容器重建后不在网络中
cd /volume2/docker/gitea && docker compose up -d --force-recreate
```

### Docker 构建很慢（国内网络）

```bash
# 1. Docker 镜像源已在 /etc/docker/daemon.json 配置
cat /etc/docker/daemon.json
# 应包含 registry-mirrors

# 2. pip install 超时 → 在 Dockerfile 中添加清华源
#    RUN pip install --no-cache-dir -r requirements.txt \
#        -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120

# 3. Alpine apk 慢 → 在 Dockerfile.gitea 中已配置清华源
```

---

## 十、从零重建（灾难恢复）

如果 NAS 上的配置全部丢失，按以下步骤重建：

### 步骤 1：部署面板

```bash
# 1. 从 GitHub 克隆代码到 NAS
git clone https://github.com/WUJinda/TelecomQt.git /volume2/docker/telecomqt

# 2. 创建数据目录
mkdir -p /volume2/docker/telecomqt/backend/data/experiments
mkdir -p /volume2/docker/telecomqt/backend/data/analytics
mkdir -p /volume2/docker/telecomqt/market-data/exports

# 3. 配置 Cloudflare Tunnel token
echo 'TUNNEL_TOKEN=<你的token>' > /volume2/docker/telecomqt/deploy/.env

# 4. 构建并启动
cd /volume2/docker/telecomqt/deploy
docker compose up -d --build
```

### 步骤 2：部署 Gitea

```bash
# 1. 把 deploy/gitea/ 目录上传到 NAS
mkdir -p /volume2/docker/gitea
# （从仓库的 deploy/gitea/ 复制 docker-compose.yaml、Dockerfile.gitea）

# 2. 确保 MySQL 已有 gitea 数据库和用户
docker exec -it mysql mysql -u root -p -e \
    "CREATE DATABASE IF NOT EXISTS gitea; \
     CREATE USER IF NOT EXISTS 'gitea'@'%' IDENTIFIED BY '<密码>'; \
     GRANT ALL ON gitea.* TO 'gitea'@'%';"

# 3. 构建 Gitea 镜像并启动
cd /volume2/docker/gitea
docker compose up -d --build

# 4. 等 30 秒后安装 Gitea（首次访问 Web UI）
# 浏览器打开 http://192.168.5.8:3003

# 5. 创建管理员账户和仓库
# 6. 配置 post-receive hook（见 5.5 节）
```

### 步骤 3：配置 Cloudflare Tunnel 路由

在 [Zero Trust Dashboard](https://one.dash.cloudflare.com) 中：
1. Networks → Tunnels → 选择隧道 → Public Hostnames
2. 添加：`panel.darewin.icu` → `HTTP` → `panel:8000`

---

## 十一、安全注意事项

1. **`.env` 文件不入 git**：包含 Cloudflare Tunnel token，只在 NAS 本地存在
2. **Gitea 数据库密码**：使用强密码，不要使用默认值
3. **Cloudflare Access**：公网访问需邮箱验证，防止未授权访问策略数据
4. **NAS SSH**：使用强密码或密钥认证，避免暴露到公网
5. **Gitea HTTP push**：内网使用，公网 push 建议通过 SSH（端口 3004）或 VPN

---

## 十二、快速参考卡

```bash
# === 日常开发 ===
git push origin main    # 推到 GitHub（版本备份）
git push gitea main     # 推到 NAS（触发自动部署）

# === 检查部署结果 ===
curl http://192.168.5.8:8000/api/health

# === SSH 到 NAS 后 ===
cd /volume2/docker/telecomqt/deploy
docker compose ps                    # 查看面板状态
docker compose logs -f panel         # 实时日志
docker compose up -d --build panel   # 手动重建

cd /volume2/docker/gitea
docker compose logs -f gitea         # Gitea 日志

# === 关键路径 ===
# 面板部署目录：/volume2/docker/telecomqt/
# Gitea 配置目录：/volume2/docker/gitea/
# Gitea Web UI：http://192.168.5.8:3003
# 面板内网：http://192.168.5.8:8000
# 面板公网：https://panel.darewin.icu
```
