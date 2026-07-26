# NAS 部署指南（darewin.icu）

> **零成本方案**：Cloudflare Tunnel + Zero Trust Access 均为免费计划（50 用户以内），无需信用卡、无需支付。

---

## 前提检查

| 条件 | 状态 |
|------|------|
| 域名 `darewin.icu` 已托管到 Cloudflare | 需你确认（见下方） |
| NAS 支持 Docker + docker compose | 需你确认 |
| 你和朋友的邮箱（用于 Access 鉴权） | 需准备 |

### 确认域名已在 Cloudflare

1. 打开 [Cloudflare Dashboard](https://dash.cloudflare.com)
2. 看左侧是否已有 `darewin.icu`
3. 如果**没有**：点 Add a Site → 输入 `darewin.icu` → 选 Free 计划 → 按提示到域名注册商把 Nameserver 改成 Cloudflare 给的两个 NS（如 `xxx.ns.cloudflare.com`）→ 等 10 分钟~24 小时生效

---

## 第 1 步：NAS 上启动面板

```bash
# 把项目同步到 NAS（rsync / scp / 网盘均可）
# 假设项目放在 NAS 的 /volume1/TelecomQt/

cd /volume1/TelecomQt/deploy
docker compose up -d --build panel

# 验证
curl http://localhost:8000/api/health
# 应返回 {"ok": true, "version": "0.1.0"}
```

此时面板已在 NAS 本地 `http://nas-ip:8000` 跑起来了。

---

## 第 2 步：创建 Cloudflare Tunnel（公网入口）

1. 打开 [Cloudflare Zero Trust](https://one.dash.cloudflare.com)
2. 左侧 → **Networks** → **Tunnels** → **Create a tunnel**
3. 选 **Cloudflared** → 给 tunnel 命名（如 `laicai-panel`）
4. 页面会显示一个 **token**，复制它（形如 `eyJh...` 很长一串）

---

## 第 3 步：启动 Tunnel 容器

```bash
# 回到 NAS 终端
cd /volume1/TelecomQt/deploy

# 把 token 填进去（三种方式任选其一）

# 方式 A：环境变量（推荐）
export TUNNEL_TOKEN="eyJh...你复制的token..."
docker compose up -d cloudflared

# 方式 B：写 .env 文件
echo 'TUNNEL_TOKEN=eyJh...你复制的token...' > .env
docker compose up -d cloudflared

# 方式 C：直接改 docker-compose.yml（不推荐，会暴露 token）
```

---

## 第 4 步：配置 Tunnel 路由

回到 Cloudflare Tunnel 页面（第 2 步那个），继续配置：

1. **Public Hostname** 标签 → Add a public hostname
2. 填写：
   - Subdomain: `panel`
   - Domain: `darewin.icu`
   - Service: `HTTP`
   - URL: `panel:8000`（注意是容器名 `panel`，不是 localhost）
3. Save

现在 `https://panel.darewin.icu` 应该能打开面板了（HTTPS 由 Cloudflare 自动签发）。

> ⚠ 如果 Tunnel 页面路由配的是 `localhost:8000`，改成 `panel:8000`——因为 cloudflared 和 panel 是同一 docker compose 网络里的两个容器，容器间用服务名通信。

---

## 第 5 步：配置 Access 鉴权（防止任何人看到你的策略数据）

这一步**必须做**——否则拿到链接的人都能看到面板内容。

1. 在 Zero Trust 左侧 → **Access** → **Applications** → **Add an application**
2. 选 **Self-hosted**
3. 填写：
   - Application name: `来财面板`
   - Session Duration: `24 hours`（之后要重新验证邮箱）
   - Application domain: `panel.darewin.icu`
4. Next → 创建 Policy：
   - Policy name: `允许访问`
   - Action: **Allow**
   - Include → Emails → 填 **你和朋友的邮箱**（如 `darewin@gmail.com`）
5. Save

**效果**：访问 `https://panel.darewin.icu` 时，Cloudflare 会先弹一个邮箱验证页 → 填邮箱 → 收到 6 位验证码 → 输入后进入面板。你和朋友各验证一次，之后 24 小时内免登录。

> 这个邮箱 OTP 验证方式叫 **One-time PIN**，是 Cloudflare 内置的，不需要接第三方登录，完全免费。

---

## 验收清单

| 检查项 | 方法 |
|--------|------|
| 面板本地可访问 | `curl http://localhost:8000/api/health` |
| 公网 HTTPS 可访问 | 手机打开 `https://panel.darewin.icu` |
| 鉴权生效 | 未验证邮箱时应看到 Cloudflare 登录页 |
| 报告正常 | 面板能看到实验报告 + K 线复盘 |
| 品种目录正常 | 面板品种页能列出 25 个品种 |
| 想法/计划 CRUD | 能创建和查看策略想法 |
| 朋友能访问 | 朋友用他的邮箱验证后进入 |

---

## 日常运维

```bash
# 更新面板代码后重新构建
cd /volume1/TelecomQt/deploy
docker compose up -d --build panel

# 更新行情数据（在本地电脑跑 fetch 后，把 exports 同步到 NAS）
rsync -avz market-data/exports/ nas:/volume1/TelecomQt/market-data/exports/

# 查看日志
docker compose logs -f panel
docker compose logs -f cloudflared

# 停止
docker compose down
```

---

## 常见问题

**Q: Zero Trust 要求绑信用卡怎么办？**
A: 免费计划通常不要求。如果弹出绑卡页面，检查你是否选的是 "Zero Trust Free" 而不是付费计划。社区报告有时新账号会被要求绑卡验证，但不扣费。

**Q: Tunnel 连不上？**
A: 检查 `docker compose logs cloudflared`，确认 token 正确。路由里的 service URL 要用 `panel:8000`（Docker 内部网络），不是 `localhost:8000`。

**Q: 朋友打不开？**
A: 确认他的邮箱已加入 Access Policy 的白名单。他第一次访问会收到验证码邮件。
