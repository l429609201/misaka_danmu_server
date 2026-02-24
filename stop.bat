@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo ============================================
echo   Misaka Danmaku Server - 停止脚本 (Windows)
echo ============================================
echo.

set "ROOT_DIR=%~dp0"
set "PID_DIR=%ROOT_DIR%.pids"
set "KILLED=0"

:: ── 1. 通过 PID 文件停止已知进程 ──
if exist "%PID_DIR%\backend.pid" (
    set /p BACKEND_PID=<"%PID_DIR%\backend.pid"
    tasklist /fi "pid eq !BACKEND_PID!" 2>nul | findstr /i "python" >nul 2>&1
    if !errorlevel! equ 0 (
        echo [后端] 正在停止 PID !BACKEND_PID! ...
        taskkill /f /pid !BACKEND_PID! >nul 2>&1
        set /a KILLED+=1
    )
    del "%PID_DIR%\backend.pid" >nul 2>&1
)

if exist "%PID_DIR%\frontend.pid" (
    set /p FRONTEND_PID=<"%PID_DIR%\frontend.pid"
    tasklist /fi "pid eq !FRONTEND_PID!" 2>nul | findstr /i "node" >nul 2>&1
    if !errorlevel! equ 0 (
        echo [前端] 正在停止 PID !FRONTEND_PID! ...
        taskkill /f /pid !FRONTEND_PID! >nul 2>&1
        set /a KILLED+=1
    )
    del "%PID_DIR%\frontend.pid" >nul 2>&1
)

:: ── 2. 扫描并清理残留的后端进程 (python *src.main*) ──
echo.
echo [清理] 扫描残留的后端进程...
set "ORPHAN_COUNT=0"
for /f %%p in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*src.main*' } | Select-Object -ExpandProperty ProcessId"') do (
    echo     发现残留后端进程 PID %%p，正在终止...
    taskkill /f /pid %%p >nul 2>&1
    set /a ORPHAN_COUNT+=1
    set /a KILLED+=1
)
if !ORPHAN_COUNT! equ 0 (
    echo     未发现残留后端进程
)

:: ── 3. 扫描并清理残留的前端进程 (node *vite*) ──
echo [清理] 扫描残留的前端进程...
set "ORPHAN_COUNT=0"
for /f %%p in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like '*vite*' } | Select-Object -ExpandProperty ProcessId"') do (
    echo     发现残留前端进程 PID %%p，正在终止...
    taskkill /f /pid %%p >nul 2>&1
    set /a ORPHAN_COUNT+=1
    set /a KILLED+=1
)
if !ORPHAN_COUNT! equ 0 (
    echo     未发现残留前端进程
)

:: ── 4. 清理可能残留的 uvicorn 子进程 ──
echo [清理] 扫描残留的 uvicorn 子进程...
set "ORPHAN_COUNT=0"
for /f %%p in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*uvicorn*' } | Select-Object -ExpandProperty ProcessId"') do (
    echo     发现残留 uvicorn 进程 PID %%p，正在终止...
    taskkill /f /pid %%p >nul 2>&1
    set /a ORPHAN_COUNT+=1
    set /a KILLED+=1
)
if !ORPHAN_COUNT! equ 0 (
    echo     未发现残留 uvicorn 进程
)

:: ── 完成 ──
echo.
if !KILLED! gtr 0 (
    echo ============================================
    echo   已停止 !KILLED! 个进程
    echo ============================================
) else (
    echo ============================================
    echo   没有正在运行的服务进程
    echo ============================================
)

endlocal

