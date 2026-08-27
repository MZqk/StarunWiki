#!/usr/bin/env python3
"""Create one raw-capture record from a saved Starun telescope comparison page.

The source page keeps its final table in three inline JavaScript *data* values:
``DATA``, ``S50_PRO_VALUES`` and ``OFFICIAL_PARAMETER_CORRECTIONS``. This
parser never evaluates page JavaScript. It accepts only JSON-shaped literals,
then applies the page's documented append, replacement and label-renaming
operations explicitly so the result can be compared with a browser snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_literal_after(source: str, marker: str) -> Any:
    try:
        start = source.index(marker) + len(marker)
    except ValueError as exc:
        raise ValueError(f"未找到数据标记：{marker}") from exc
    value, _ = json.JSONDecoder().raw_decode(source[start:].lstrip())
    return value


def strip_js_line_comments(source: str) -> str:
    """Remove ``//`` comments outside JSON strings without interpreting code."""
    result: list[str] = []
    quote = False
    escaped = False
    index = 0
    while index < len(source):
        char = source[index]
        if quote:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = False
            index += 1
            continue
        if char == '"':
            quote = True
            result.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < len(source) and source[index + 1] == "/":
            index = source.find("\n", index)
            if index < 0:
                break
            result.append("\n")
            index += 1
            continue
        result.append(char)
        index += 1
    return "".join(result)


def json_array_with_comments(source: str, marker: str) -> list[str]:
    try:
        start = source.index(marker)
        array_start = source.index("[", start)
        array_end = source.index("];", array_start) + 1
    except ValueError as exc:
        raise ValueError(f"未找到数组数据：{marker}") from exc
    value = json.loads(strip_js_line_comments(source[array_start:array_end]))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{marker} 不是字符串数组")
    return value


def markdown_cell(value: str) -> str:
    return (value or "—").replace("|", "\\|").replace("\n", "<br>")


def page_note(source: str) -> str:
    start_marker = '<div class="note">'
    try:
        start = source.index(start_marker) + len(start_marker)
        end = source.index("</div>", start)
    except ValueError:
        return "页面未提取到数据说明。"
    note = source[start:end]
    for tag in ("<b>", "</b>"):
        note = note.replace(tag, "")
    return note.strip()


def final_table(source: str) -> tuple[list[str], list[list[str]]]:
    data = json_literal_after(source, "const DATA =")
    if not isinstance(data, dict) or not isinstance(data.get("headers"), list) or not isinstance(data.get("rows"), list):
        raise ValueError("DATA 不是预期的表格对象")
    headers = [str(item) for item in data["headers"]]
    rows = [[str(item) for item in row] for row in data["rows"]]
    s50_pro_values = json_array_with_comments(source, "const S50_PRO_VALUES =")
    if len(s50_pro_values) != len(rows):
        raise ValueError(f"S50 Pro 值数量不匹配：{len(s50_pro_values)} != {len(rows)}")
    headers.append("Seestar S50 Pro")
    for row, value in zip(rows, s50_pro_values, strict=True):
        row.append(value)

    corrections = json_literal_after(source, "const OFFICIAL_PARAMETER_CORRECTIONS =")
    if not isinstance(corrections, dict):
        raise ValueError("修订对象不是 JSON 对象")
    for model, changes in corrections.items():
        if model not in headers or not isinstance(changes, dict):
            raise ValueError(f"无法应用修订：{model}")
        column = headers.index(model)
        for label, value in changes.items():
            matches = [row for row in rows if row[0] == label]
            if not matches:
                raise ValueError(f"修订字段不存在：{label}")
            target = matches[-1] if label.startswith("星图软件模拟") else matches[0]
            target[column] = str(value)

    for row in rows:
        if row[0] == "星道仪模式":
            row[0] = "赤道仪（EQ）模式"

    if len(headers) != 7 or len(rows) != 65 or any(len(row) != len(headers) for row in rows):
        raise ValueError(f"意外的最终表格规模：{len(headers) - 1} 款机型，{len(rows)} 行")
    return headers, rows


def render_raw_text(headers: list[str], rows: list[list[str]], html_sha256: str, source_updated_at: str, note: str) -> str:
    lines = [
        "# 智能望远镜参数规格对比（浏览器渲染快照）",
        "",
        "## 采集说明",
        "",
        "- 页面类型：第三方市场规格汇总，不是各厂商的单一官方规格页。",
        f"- 浏览器最终状态：{len(headers) - 1} 款对比机型、{len(rows)} 行数据；其中 2 行是星图软件模拟分区，因此有 63 条实际参数记录。",
        f"- 页面 HTML SHA-256：{html_sha256}。",
        f"- HTTP Last-Modified：{source_updated_at}。",
        f"- 页面数据说明：{note}",
        "- `—` 表示页面未列出、无或未公开，具体含义必须以同一单元格的原文为准，不能一概视为不支持。",
    ]
    lines.extend([
        "",
        "## 最终渲染的规格表",
        "",
        f"产品分组：DWARFLAB（{headers[1]} / {headers[2]}）；ZWO Seestar（{' / '.join(headers[3:])}）。",
        "",
        "| 参数 | " + " | ".join(markdown_cell(value) for value in headers[1:]) + " |",
        "|---|" + "|".join("---" for _ in headers[1:]) + "|",
    ])
    for row in rows:
        if row[0] in {"长焦模拟", "广角模拟"}:
            lines.extend(["", f"### ✦{row[0]} · 星图软件模拟参数", ""])
            continue
        lines.append("| " + " | ".join(markdown_cell(value) for value in row) + " |")
    return "\n".join(lines).rstrip() + "\n"


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--input", type=Path, required=True, help="下载的 Starun HTML 文件")
parser.add_argument("--output", type=Path, required=True, help="输出的单行 JSONL 文件")
parser.add_argument("--fetched-at", required=True, help="浏览器抓取时间（ISO 8601）")
parser.add_argument("--source-updated-at", required=True, help="HTTP Last-Modified（ISO 8601）")
args = parser.parse_args()

SOURCE_HTML = args.input.read_text(encoding="utf-8")
headers_, rows_ = final_table(SOURCE_HTML)
raw_text_ = render_raw_text(headers_, rows_, sha256(SOURCE_HTML), args.source_updated_at, page_note(SOURCE_HTML))
record = {
    "site": "starun.cloud",
    "source_role": "market-comparison-catalog",
    "canonical_url": "https://starun.cloud/telescope",
    "title": "Starun：市面常见智能望远镜参数规格对比（2026-08-27 快照）",
    "category_path": "智能望远镜 / 市场参数规格对照",
    "applies_to": "本次访问日页面可见的 DWARF 3、DWARF mini、Seestar S30、Seestar S30 Pro、Seestar S50、Seestar S50 Pro；第三方汇编快照，不替代各厂商当前规格、价格、固件或 App 说明。",
    "source_updated_at": args.source_updated_at,
    "fetched_at": args.fetched_at,
    "capture_status": "browser-rendered-full-table",
    "raw_text": raw_text_,
    "content_hash": sha256(raw_text_),
}
args.output.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps({"output": str(args.output), "models": headers_[1:], "rows": len(rows_), "text_sha256": record["content_hash"]}, ensure_ascii=False))
