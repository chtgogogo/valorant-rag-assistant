# -*- coding: utf-8 -*-
"""
知识库初始化 / 重建脚本

作用：
1. 清空 valortant 知识库的向量集合与文档清单
2. 从项目根目录 knowledge_base/ 同步 .md 文档到 backend/data/uploads/
3. 逐个文档执行解析、切分、向量化入库

用法：
    cd backend
    python scripts/init_knowledge_base.py
"""
import os
import shutil
from pathlib import Path

import sys

# 脚本直接运行时，将 backend 目录加入模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb

from config.settings import PROJECT_ROOT, VECTOR_DB_PATH, UPLOAD_PATH, DATA_DIR
from services.document_service import upload_and_process


def main():
    # 支持指定领域：python scripts/init_knowledge_base.py [domain]（默认 valorant）
    domain = sys.argv[1] if len(sys.argv) > 1 else "valorant"
    kb_dir = PROJECT_ROOT / "knowledge_base" / domain if domain != "valorant" else PROJECT_ROOT / "knowledge_base"
    # valorant 保持旧布局（knowledge_base/ 根目录），其他领域用 knowledge_base/<domain>/
    upload_dir = Path(UPLOAD_PATH)

    if not kb_dir.exists():
        raise SystemExit(f"知识库源目录不存在: {kb_dir}")

    # 1. 清空旧向量集合
    print(f"[1/4] 清空旧向量集合: {domain} ...")
    client = chromadb.PersistentClient(path=VECTOR_DB_PATH)
    try:
        client.delete_collection(domain)
        print(f"    已删除旧 collection: {domain}")
    except Exception:
        print("    collection 不存在或已为空，跳过")

    # 2. 清空旧文档列表
    print("[2/4] 清空旧文档列表 ...")
    doc_list = DATA_DIR / f"doc_list_{domain}.json"
    if doc_list.exists():
        doc_list.unlink()
        print(f"    已删除 {doc_list.name}")

    # 3. 同步源文档到上传目录
    print("[3/4] 同步 knowledge_base -> data/uploads ...")
    upload_dir.mkdir(parents=True, exist_ok=True)

    md_files = sorted(p for p in kb_dir.glob("*.md") if p.name != "README.md")
    for src in md_files:
        dst = upload_dir / src.name
        shutil.copy2(src, dst)
        print(f"    同步: {src.name}")

    # 4. 逐份入库
    print("[4/4] 向量化入库 ...")
    total_chunks = 0
    for src in md_files:
        filename = src.name
        print(f"    索引: {filename} ...", end="", flush=True)
        try:
            chunks = upload_and_process(str(upload_dir / filename), filename, domain)
            total_chunks += len(chunks)
            print(f" 完成 ({len(chunks)} 块)")
        except Exception as e:
            print(f" 失败: {e}")
    print(f"\n全部完成，共 {len(md_files)} 份文档，{total_chunks} 个文本块。知识库ID: {domain}")
    print(f"启动该领域服务: set APP_DOMAIN={domain} 后运行 main.py")


if __name__ == "__main__":
    main()
