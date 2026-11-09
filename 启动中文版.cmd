@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo 未找到本地运行环境，请先完成安装。
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m data_mask_studio
endlocal
