# 来财面板 · NAS 完整部署教程

> **适用环境**：绿联 UGREEN NAS（UGOS Pro，Debian 内核）  
> **项目**：TelecomQt — 来财 · 策略协作面板  
> **技术栈**：Python 3.12 + FastAPI + SQLite · Alpine.js + ECharts（零构建前端）  
> **最终效果**：`git push` → NAS 自动拉取代码、重建镜像、重启面板，全程零停机  

---

## 目录

1. [架构总览](#1-架构总览)
2. [前置条件](#2-前置条件)
3. [部署面板服务](#3-部署面板服务)
4. [配置 Cloudflare Tunnel（公网 HTTPS）](#4-配置-cloudflare-tunnel公网-https)
5. [部署 Gitea（私有 Git 仓库）](#5-部署-gitea私有-git-仓库)
6. [配置自动部署（push → 重建 → 上线）](#6-配置自动部署push--重建--上线)
7. [多设备协作](#7-多设备协作)
8. [日常运维](#8-日常运维)
9. [故障排查](#9-故障排查)

---

## 1. 架构总览

```text
                         ┌─────────────────────────────────────────────┐
                         │              绿联 NAS (192.168.5.8)           │
                         │                                             │
  开发者电脑 ──git push──▶│  Gitea (3003)  ──post-receive hook──▶  部署  │
                         │       │                          git checkout │
                         │       │                          docker build │
                         │       ▼                               │       │
  公网用户 ──HTTPS──▶ Cloudflare ──tunnel──▶ laicai-panel:8000 ◀┘       │
                         │                    (FastAPI 应用)             │
                         │                                             │
                         │  MySQL (3306) ◀── Gitea 数据库               │
                         └─────────────────────────────────────────────┘
```

**核心组件**：

| 组件 | 容器名 | 端口 | 作用 |
|------|--------|------|------|
| 面板应用 | `laicai-panel` | 8000 | FastAPI 后端 + 前端静态页面 |
| Cloudflare 隧道 | `laicai-tunnel` | — | 将面板安全暴露到公网 HTTPS |
| Gitea | `gitea` | 3003 / 3004 | 私有 Git 仓库，接收代码推送 |
| MySQL | `mysql` | 3306 | Gitea 的数据库（NAS 已有） |

**Docker 网络**：

| 网络 | 成员 | 说明 |
|------|------|------|
| `deploy_default` | panel, cloudflared | 面板部署网络 |
| `mysql_default` | mysql, gitea | Gitea 通过容器名连接 MySQL |
| `gitea_default` | gitea | Gitea 默认网络 |

---

## 2. 前置条件

### 2.1 NAS 环境

```bash
# SSH 登录 NAS（替换为你的账号）
ssh YOUR_USER@NAS_IP

# 确认 Docker 和 Compose 版本
docker --version        # Docker 26.1.0+
docker compose version  # Compose v2.26+
```

### 2.2 需要的 Docker 镜像

```bash
# 提前拉取镜像（国内网络可能较慢，建议用镜像加速）
docker pull python:3.12-slim
docker pull cloudflare/cloudflared:latest
docker pull gitea/gitea:latest
```

> **国内镜像加速**：如果 NAS 已配置 `/etc/docker/daemon.json` 中的 `registry-mirrors`，直接 `docker pull` 即可。否则手动配置镜像源。

### 2.3 目录结构

```text
/volume2/docker/
├── telecomqt/              # 面板部署目录（代码 + Dockerfile）
│   ├── backend/
│   │   ├── app/            # FastAPI 应用代码
│   │   ├── data/           # 运行时数据（挂载到容器 /data）
│   │   │   ├── experiments/
│   │   │   └── analytics/
│   │   ├── Dockerfile      # 面板镜像构建文件
│   │   └── requirements.txt
│   ├── frontend/           # 静态前端（Alpine.js + ECharts）
│   ├── market-data/
│   │   ├── config.py
│   │   └── exports/
│   ├── deploy/
│   │   ├── docker-compose.yml  # 面板 + 隧道编排
│   │   └── .env                # Cloudflare Tunnel Token
│   └── .dockerignore       # 排除 .git/.venv/数据等
│
├── gitea/                  # Gitea 部署目录
│   ├── docker-compose.yaml
│   ├── Dockerfile.gitea    # 自定义镜像 = Gitea + Docker CLI
│   └── data/               # Gitea 持久化数据（仓库、配置）
│       ├── git/            # Git 仓库存储
│       ├── gitea/          # Gitea 配置
│       └── ssh/            # SSH 密钥
│
└── mysql/                  # MySQL（NAS 已有，Gitea 复用）
    └── ...
```

> **绿联 NAS 特殊说明**：Docker data-root 默认在 `/volume2/@docker`。通过 SFTP 上传文件时，`docker/` 映射到 `/volume2/docker/`（chroot 限制）。

### 2.4 域名准备

- 一个托管在 **Cloudflare** 的域名（如 `example.com`）
- 在 Cloudflare Zero Trust 面板中创建 Tunnel

---

## 3. 部署面板服务

### 3.1 上传项目代码

将项目代码上传到 NAS 的 `/volume2/docker/telecomqt/`。

**方式 A：通过 SFTP 上传**

```bash
# 从本地打包（排除不需要的文件）
cd /path/to/TelecomQt
tar czf telecomqt.tar.gz \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='backend/data/experiments' \
    --exclude='market-data/store' \
    .

# 通过 SFTP 上传到 NAS
sftp YOUR_USER@NAS_IP
> cd docker
> put telecomqt.tar.gz
> bye

# SSH 登录后解压
ssh YOUR_USER@NAS_IP
mkdir -p /volume2/docker/telecomqt
cd /volume2/docker/telecomqt
tar xzf /volume2/docker/telecomqt.tar.gz  # 或从 SFTP 上传目录解压
```

**方式 B：通过 Git 克隆**（如果已有远程仓库）

```bash
cd /volume2/docker
git clone https://github.com/YOUR/TelecomQt.git telecomqt
```

### 3.2 创建运行时数据目录

```bash
mkdir -p /volume2/docker/telecomqt/backend/data/experiments
mkdir -p /volume2/docker/telecomqt/backend/data/analytics
mkdir -p /volume2/docker/telecomqt/market-data/exports
```

### 3.3 （国内网络）优化 Dockerfile

国内拉取 PyPI 包可能超时，在 Dockerfile 中使用清华源：

```dockerfile
# 修改 pip install 行，添加清华 PyPI 源
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --timeout 120 \
    -r requirements.txt
```

如果 `python:3.12-slim` 镜像拉取困难，可改用全量镜像：

```dockerfile
FROM python:3.12      # 替换 python:3.12-slim
```

### 3.4 构建并启动面板

```bash
cd /volume2/docker/telecomqt/deploy

# 创建 .env 文件（如果使用 Cloudflare Tunnel）
# 先跳过，第 4 节配置
touch .env

# 构建并启动
docker compose up -d --build panel
```

### 3.5 验证面板

```bash
# 查看容器状态
docker ps --filter name=laicai-panel

# 健康检查
curl http://localhost:8000/api/health
# 期望输出：{"ok":true,"version":"0.1.0"}

# 测试 API 端点
curl http://localhost:8000/api/reports
curl http://localhost:8000/api/symbols   # 品种列表
curl http://localhost:8000/api/analytics # 分析报告

# 浏览器访问前端
# http://NAS_IP:8000
```

---

## 4. 配置 Cloudflare Tunnel（公网 HTTPS）

### 4.1 创建 Tunnel

1. 登录 [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)
2. **Networks** → **Tunnels** → **Create Tunnel**
3. 选择 **Cloudflared** 类型，命名（如 `laicai-panel`）
4. 复制生成的 **Tunnel Token**

### 4.2 配置 Public Hostname

在 Tunnel 的 **Public Hostname** 页面添加路由：

| 字段 | 值 |
|------|-----|
| Subdomain | `panel`（或你喜欢的子域名） |
| Domain | 选择你的域名 |
| Type | `HTTP` |
| URL | `panel:8000` |

> **关键**：Type 必须是 `HTTP`（不是 HTTPS），URL 用容器名 `panel:8000`（不是 `localhost:8000`）。因为 cloudflared 和 panel 在同一个 Docker 网络（`deploy_default`）中，通过容器名互访。

### 4.3 写入 Token 并启动隧道

```bash
cd /volume2/docker/telecomqt/deploy

# 写入 Tunnel Token
cat > .env << 'EOF'
TUNNEL_TOKEN=eyJhIjoieX...你的完整token...
EOF

# 启动隧道容器
docker compose up -d cloudflared
```

### 4.4 验证公网访问

```bash
# 检查隧道容器日志
docker logs laicai-tunnel

# 应看到类似输出：
# INF Registered tunnel connection ... connIndex=0 ... location=LAX
# INF Registered tunnel connection ... connIndex=1 ...

# 公网 HTTPS 测试（在任意联网设备上）
curl https://panel.your-domain.com/api/health
# 期望输出：{"ok":true,"version":"0.1.0"}
```

> **如果遇到 522 错误**：检查 Cloudflare 仪表盘中 Public Hostname 的 Type 和 URL 配置是否正确（Type=HTTP, URL=panel:8000）。

---

## 5. 部署 Gitea（私有 Git 仓库）

### 5.1 创建 Gitea 目录

```bash
mkdir -p /volume2/docker/gitea/data
cd /volume2/docker/gitea
```

### 5.2 自定义 Gitea 镜像

Gitea 的 post-receive hook 需要调用 Docker 命令来重建面板容器。因此我们在官方镜像基础上安装 Docker CLI。

创建 `Dockerfile.gitea`：

```dockerfile
FROM gitea/gitea:latest
USER root

# 使用清华 Alpine 源加速
RUN sed -i "s|dl-cdn.alpinelinux.org|mirrors.tuna.tsinghua.edu.cn|g" /etc/apk/repositories && \
    apk add --no-cache docker-cli docker-cli-compose

# 容器启动时自动设置 docker.sock 权限
RUN mkdir -p /etc/cont-init.d && \
    printf "#!/usr/bin/with-contenv bash\nchmod 666 /var/run/docker.sock 2>/dev/null || true\n" \
    > /etc/cont-init.d/zz-docker-sock && \
    chmod +x /etc/cont-init.d/zz-docker-sock
```

**为什么需要自定义镜像？**

- `docker-cli`：让 Gitea 的 git hook 能执行 `docker compose` 命令
- `docker-cli-compose`：提供 `docker compose` 子命令
- `zz-docker-sock`：Gitea 容器以 `git` 用户（UID 1000）运行，默认无权访问 `docker.sock`。s6 初始化脚本在容器启动时自动 `chmod 666` 解除限制

> **替代方案**：如果不想自定义镜像，可以在运行的容器内手动安装：先换清华源 `sed -i ...`，再 `apk add docker-cli docker-cli-compose`。但容器重建后需要重装，不持久化。

### 5.3 编写 docker-compose.yaml

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
      # MySQL 数据库连接（通过容器名访问，需要加入 MySQL 的网络）
      - GITEA__database__DB_TYPE=mysql
      - GITEA__database__HOST=mysql:3306
      - GITEA__database__NAME=gitea          # 需提前在 MySQL 中创建
      - GITEA__database__USER=gitea          # 数据库用户名
      - GITEA__database__PASSWD=YOUR_DB_PASSWORD
    group_add:
      - "121"   # 加入宿主机 docker 组（GID 可能不同，需确认）
    restart: always
    volumes:
      - ./data:/data
      # Docker socket：让 Gitea 能控制宿主机 Docker
      - /var/run/docker.sock:/var/run/docker.sock
      # 部署目录：容器内外路径必须一致（docker compose 的相对路径才能正确解析）
      - /volume2/docker/telecomqt:/volume2/docker/telecomqt
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    ports:
      - "3003:3000"   # Gitea Web UI
      - "3004:22"     # Git SSH（可选）
    networks:
      - mysql_net     # 加入 MySQL 网络，通过容器名连接数据库
      - default

networks:
  mysql_net:
    external: true
    name: mysql_default   # MySQL 容器所在的 Docker 网络名
```

**关键配置说明**：

| 配置 | 作用 |
|------|------|
| `build` 而非 `image` | 使用自定义镜像（安装了 docker-cli） |
| `docker.sock` 挂载 | Gitea 容器内可通过 socket 控制 NAS 的 Docker daemon |
| `telecomqt` 目录挂载 | Hook 脚本 checkout 代码的目录，路径容器内外一致 |
| `mysql_default` 网络 | 让 Gitea 通过容器名 `mysql:3306` 连接数据库（而非可能变化的 IP） |

> **如何确认 docker 组 GID**：在 NAS 上执行 `getent group docker`，输出的第三个字段就是 GID（如 `121`）。不同 NAS 可能不同。

> **如何确认 MySQL 网络名**：执行 `docker network ls`，找到 MySQL 容器所在的网络。

### 5.4 准备 MySQL 数据库

```sql
-- 在 MySQL 中创建 Gitea 专用数据库和用户
CREATE DATABASE gitea CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'gitea'@'%' IDENTIFIED BY 'YOUR_STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON gitea.* TO 'gitea'@'%';
FLUSH PRIVILEGES;
```

### 5.5 设置目录权限

Gitea 容器内 `git` 用户 UID=1000，需要对应的目录权限：

```bash
sudo chown -R 1000:1000 /volume2/docker/gitea/data/
```

### 5.6 构建并启动 Gitea

```bash
cd /volume2/docker/gitea

# 构建自定义镜像并启动（清华源，约 2 秒完成）
docker compose up -d --build
```

### 5.7 初始化 Gitea

首次启动后，访问 `http://NAS_IP:3003`：

1. **安装页面**：如果数据库已配好，Gitea 会自动初始化
2. **创建管理员账号**：也可通过命令行创建

```bash
# 通过命令行创建/修改管理员
docker exec -u git gitea gitea admin user create \
    --username admin \
    --password YOUR_PASSWORD \
    --email admin@example.com \
    --admin

# 或修改已有用户密码
docker exec -u git gitea gitea admin user change-password \
    --username admin \
    --password YOUR_PASSWORD
```

### 5.8 创建项目仓库

**方式 A：通过 Web UI**

访问 `http://NAS_IP:3003` → 右上角 `+` → 新建仓库 → 填写名称

**方式 B：通过 API**

```bash
curl -X POST "http://localhost:3003/api/v1/repos/user/TelecomQt" \
  -H "Authorization: token YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "TelecomQt", "private": true}'
```

---

## 6. 配置自动部署（push → 重建 → 上线）

### 6.1 原理

```text
git push main
    │
    ▼
Gitea bare 仓库接收代码
    │
    ▼ post-receive hook 触发
    │
    ├── git checkout -f main → /volume2/docker/telecomqt/  (更新部署目录代码)
    │
    ├── docker compose up -d --build panel                  (重建面板容器)
    │
    └── 健康检查                                             (验证部署结果)
```

### 6.2 安装 post-receive hook

在 Gitea bare 仓库的 hooks 目录写入 `post-receive` 脚本：

```bash
# 找到仓库 hooks 目录（容器内路径）
# 格式：/data/git/repositories/<user>/<repo>.git/hooks/

docker exec -u git gitea ls /data/git/repositories/admin/telecomqt.git/hooks/
```

写入 hook 内容：

```bash
docker exec -u git gitea bash -c 'cat > /data/git/repositories/admin/telecomqt.git/hooks/post-receive << '\''HOOKEOF'\''
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

        # 2. 重新构建并启动面板（只重建 panel，不影响隧道）
        cd "$DEPLOY_DIR/deploy"
        docker compose up -d --build panel
        echo "Docker rebuilt"

        # 3. 健康检查
        echo "Waiting for health check..."
        sleep 8
        if docker compose exec -T panel \
            curl -sf http://localhost:8000/api/health >/dev/null 2>&1; then
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
HOOKEOF'
```

设置可执行权限：

```bash
docker exec -u git gitea chmod +x /data/git/repositories/admin/telecomqt.git/hooks/post-receive
```

> **也可以通过 Gitea Web UI 安装**：仓库设置 → Git Hooks → post-receive → 粘贴脚本内容。

### 6.3 推送代码并验证

```bash
# 在开发电脑上添加 Gitea 远程
cd /path/to/TelecomQt
git remote add gitea http://NAS_IP:3003/admin/TelecomQt.git

# 推送代码
git push gitea main

# push 输出中应看到：
# remote: Deploying main branch...
# remote: Code updated
# remote: Docker rebuilt
# remote: Health check PASSED - deploy OK!
```

### 6.4 自动部署的运行机制

| 步骤 | 命令 | 说明 |
|------|------|------|
| 接收代码 | Git 内部 | Gitea 将提交写入 bare 仓库 |
| 触发 Hook | `post-receive` | Git 在接收完成后自动执行 |
| 更新代码 | `git checkout -f main` | 把 bare 仓库的代码展开到部署目录 |
| 重建容器 | `docker compose up --build` | 重新构建镜像，替换容器 |
| 健康检查 | `curl /api/health` | 验证新版本是否正常运行 |

**镜像构建优化**：如果没有代码变更（如空 commit），Docker 使用缓存层，构建秒级完成。修改 Python 依赖（requirements.txt）或应用代码时，才会触发对应层重建。

---

## 7. 多设备协作

### 7.1 HTTP 方式（推荐内网）

```bash
# 克隆仓库
git clone http://NAS_IP:3003/admin/TelecomQt.git

# 推送时输入 Gitea 账号密码
git push origin main
```

配置凭据缓存，避免每次输入密码：

```bash
# 缓存密码 1 小时
git config credential.helper 'cache --timeout=3600'

# 或永久存储（明文，仅限可信设备）
git config credential.helper store
```

### 7.2 SSH 方式（可选）

```bash
# 使用映射的 SSH 端口 3004
git clone ssh://git@NAS_IP:3004/admin/TelecomQt.git
```

需要先在 Gitea Web UI 添加你的 SSH 公钥（个人设置 → SSH/GPG 密钥）。

### 7.3 同时使用 GitHub 和 Gitea

```bash
# 添加多个远程
git remote add origin https://github.com/YOUR/TelecomQt.git
git remote add gitea http://NAS_IP:3003/admin/TelecomQt.git

# 推送到 GitHub（代码备份）
git push origin main

# 推送到 Gitea（触发自动部署）
git push gitea main

# 同时推送到两个远程
git remote set-url --add --push origin https://github.com/YOUR/TelecomQt.git
git remote set-url --add --push origin http://NAS_IP:3003/admin/TelecomQt.git
git push origin main  # 同时推送 GitHub + Gitea
```

---

## 8. 日常运维

### 8.1 更新面板代码

```bash
# 只需 push，全自动
git push gitea main
```

### 8.2 上传实验数据

```bash
# 方式 A：SFTP 上传到挂载目录
sftp YOUR_USER@NAS_IP
> put experiment.json docker/telecomqt/backend/data/experiments/
> put analysis.json docker/telecomqt/backend/data/analytics/

# 方式 B：通过 Git（小文件推荐）
# 把数据文件放入仓库，push 即自动同步到部署目录

# 上传后重启面板使其重新加载数据
docker restart laicai-panel
```

### 8.3 查看日志

```bash
# 面板日志
docker logs laicai-panel --tail 50
docker logs -f laicai-panel  # 实时跟踪

# 隧道日志
docker logs laicai-tunnel --tail 20

# Gitea 日志
docker logs gitea --tail 50
```

### 8.4 重启服务

```bash
# 重启面板
cd /volume2/docker/telecomqt/deploy
docker compose restart panel

# 重启 Gitea
cd /volume2/docker/gitea
docker compose restart gitea
```

### 8.5 查看容器状态

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

### 8.6 备份策略

| 数据 | 位置 | 备份方式 |
|------|------|---------|
| Gitea 仓库 | `/volume2/docker/gitea/data/git/` | 定期 tar 或 rsync |
| Gitea 数据库 | MySQL `gitea` 库 | `mysqldump gitea > backup.sql` |
| 面板实验数据 | `/volume2/docker/telecomqt/backend/data/` | 定期 tar |
| Cloudflare 配置 | Cloudflare Dashboard | 无需备份（云端） |

---

## 9. 故障排查

### 9.1 面板无法访问

```bash
# 1. 检查容器是否运行
docker ps --filter name=laicai-panel

# 2. 检查健康状态
docker inspect laicai-panel --format '{{.State.Health.Status}}'

# 3. 检查端口
curl http://localhost:8000/api/health

# 4. 查看日志
docker logs laicai-panel --tail 50
```

### 9.2 Cloudflare Tunnel 522 错误

522 = Cloudflare 连不到后端。检查：

1. **Public Hostname 配置**：Type 应为 `HTTP`，URL 应为 `panel:8000`（容器名，不是 localhost）
2. **容器网络**：`laicai-tunnel` 和 `laicai-panel` 应在同一个 Docker 网络（`deploy_default`）
3. **面板是否健康**：在 NAS 上 `curl http://localhost:8000/api/health`

```bash
# 检查网络
docker network inspect deploy_default | grep -A5 Containers

# 检查隧道是否能连到面板
docker exec laicai-tunnel wget -qO- http://panel:8000/api/health
```

### 9.3 Gitea 数据库连接失败

```bash
# 检查 Gitea 是否在 MySQL 网络中
docker network inspect mysql_default | grep gitea

# 检查 MySQL 容器名（Gitea 用容器名连接）
docker ps --filter name=mysql --format "{{.Names}}"

# 测试连接
docker exec gitea nc -zv mysql 3306
```

### 9.4 自动部署 Hook 不触发

```bash
# 1. 检查 hook 文件是否存在且有执行权限
docker exec -u git gitea ls -la \
    /data/git/repositories/admin/telecomqt.git/hooks/post-receive

# 2. 检查 hook 内容
docker exec -u git gitea cat \
    /data/git/repositories/admin/telecomqt.git/hooks/post-receive

# 3. 检查 docker CLI 是否在容器内可用
docker exec -u git gitea docker version

# 4. 检查 docker.sock 权限
ls -la /var/run/docker.sock
# 应为 srw-rw-rw-（666 权限）

# 5. 手动测试 hook 逻辑
docker exec -u git gitea docker ps
# 应能列出宿主机所有容器
```

### 9.5 pip install 超时（构建面板镜像慢）

```dockerfile
# 在 Dockerfile 的 pip install 行添加清华源和超时设置
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --timeout 120 \
    -r requirements.txt
```

### 9.6 Gitea 容器重建后丢失 docker-cli

**现象**：`docker exec gitea docker version` 报 `command not found`

**原因**：直接用 `image: gitea/gitea:latest` 而非 `build`，容器重建后 Alpine 包丢失

**解决**：确保 docker-compose.yaml 中使用 `build` 而非 `image`：

```yaml
services:
  gitea:
    build:           # ✅ 使用自定义镜像
      context: .
      dockerfile: Dockerfile.gitea
    # image: gitea/gitea:latest  # ❌ 不要用这种方式
```

---

## 附录：端口与网络快速参考

| 端口 | 服务 | 访问方式 |
|------|------|---------|
| 8000 | 面板 (FastAPI) | `http://NAS_IP:8000` |
| 3003 | Gitea Web UI | `http://NAS_IP:3003` |
| 3004 | Gitea SSH | `ssh://git@NAS_IP:3004` |
| 3306 | MySQL | 仅容器内部访问 |

| Docker 网络 | 成员 |
|-------------|------|
| `deploy_default` | laicai-panel, laicai-tunnel |
| `mysql_default` | mysql, gitea |
| `gitea_default` | gitea |

---

*本教程基于绿联 UGREEN DXP4800 PLUS (UGOS Pro) 环境编写，适用于大多数 Docker-capable NAS 设备。*
