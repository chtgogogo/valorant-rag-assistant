@echo off
chcp 65001 >nul
title 无畏契约知识库 - 重建索引

echo ========================================
echo   无畏契约智能对话小助手 - 重建索引
echo ========================================
echo.

cd /d "%~dp0backend"

echo [1/2] 检查 Python 虚拟环境...
if exist "%~dp0.venv\Scripts\python.exe" (
    set PY=%~dp0.venv\Scripts\python.exe
) else (
    set PY=python
)

echo [2/2] 开始从 knowledge_base 同步并向量化...
"%PY%" scripts\init_knowledge_base.py

echo.
echo 索引完成！前端访问 http://localhost:5174
pause
