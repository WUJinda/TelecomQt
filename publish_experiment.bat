@echo off
REM ============================================================
REM 回测结果一键推送到 NAS 面板
REM ============================================================
REM 用法：
REM   publish_experiment.bat                                    推送最新一次回测
REM   publish_experiment.bat 20260726_215242_double_top_short_baseline   推送指定实验
REM
REM 前提：market-data/.env 已配置 PANEL_HOST 和 DEPLOY_TOKEN
REM ============================================================

cd /d "%~dp0..\market-data"

if "%~1"=="" (
    REM 没有参数 → 自动找最新一次回测
    echo 正在查找最新回测结果...
    for /f "delims=" %%i in ('dir /b /ad /o-n "..\backend\data\experiments" ^| findstr /v "^_"') do (
        set "LATEST=%%i"
        goto found
    )
    echo 未找到任何回测结果（backend\data\experiments\ 目录为空）
    exit /b 1

    :found
    echo 最新回测：%LATEST%
    set "EXP_DIR=%LATEST%"
) else (
    set "EXP_DIR=%~1"
)

set "EXP_FILE=..\backend\data\experiments\%EXP_DIR%\experiment.json"

if not exist "%EXP_FILE%" (
    echo 文件不存在：%EXP_FILE%
    echo 可用实验：
    dir /b /ad "..\backend\data\experiments" | findstr /v "^_"
    exit /b 1
)

echo.
echo 推送回测结果到 NAS...
echo   实验 ID：%EXP_DIR%
echo   文件：   %EXP_FILE%
echo.

python sync_to_nas.py --experiment "%EXP_FILE%"

if %errorlevel%==0 (
    echo.
    echo ✅ 推送成功！
    echo    访问 https://panel.darewin.icu 查看面板
) else (
    echo.
    echo ❌ 推送失败，请检查错误信息
)

pause
