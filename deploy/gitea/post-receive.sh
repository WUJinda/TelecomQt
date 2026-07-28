#!/bin/bash
# ============================================================
# Gitea post-receive hook — 自动部署脚本
# ------------------------------------------------------------
# 安装方式：
#   Gitea Web UI → 仓库设置 → Git Hooks → post-receive
#   把本文件内容完整粘贴进去，保存即可。
#
# 功能：
#   push 到 main 分支时，自动 checkout 代码到部署目录，
#   然后执行 docker compose up --build 重建面板容器。
# ============================================================

set -e

# 项目部署目录（必须与 docker-compose.yml 中的挂载路径一致）
DEPLOY_DIR=/share/TelecomQt

while read oldrev newrev refname; do
    # 只在 main 分支触发部署
    if [ "$refname" = "refs/heads/main" ]; then
        echo ""
        echo "========================================"
        echo "🚀 收到 main 分支推送，开始部署..."
        echo "========================================"

        # 1. Checkout 最新代码到部署目录
        mkdir -p "$DEPLOY_DIR"
        git --work-tree="$DEPLOY_DIR" checkout -f main
        echo "✅ 代码已更新到最新版本"

        # 2. 重新构建并启动面板（只重建 panel，不影响 cloudflared）
        cd "$DEPLOY_DIR/deploy"
        docker compose up -d --build panel
        echo "✅ Docker 镜像已重建，容器已重启"

        # 3. 健康检查（等待容器启动）
        echo "⏳ 等待健康检查..."
        sleep 8
        if docker compose exec -T panel \
            curl -sf http://localhost:8000/api/health >/dev/null 2>&1; then
            echo "✅ 健康检查通过 — 部署成功！"
        else
            echo "⚠️  健康检查未通过"
            echo "   请检查日志：cd $DEPLOY_DIR/deploy && docker compose logs --tail=50 panel"
        fi

        echo "========================================"
        echo "🎉 部署完成 $(date)"
        echo "========================================"
        echo ""
    else
        echo "ℹ️  非主分支推送 ($refname)，跳过部署"
    fi
done
