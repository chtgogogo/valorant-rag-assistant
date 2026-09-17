@echo off
rem v15.2 修复：先切到 bat 所在目录——从任何位置双击/调用都不再因相对路径失效
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  start "Backend" cmd /k "cd backend && ..\.venv\Scripts\python.exe main.py"
) else (
  start "Backend" cmd /k "cd backend && python main.py"
)
start "Frontend" cmd /k "cd frontend && npm run dev"
echo 两个窗口已启动：Backend(8000) + Frontend(5173)
echo 后端约 5 秒就绪；窗口保持开着，报错时窗口不会闪退，能看到错误信息。
pause
