from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import ParsedSection


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class RequirementDocumentParser:
    SUPPORTED = {".txt", ".md", ".json", ".docx"}

    def parse(self, path: str) -> dict:
        source = Path(path).resolve()
        suffix = source.suffix.lower()
        if suffix not in self.SUPPORTED:
            raise ValueError(f"unsupported requirement document: {suffix}")
        if suffix == ".docx":
            title, sections, original = self._docx(source)
        elif suffix == ".json":
            payload = json.loads(source.read_text(encoding="utf-8"))
            original = str(payload.get("original", "")).strip()
            title = str(payload.get("title") or source.stem)
            sections = _text_sections(original)
        else:
            original = source.read_text(encoding="utf-8").strip()
            title, sections = _markdown_or_text(original, source.stem)
        if not original:
            raise ValueError("requirement document is empty")
        return {"title": title, "original": original, "sections": sections, "source_type": suffix.removeprefix(".").upper()}

    def _docx(self, source: Path) -> tuple[str, list[ParsedSection], str]:
        try:
            archive = zipfile.ZipFile(source)
            root = ET.fromstring(archive.read("word/document.xml"))
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            raise ValueError(f"invalid DOCX: {exc}") from exc
        paragraphs = []
        title_index = None
        for paragraph in root.iter(W + "p"):
            text = "".join(node.text or "" for node in paragraph.iter(W + "t")).strip()
            if not text:
                continue
            style_node = paragraph.find(f"{W}pPr/{W}pStyle")
            style = style_node.get(W + "val", "") if style_node is not None else ""
            heading = _heading_level(style, text)
            if title_index is None and style.lower() in {"title", "标题"}:
                title_index = len(paragraphs)
            paragraphs.append((text, heading))
        if not paragraphs:
            raise ValueError("DOCX contains no readable paragraphs")
        title = paragraphs[title_index if title_index is not None else 0][0]
        content_paragraphs = [item for index, item in enumerate(paragraphs) if index != title_index]
        sections = _structured_sections(content_paragraphs)
        original = "\n\n".join(text for text, _ in paragraphs)
        return title, sections, original


def _heading_level(style: str, text: str) -> int | None:
    match = re.search(r"(?:Heading|标题)\s*([1-9])", style, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.match(r"^([一二三四五六七八九十]+|\d+)[.、]\s*\S+", text)
    return 1 if match else None


def _structured_sections(paragraphs: list[tuple[str, int | None]]) -> list[ParsedSection]:
    path: list[str] = []
    sections: list[ParsedSection] = []
    current: list[str] = []
    current_path: list[str] = []
    start = 1
    for index, (text, heading) in enumerate(paragraphs, 1):
        if heading is not None:
            if current:
                sections.append(ParsedSection(current_path or ["正文"], current, start, index - 1))
            path = path[:heading - 1] + [text]
            current_path = list(path)
            current = []
            start = index + 1
        else:
            if not current:
                start = index
                current_path = list(path) or ["正文"]
            current.append(text)
    if current:
        sections.append(ParsedSection(current_path or ["正文"], current, start, len(paragraphs)))
    return sections or [ParsedSection(["正文"], [text for text, _ in paragraphs], 1, len(paragraphs))]


def _markdown_or_text(original: str, default_title: str) -> tuple[str, list[ParsedSection]]:
    paragraphs = []
    for value in [item.strip() for item in re.split(r"\n\s*\n", original) if item.strip()]:
        match = re.match(r"^(#{1,6})\s+(.+)$", value)
        paragraphs.append((match.group(2), len(match.group(1))) if match else (value, _heading_level("", value)))
    title = next((text for text, level in paragraphs if level == 1), default_title)
    return title, _structured_sections(paragraphs)


def _text_sections(original: str) -> list[ParsedSection]:
    return _structured_sections([(item, _heading_level("", item)) for item in re.split(r"\n\s*\n", original) if item.strip()])
