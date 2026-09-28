@echo off
taskkill /IM OpenScrcpy.exe /F >nul 2>&1
if %errorlevel%==0 (
    echo OpenScrcpy 服务已停止
) else (
    echo 未发现运行中的 OpenScrcpy.exe
)
pause
