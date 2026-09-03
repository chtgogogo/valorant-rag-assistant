@echo off
if exist ".venv\Scripts\python.exe" (
  start "Backend" cmd /c "cd /d backend && ..\.venv\Scripts\python.exe main.py"
) else (
  start "Backend" cmd /c "cd /d backend && python main.py"
)
start "Frontend" cmd /c "cd /d frontend && npm run dev"
echo 启动完成！
pause