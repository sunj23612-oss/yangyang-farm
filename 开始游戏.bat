@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PORT=8000"
set "TURSO_URL="
set "TURSO_TOKEN="

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没有找到 Python 3。
    echo 请先安装 Python 3，并勾选 Add Python to PATH。
    echo 安装地址：https://www.python.org/downloads/
    pause
    exit /b 1
)
if not exist "server.py" (
    echo [错误] 当前文件夹缺少 server.py，请完整保留 D:\洋洋农场。
    pause
    exit /b 1
)
if not exist "web\洋洋农场.html" (
    echo [错误] 当前文件夹缺少游戏页面，请完整保留 web 文件夹。
    pause
    exit /b 1
)

echo 正在启动洋洋农场，使用本文件夹中的 farm.db 保存本地进度。
echo 如果页面没有自动打开，请访问 http://127.0.0.1:8000/
start "" http://127.0.0.1:8000/
echo.
echo 游玩期间请不要关闭本窗口。关闭本窗口后，本地服务将停止。
echo 线上公网版不受影响：https://yangyang-farm.onrender.com/
echo.
python -u server.py
if errorlevel 1 (
    echo.
    echo [错误] 游戏服务已退出。若提示端口被占用，请关闭已启动的游戏窗口后重试。
    pause
)
