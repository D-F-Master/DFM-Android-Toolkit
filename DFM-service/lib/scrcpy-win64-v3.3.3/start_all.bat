@echo off
chcp 65001 >nul
echo ==========================================
echo 正在顺序启动设备投屏 (每台间隔 5 秒)...
echo ==========================================

:: 启动第 1 台 (hx1)
echo [1/4] 正在启动 hx1 (192.168.0.18)...
start "hx1" scrcpy.exe -s 192.168.0.18:5555
timeout /t 5 /nobreak >nul

:: 启动第 2 台 (hx2)
echo [2/4] 正在启动 hx2 (192.168.0.98)...
start "hx2" scrcpy.exe -s 192.168.0.98:5555
timeout /t 5 /nobreak >nul

:: 启动第 3 台 (dc1)
echo [3/4] 正在启动 dc1 (192.168.0.118)...
start "dc1" scrcpy.exe -s 192.168.0.118:5555
timeout /t 5 /nobreak >nul

:: 启动第 4 台 (dc2)
echo [4/4] 正在启动 dc2 (192.168.0.90)...
start "dc2" scrcpy.exe -s 192.168.0.90:5555
timeout /t 5 /nobreak >nul

echo.
echo ==========================================
echo 所有设备启动指令已发送完毕！
echo 窗口应该已经陆续弹出。
echo ==========================================
pause