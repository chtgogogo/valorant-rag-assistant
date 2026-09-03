# -*- coding: utf-8 -*-
"""
联网检索脚本：GitHub 同类项目 + B站教程

用途：
- 帮助维护者定期检索 GitHub 上的同类型项目。
- 帮助维护者检索 B站上的无畏契约教程，方便扩充知识库。

用法：
    cd backend
    python scripts/search_online_resources.py
    python scripts/search_online_resources.py --write
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_PATH = PROJECT_ROOT / "docs" / "联网检索结果.md"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def github_search(query: str, per_page: int = 5) -> list[dict]:
    """检索 GitHub 仓库"""
    url = (
        "https://api.github.com/search/repositories?q="
        + urllib.parse.quote(query)
        + f"&sort=stars&order=desc&per_page={per_page}"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    items = data.get("items", [])
    return [
        {
            "name": it.get("full_name", ""),
            "stars": it.get("stargazers_count", 0),
            "url": it.get("html_url", ""),
            "desc": (it.get("description") or "")[:160],
        }
        for it in items
    ]


def bilibili_search(keyword: str, limit: int = 5) -> list[dict]:
    """检索 B站视频"""
    url = (
        "https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword="
        + urllib.parse.quote(keyword)
    )
    req = urllib.request.Request(
        url,
        headers={
            **HEADERS,
            "Referer": "https://www.bilibili.com",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("code") != 0:
        return []
    result = data.get("data", {}).get("result", []) or []
    out = []
    for it in result[:limit]:
        title = it.get("title", "").replace("<em class=\"keyword\">", "").replace("</em>", "")
        bvid = it.get("bvid", "")
        out.append({
            "title": title,
            "author": it.get("author", ""),
            "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
            "play": it.get("play", 0),
        })
    return out


def build_markdown(github_rows, bili_rows) -> str:
    lines = ["# 联网检索结果", ""]
    lines.append("> 由 `backend/scripts/search_online_resources.py` 自动生成，仅供参考。")
    lines.append("")
    lines.append("## GitHub 同类项目")
    lines.append("")
    for query, rows in github_rows:
        lines.append(f"### 检索词：{query}")
        lines.append("")
        if not rows:
            lines.append("无结果")
            lines.append("")
        for r in rows:
            lines.append(f"- [{r['name']}]({r['url']}) ⭐{r['stars']}：{r['desc']}")
        lines.append("")
    lines.append("## B站教程")
    lines.append("")
    for keyword, rows in bili_rows:
        lines.append(f"### 检索词：{keyword}")
        lines.append("")
        if not rows:
            lines.append("无结果")
            lines.append("")
        for r in rows:
            lines.append(f"- [{r['title']}]({r['url']})  UP：{r['author']}  播放：{r['play']}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="将结果写入 docs/联网检索结果.md")
    args = parser.parse_args()

    github_queries = [
        "valorant chatbot RAG",
        "RAG chatbot FastAPI Vue",
        "knowledge base LLM FastAPI Vue",
    ]
    bili_keywords = [
        "无畏契约 新手教程",
        "无畏契约 英雄教学",
        "无畏契约 战术教学",
    ]

    github_rows = []
    for q in github_queries:
        print(f"[GitHub] 检索: {q} ...", flush=True)
        try:
            rows = github_search(q)
        except Exception as e:
            print(f"    失败: {e}")
            rows = []
        github_rows.append((q, rows))
        time.sleep(1)

    bili_rows = []
    for kw in bili_keywords:
        print(f"[B站] 检索: {kw} ...", flush=True)
        try:
            rows = bilibili_search(kw)
        except Exception as e:
            print(f"    失败: {e}")
            rows = []
        bili_rows.append((kw, rows))
        time.sleep(1)

    markdown = build_markdown(github_rows, bili_rows)
    if args.write:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(markdown, encoding="utf-8")
        print(f"已写入: {OUTPUT_PATH}")
    else:
        print(markdown)


if __name__ == "__main__":
    main()
