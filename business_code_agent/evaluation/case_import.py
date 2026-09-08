"""Import and normalize evaluation cases from JSON and simple Excel files.

The browser sends an uploaded file as text/base64 JSON so the existing small
HTTP server does not need a multipart parser.  The importer deliberately
keeps answer criteria as user-editable text; it does not turn a reference
answer into an automatic score.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


FIELD_ALIASES = {
    "id": ("id", "case_id", "caseId", "案例编号", "编号"),
    "question": ("question", "问题", "题目", "问题描述"),
    "referenceAnswer": ("referenceAnswer", "reference_answer", "answer", "参考答案", "预期答案"),
    "category": ("category", "题目分类", "分类", "类型"),
    "checks": ("checks", "rubric", "评分要点", "评分标准", "判分标准"),
    "scope": ("scope", "适用范围", "适用业务", "适用版本"),
    "evidence": ("evidence", "依据", "参考依据", "来源"),
    "disabled": ("disabled", "禁用", "停用"),
    "notes": ("notes", "备注", "说明"),
}


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _header_key(value) -> str:
    return re.sub(r"[\s_\-（）()]+", "", _text(value)).casefold()


def _find(row: dict, field: str, mapping: dict | None = None):
    mapped = (mapping or {}).get(field)
    if mapped:
        if mapped in row:
            return row[mapped]
        mapped_key = _header_key(mapped)
        for key, value in row.items():
            if _header_key(key) == mapped_key:
                return value
    normalized = {_header_key(key): value for key, value in row.items()}
    for alias in FIELD_ALIASES[field]:
        key = _header_key(alias)
        if key in normalized:
            return normalized[key]
    return None


def _checks(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    if not text:
        return []
    # Standard templates use one item per line.  Accept numbered/bulleted
    # input without making punctuation part of the stored criterion.
    values = []
    for line in text.replace("；", "\n").splitlines():
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.、)]|[一二三四五六七八九十]+[、.)])\s*", "", line).strip()
        if cleaned:
            values.append(cleaned)
    return values or [text]


def _boolean(value) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).casefold() in {"1", "true", "yes", "y", "是", "禁用", "停用"}


def normalize_row(row: dict, index: int, *, require_reference: bool = True,
                  mapping: dict | None = None) -> tuple[dict | None, str | None]:
    if not isinstance(row, dict):
        return None, "行内容不是对象"
    question = _text(_find(row, "question", mapping))
    reference = _text(_find(row, "referenceAnswer", mapping))
    if not question:
        return None, "缺少问题"
    if require_reference and not reference:
        return None, "缺少参考答案"
    case_id = _text(_find(row, "id", mapping)) or f"case-{index:03d}"
    case_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", case_id).strip("-") or f"case-{index:03d}"
    return {
        "id": case_id,
        "question": question,
        "referenceAnswer": reference,
        "checks": _checks(_find(row, "checks", mapping)),
        "category": _text(_find(row, "category", mapping)) or "uncategorized",
        "scope": _text(_find(row, "scope", mapping)),
        "evidence": _text(_find(row, "evidence", mapping)),
        "disabled": _boolean(_find(row, "disabled", mapping)),
        "notes": _text(_find(row, "notes", mapping)),
    }, None


def normalize_rows(rows: list[dict], *, require_reference: bool = True,
                   mapping: dict | None = None) -> tuple[list[dict], list[dict]]:
    cases = []
    errors = []
    seen = set()
    for index, row in enumerate(rows, 1):
        case, error = normalize_row(row, index, require_reference=require_reference, mapping=mapping)
        if error:
            errors.append({"row": index, "error": error, "values": row})
            continue
        if case["id"] in seen:
            errors.append({"row": index, "error": f"案例编号重复：{case['id']}", "values": row})
            continue
        seen.add(case["id"])
        cases.append(case)
    return cases, errors


def _json_rows(payload):
    value = json.loads(payload) if isinstance(payload, str) else payload
    if isinstance(value, list):
        return {}, value
    if not isinstance(value, dict):
        raise ValueError("JSON 顶层必须是对象或数组")
    rows = value.get("cases")
    if rows is None:
        rows = value.get("questions")
    if not isinstance(rows, list):
        raise ValueError("JSON 中没有 cases 数组")
    return value, rows


def _xlsx_value(cell, shared_strings):
    node = cell.find("{*}v")
    raw = "" if node is None else node.text or ""
    if cell.get("t") == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    inline = cell.find("{*}is/{*}t")
    if inline is not None:
        return inline.text or ""
    return raw


def _xlsx_rows(data: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("{*}si"):
                shared.append("".join(text.text or "" for text in item.findall(".//{*}t")))
        sheet_names = [name for name in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)]
        if not sheet_names:
            raise ValueError("Excel 文件中没有可读取的工作表")
        root = ElementTree.fromstring(archive.read(sorted(sheet_names)[0]))
        raw_rows = []
        for row in root.findall(".//{*}row"):
            values = {}
            for cell in row.findall("{*}c"):
                ref = cell.get("r", "A1")
                column = re.sub(r"\d", "", ref).upper()
                values[column] = _xlsx_value(cell, shared)
            raw_rows.append(values)
    if not raw_rows:
        return []
    columns = sorted({key for row in raw_rows for key in row}, key=lambda value: (len(value), value))
    headers = [raw_rows[0].get(column, "") for column in columns]
    return [{str(headers[index] or columns[index]): row.get(columns[index], "")
             for index in range(len(columns)) if str(headers[index] or columns[index]).strip()}
            for row in raw_rows[1:]
            if any(_text(value) for value in row.values())]


def _csv_rows(data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def decode_upload(filename: str, content: str, encoding: str = "base64") -> tuple[dict, list[dict]]:
    name = Path(filename or "cases.json").name
    raw = base64.b64decode(content) if encoding == "base64" else content.encode("utf-8")
    suffix = Path(name).suffix.casefold()
    if suffix == ".json":
        metadata, rows = _json_rows(raw.decode("utf-8-sig"))
    elif suffix in {".xlsx", ".xlsm"}:
        metadata, rows = {}, _xlsx_rows(raw)
    elif suffix in {".csv", ".tsv"}:
        metadata, rows = {}, _csv_rows(raw.replace(b"\t", b",") if suffix == ".tsv" else raw)
    else:
        raise ValueError("支持 JSON、Excel（.xlsx/.xlsm）或 CSV 文件")
    return metadata, rows


def import_upload(filename: str, content: str, encoding: str = "base64", mapping: dict | None = None) -> dict:
    metadata, rows = decode_upload(filename, content, encoding)
    columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
    if rows and mapping is None:
        has_question = _find(rows[0], "question") is not None
        has_reference = _find(rows[0], "referenceAnswer") is not None
        if not has_question or not has_reference:
            return {
                "name": _text(metadata.get("name")) or Path(filename or "题库").stem,
                "scope": _text(metadata.get("scope")),
                "origin": metadata.get("origin") or "real",
                "mappingRequired": True,
                "columns": columns,
                "previewRows": rows[:5],
                "importSummary": {"success": 0, "failed": len(rows), "errors": []},
            }
    cases, errors = normalize_rows(rows, mapping=mapping)
    name = _text(metadata.get("name")) or Path(filename or "题库").stem
    return {
        "name": name,
        "scope": _text(metadata.get("scope")),
        "origin": metadata.get("origin") or "real",
        "cases": cases,
        "importSummary": {"success": len(cases), "failed": len(errors), "errors": errors},
    }
