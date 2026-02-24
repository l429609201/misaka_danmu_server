@echo off
chcp 65001 >nul 2>&1
setlocal

echo ============================================
echo   Misaka Danmaku Server - 启动脚本 (Windows)
echo ============================================
echo.

set "ROOT_DIR=%~dp0"
set "PID_DIR=%ROOT_DIR%.pids"
if not exist "%PID_DIR%" mkdir "%PID_DIR%"

:: ── 检查 Python ──
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 python，请确认已安装并加入 PATH
    pause
    exit /b 1
)

:: ── 检查 Node / npm ──
where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] 未找到 npm，将跳过前端启动
    set "SKIP_FRONTEND=1"
)

:: ── 启动后端 ──
echo [1/2] 正在启动后端服务...
cd /d "%ROOT_DIR%"
start /b "" python -m src.main > "%ROOT_DIR%config\logs\backend_console.log" 2>&1
:: 等待进程启动后获取 PID
timeout /t 2 /nobreak >nul
for /f "tokens=2" %%a in ('tasklist /fi "imagename eq python.exe" /fo list ^| findstr /i "PID"') do (
    set "BACKEND_PID=%%a"
)
:: 用 PowerShell 精确获取后端 PID
for /f %%p in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*src.main*' } | Select-Object -ExpandProperty ProcessId -First 1"') do (
    set "BACKEND_PID=%%p"
)
if defined BACKEND_PID (
    echo %BACKEND_PID%> "%PID_DIR%\backend.pid"
    echo     后端已启动 (PID: %BACKEND_PID%)，端口 7768
) else (
    echo     [警告] 后端似乎未成功启动，请检查日志: config\logs\backend_console.log
)

:: ── 启动前端 ──
if defined SKIP_FRONTEND (
    echo [2/2] 跳过前端启动 (未安装 npm)
    goto :done
)

echo [2/2] 正在启动前端开发服务器...
cd /d "%ROOT_DIR%web"
if not exist "node_modules" (
    echo     首次运行，正在安装依赖...
    call npm install
)
start /b "" cmd /c "npm run dev > \"%ROOT_DIR%config\logs\frontend_console.log\" 2>&1"
timeout /t 3 /nobreak >nul
for /f %%p in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like '*vite*' } | Select-Object -ExpandProperty ProcessId -First 1"') do (
    set "FRONTEND_PID=%%p"
)
if defined FRONTEND_PID (
    echo %FRONTEND_PID%> "%PID_DIR%\frontend.pid"
    echo     前端已启动 (PID: %FRONTEND_PID%)，端口 5173
) else (
    echo     [警告] 前端似乎未成功启动，请检查日志: config\logs\frontend_console.log
)

:done
echo.
echo ============================================
echo   启动完成!
echo   后端: http://127.0.0.1:7768
echo   前端: http://127.0.0.1:5173
echo   停止请运行: stop.bat
echo ============================================
cd /d "%ROOT_DIR%"
endlocal

