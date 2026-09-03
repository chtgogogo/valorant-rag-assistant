@echo off
start "Backend" cmd /c "cd /d backend && python main.py"
start "Frontend" cmd /c "cd /d frontend && npm run dev"
echo 启动完成！
pause