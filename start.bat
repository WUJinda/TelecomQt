@echo off
chcp 65001 >nul
title 来财 · 策略协作面板
cd /d D:\workstations\TelecomQt\backend
echo ============================================
echo   来财 · 策略协作面板 启动中...
echo   浏览器即将自动打开 http://localhost:8000
echo.
echo   停止服务: 直接关闭本窗口 (或按 Ctrl+C)
echo   切换品种/查看K线: 点页面里 "逐笔复盘"
echo ============================================
echo.
timeout /t 2 >nul
start "" http://localhost:8000
D:\workstations\TelecomQt\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --host 127.0.0.1 --reload
echo.
echo 服务已停止。按任意键关闭窗口。
pause >nul
