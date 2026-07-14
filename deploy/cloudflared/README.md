# Cloudflare Tunnel 部署（NAS 公网访问）

用 Cloudflare Tunnel 把 NAS 上的面板（localhost:8000）安全暴露到公网，**不用公网 IP、不用开端口、自动 HTTPS**。

## 前提
- 你有自有域名，且已托管到 Cloudflare（在 Cloudflare 的 DNS 管理该域名）
- NAS 能装 `cloudflared`（群晖/威联通/Docker 都行）

## 方式一：临时快速验证（5 分钟，域名是随机的）

NAS 上面板已跑在 8000 端口后：

```bash
cloudflared tunnel --url http://localhost:8000
```

会打印一个 `https://xxx-xxx.trycloudflare.com` 临时地址，手机打开就能看。**关掉就失效**，只用来验证面板通不通。

## 方式二：固定子域名（长期用）

1. 登录授权（浏览器弹一次）：
   ```bash
   cloudflared tunnel login
   ```
2. 建一条隧道：
   ```bash
   cloudflared tunnel create laicai-panel
   ```
3. 写配置 `config.yml`（和本说明同目录有模板）：
   ```yaml
   tunnel: <上一步返回的隧道 UUID>
   credentials-file: /root/.cloudflared/<UUID>.json
   ingress:
     - hostname: panel.你的域名.com
       service: http://localhost:8000
     - service: http_status:404
   ```
4. 绑 DNS（自动在你的域名下建 CNAME）：
   ```bash
   cloudflared tunnel route dns laicai-panel panel.你的域名.com
   ```
5. 跑起来（建议用 Docker 或 systemd 守护，开机自启）：
   ```bash
   cloudflared tunnel run --config config.yml laicai-panel
   ```

手机访问 `https://panel.你的域名.com` 即可，HTTPS 由 Cloudflare 自动签发。

## 加鉴权（推荐：Cloudflare Access，免费、零信任）

在 Cloudflare Dashboard → Zero Trust → Access → Applications，把 `panel.你的域名.com` 加一个应用，
策略设成"邮箱白名单"（你和朋友的邮箱）。访问时 Cloudflare 让你邮箱验证一次，之后随便开。
**面板本身就不用写登录了**，安全交给 Cloudflare。

## 为什么不用 frp / 内网穿透自建
- Cloudflare Tunnel 免费、带宽够两人用、不用维护证书
- frp 需要一台有公网 IP 的中转机 + 自己管 Let's Encrypt 证书续期，运维更重
