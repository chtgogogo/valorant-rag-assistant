# 文档解析/切分逻辑
import os
import json
import time
import uuid
from docx import Document as DocxDocument
from pypdf import PdfReader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from config.settings import UPLOAD_PATH, ALLOWED_EXTENSIONS, RAG_CONFIG
from schemas.models import DocumentChunk
from services.vector_service import add_chunks, delete_doc_vectors
from services.semantic_cache import touch_kb_epoch


# -------------------------- 文档元数据管理（用 JSON 文件存储） --------------------------

def _get_doc_list_path(kb_id: str) -> str:
    """获取知识库的文档列表 JSON 路径"""
    return os.path.join(os.path.dirname(UPLOAD_PATH), f"doc_list_{kb_id}.json")


def _load_doc_list(kb_id: str) -> list:
    """加载文档列表"""
    path = _get_doc_list_path(kb_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_doc_list(kb_id: str, doc_list: list):
    """保存文档列表"""
    path = _get_doc_list_path(kb_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc_list, f, ensure_ascii=False, indent=2)


# -------------------------- 文档解析 --------------------------

def parse_txt(file_path: str) -> str:
    """解析 txt/md 文件"""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def parse_docx(file_path: str) -> str:
    """解析 docx 文件，提取所有段落文本"""
    doc = DocxDocument(file_path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def parse_pdf(file_path: str) -> str:
    """解析 pdf 文件，逐页提取文字"""
    reader = PdfReader(file_path)
    texts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            texts.append(text.strip())
    return "\n".join(texts)


def parse_document(file_path: str, ext: str) -> str:
    """根据扩展名选择解析器"""
    if ext in ("txt", "md"):
        return parse_txt(file_path)
    elif ext == "docx":
        return parse_docx(file_path)
    elif ext == "pdf":
        return parse_pdf(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


# -------------------------- 文本清洗 --------------------------

def clean_text(text: str) -> str:
    """清洗文本：去除多余空行、首尾空白"""
    # 去除多余空行（连续空行合并为一个）
    lines = text.split("\n")
    cleaned_lines = []
    prev_empty = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not prev_empty:
                cleaned_lines.append("")
            prev_empty = True
        else:
            cleaned_lines.append(stripped)
            prev_empty = False
    return "\n".join(cleaned_lines).strip()


# -------------------------- 文本拆分 --------------------------

def split_text(text: str, chunk_size: int = None, chunk_overlap: int = None) -> list[str]:
    """
    用 LangChain 的递归字符拆分器切分文本
    :param chunk_size: 每块最大字符数
    :param chunk_overlap: 相邻块重叠字符数
    :return: 切好的文本块列表
    """
    if chunk_size is None:
        chunk_size = RAG_CONFIG["chunk_size"]
    if chunk_overlap is None:
        chunk_overlap = RAG_CONFIG["chunk_overlap"]

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " ", ""]
    )
    return splitter.split_text(text)


# -------------------------- 上传文档全流程 --------------------------

def upload_and_process(file_path: str, filename: str, kb_id: str = "valorant") -> list[DocumentChunk]:
    """
    文档上传全流程：解析 → 清洗 → 拆分 → 入库 → 记录元数据
    :return: 切好的文档块列表
    """
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {ext}，仅支持 {ALLOWED_EXTENSIONS}")

    # 1. 解析文档
    raw_text = parse_document(file_path, ext)

    # 2. 清洗文本
    clean = clean_text(raw_text)
    if not clean:
        raise ValueError("文档内容为空，无法处理")

    # 3. 拆分文本
    chunks_text = split_text(clean, RAG_CONFIG["chunk_size"], RAG_CONFIG["chunk_overlap"])
    if not chunks_text:
        raise ValueError("文本拆分失败，未生成任何文档块")

    # 4. 生成 doc_id 和 DocumentChunk
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    upload_time = time.strftime("%Y-%m-%d %H:%M:%S")

    chunks = []
    for text_block in chunks_text:
        chunks.append(DocumentChunk(
            content=text_block,
            metadata={
                "doc_id": doc_id,
                "doc_name": filename,
                "kb_id": kb_id
            }
        ))

    # 5. 调用向量服务入库
    success = add_chunks(chunks, kb_id)
    if not success:
        raise ValueError("向量入库失败，请检查向量引擎是否正常")

    # 6. 记录文档元数据
    doc_list = _load_doc_list(kb_id)
    doc_list.append({
        "doc_id": doc_id,
        "doc_name": filename,
        "upload_time": upload_time,
        "chunk_count": len(chunks)
    })
    _save_doc_list(kb_id, doc_list)

    # 【v3.19】知识库内容变更：touch epoch 信号让语义缓存自动失效
    touch_kb_epoch()

    print(f"[文档管理] 文档 [{filename}] 处理完成，切分为 {len(chunks)} 块，已入库")
    return chunks


# -------------------------- 文档列表 --------------------------

def list_documents(kb_id: str = "valorant") -> list:
    """获取指定知识库的文档列表"""
    return _load_doc_list(kb_id)


# -------------------------- 文档删除 --------------------------

def delete_document(doc_id: str, kb_id: str = "valorant") -> bool:
    """
    删除文档：删除向量 + 删除元数据记录 + 删除原始文件
    """
    # 1. 删除向量库中的数据
    delete_doc_vectors(doc_id, kb_id)

    # 2. 从文档列表中删除记录
    doc_list = _load_doc_list(kb_id)
    target = None
    new_list = []
    for doc in doc_list:
        if doc["doc_id"] == doc_id:
            target = doc
        else:
            new_list.append(doc)

    if target:
        _save_doc_list(kb_id, new_list)
        # 3. 删除原始上传文件
        # doc_name 来自注册表（源头是历史客户端文件名），删除前必须校验仍在上传目录内，
        # 防历史脏数据（如 ../../x.md）借删除接口删掉任意文件
        base = os.path.realpath(UPLOAD_PATH)
        resolved = os.path.realpath(os.path.join(UPLOAD_PATH, target["doc_name"]))
        if resolved == base or resolved.startswith(base + os.sep):
            if os.path.exists(resolved):
                os.remove(resolved)
        else:
            print(f"[文档管理] 拒绝删除上传目录外的路径: {target['doc_name']}")
        print(f"[文档管理] 文档 [{target['doc_name']}] 已删除")
    # 【v3.19】知识库内容变更：touch epoch 信号让语义缓存自动失效（无论是否命中记录，内容已变）
    touch_kb_epoch()
    return True
