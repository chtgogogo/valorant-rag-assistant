@echo off
chcp 65001 >nul
title 无畏契约知识库 - 重新索引

echo ========================================
echo   无畏契约智能对话小助手 - 重新索引
echo ========================================
echo.

cd /d "%~dp0backend"

echo [1/2] 启动后端...
start "Backend" /B python main.py > NUL 2>&1

echo 等待后端启动...
timeout /t 8 /nobreak > NUL

echo.
echo [2/2] 开始重新上传文档并索引...
python -c "
import requests, os, time, glob

base = 'http://127.0.0.1:8000'
uploads_dir = 'data/uploads'
kb_id = 'valorant'

# 找到所有文档
files = sorted(glob.glob(os.path.join(uploads_dir, '*.md'))) + \
        sorted(glob.glob(os.path.join(uploads_dir, '*.txt')))

if not files:
    print('没有找到文档！请把 .md/.txt 文件放到 backend/data/uploads/ 目录下。')
    exit(1)

total = 0
for fpath in files:
    fname = os.path.basename(fpath)
    print(f'  上传: {fname} ... ', end='', flush=True)
    with open(fpath, 'rb') as f:
        r = requests.post(f'{base}/api/document/upload',
                          files={'file': f},
                          params={'kb_id': kb_id},
                          timeout=120)
    d = r.json()
    if d.get('code') == 200:
        n = d['data']['chunk_count']
        total += n
        print(f'完成 ({n} 块)')
    else:
        print(f'失败: {d.get(\"msg\", d)}')
    time.sleep(0.5)

print()
print(f'====================')
print(f'全部完成! 共 {len(files)} 份文档, {total} 个文本块已入库。')
print(f'====================')
"

echo.
echo 索引完成！前端访问 http://localhost:5173
pause
