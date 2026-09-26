# 文档解析/切分逻辑
import logging
import os
import json
import tempfile
import threading
import time
import uuid
from docx import Document as DocxDocument
from pypdf import PdfReader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from config.settings import UPLOAD_PATH, ALLOWED_EXTENSIONS, RAG_CONFIG
from schemas.models import DocumentChunk
from services.vector_service import add_chunks, delete_doc_vectors
from services.semantic_cache import touch_kb_epoch

logger = logging.getLogger(__name__)

# -------------------------- 文档元数据管理（用 JSON 文件存储） --------------------------
# 【v3.21】注册表三防：
#   ① 原子写——写临时文件再 os.replace，写一半崩溃不会留下损坏的半截 JSON；
#   ② per-kb 文件锁——并发上传的 load→append→save 全程持锁，不再互相覆盖丢记录；
#   ③ 损坏自愈留痕——读损坏文件时记 error 日志 + 备份坏文件，绝不无声无息当空
#     （旧行为 except 后静默返回 []，知识库会"凭空消失"且无从排查）。

_doc_list_locks: dict = {}
_locks_guard = threading.Lock()


def _get_doc_list_lock(kb_id: str) -> threading.Lock:
    with _locks_guard:
        if kb_id not in _doc_list_locks:
            _doc_list_locks[kb_id] = threading.Lock()
        return _doc_list_locks[kb_id]


def _get_doc_list_path(kb_id: str) -> str:
    """获取知识库的文档列表 JSON 路径"""
    return os.path.join(os.path.dirname(UPLOAD_PATH), f"doc_list_{kb_id}.json")


def _load_doc_list(kb_id: str) -> list:
    """加载文档列表（损坏 → error 日志 + 备份坏文件后自愈为空）"""
    path = _get_doc_list_path(kb_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        backup = f"{path}.corrupt-{time.strftime('%Y%m%d_%H%M%S')}"
        try:
            os.replace(path, backup)
        except OSError:
            backup = "(备份失败)"
        logger.error("文档注册表损坏 kb=%s: %s；坏文件已备份至 %s，本次按空注册表继续（自愈）",
                     kb_id, e, backup)
        return []
    except OSError as e:
        logger.error("文档注册表读取失败 kb=%s: %s（按空注册表继续）", kb_id, e)
        return []


def _save_doc_list(kb_id: str, doc_list: list):
    """保存文档列表（原子写：临时文件 + os.replace，永不留半截文件）"""
    path = _get_doc_list_path(kb_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc_list, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _append_doc_record(kb_id: str, record: dict):
    """持 per-kb 锁追加一条文档记录（load→append→save 原子化，并发上传互不覆盖）"""
    with _get_doc_list_lock(kb_id):
        doc_list = _load_doc_list(kb_id)
        doc_list.append(record)
        _save_doc_list(kb_id, doc_list)


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

def _split_by_headings(text: str) -> list[tuple[str, str]]:
    """按 markdown 标题（#/##/###/####）把全文预分段
    :return: [(所属标题, 段文本), ...]——首个标题前的导语段标题为 ""
    【W8-卡7】根治"标题与内容分家"：原切分器按字符边界切，标题可能留在
    上一块尾部、内容块成了无主切片（实测：雷兹技能表格不含"雷兹"字样，
    查"雷兹"永远命中尾部借走标题的芮娜切片——卡1重放实验 §3 实锤）。
    """
    import re
    matches = list(re.finditer(r"^#{1,4} .+$", text, flags=re.M))
    if not matches:
        return [("", text)]
    sections = []
    if matches[0].start() > 0:
        sections.append(("", text[:matches[0].start()]))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(0).strip(), text[m.start():end]))
    return sections


def split_text(text: str, chunk_size: int = None, chunk_overlap: int = None) -> list[str]:
    """
    用 LangChain 的递归字符拆分器切分文本
    【W8-卡7】切分前先按标题预分段，每块前置所属标题——让每块自带身份，
    检索词命中标题即可带出正确内容（工单等无标题文本走原逻辑不受影响）
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
    out: list[str] = []
    for heading, section in _split_by_headings(text):
        for piece in splitter.split_text(section):
            # 段文本本身以标题开头时（标题行总在段首）不重复前置
            out.append(piece if piece.startswith(heading)
                       else f"{heading}\n{piece}" if heading else piece)
    return out


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

    # 【v3.21】同名文档重复上传 version 递增：读注册表定版本号（此刻就定，
    # 以便 version 随块 metadata 写入向量库，检索结果与 sources 均可透传）
    with _get_doc_list_lock(kb_id):
        doc_list = _load_doc_list(kb_id)
        same_name = [d for d in doc_list if d.get("doc_name") == filename]
        version = max((int(d.get("version") or 1) for d in same_name), default=0) + 1

    chunks = []
    for text_block in chunks_text:
        chunks.append(DocumentChunk(
            content=text_block,
            metadata={
                "doc_id": doc_id,
                "doc_name": filename,
                "kb_id": kb_id,
                "version": version
            }
        ))

    # 5. 调用向量服务入库
    success = add_chunks(chunks, kb_id)
    if not success:
        raise ValueError("向量入库失败，请检查向量引擎是否正常")

    # 6. 记录文档元数据（【v3.21】持锁追加：并发上传互不覆盖；记录带 version 与 updated_at）
    _append_doc_record(kb_id, {
        "doc_id": doc_id,
        "doc_name": filename,
        "upload_time": upload_time,
        "chunk_count": len(chunks),
        "version": version,
        "updated_at": upload_time,
    })

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

    # 2. 从文档列表中删除记录（【v3.21】持锁读改写，防与并发上传互相覆盖）
    with _get_doc_list_lock(kb_id):
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
