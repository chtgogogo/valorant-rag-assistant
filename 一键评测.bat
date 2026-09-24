@echo off
chcp 65001 >nul
title 无畏契约智能对话小助手 - 一键评测

echo ========================================
echo   无畏契约智能对话小助手 - 效果评测
echo ========================================
echo.
echo   给检索和问答能力打分（Hybrid 全管线）：
echo   - 默认带生成：约 10 分钟（调大模型，含忠实度判分）
echo   - 建议在晚上/清晨低峰期跑：高峰期限流会污染数字
echo   报告自动保存在 backend\eval\reports\，跑完自动打开该文件夹
echo.

cd /d "%~dp0backend"

if exist "%~dp0.venv\Scripts\python.exe" (
    set PY=%~dp0.venv\Scripts\python.exe
) else (
    set PY=python
)

echo 开始评测（窗口会有滚动日志，别关，耐心等汇总）...
"%PY%" scripts\evaluate_rag.py --mode hybrid %1

echo.
echo 评测结束！汇总数字看上面，明细报告在 backend\eval\reports\ 最新一份
start "" "%~dp0backend\eval\reports"
pause
