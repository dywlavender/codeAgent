from __future__ import annotations

import re

from .models import RequirementChunk


class RequirementChunker:
    def __init__(self, max_chars: int = 1200):
        if max_chars < 200:
            raise ValueError("max_chars must be at least 200")
        self.max_chars = max_chars

    def chunk(self, requirement_id: str, version: int, sections, original: str | None = None) -> list[RequirementChunk]:
        chunks = []
        offset = 0
        sequence = 0
        for section in sections:
            content = "\n".join(section.paragraphs).strip()
            for part in self._split(content):
                sequence += 1
                chunk_id = f"{requirement_id}-V{version}-C{sequence:03d}"
                located = original.find(part, offset) if original is not None else -1
                start = located if located >= 0 else offset
                end = start + len(part)
                chunks.append(RequirementChunk(
                    chunk_id, list(section.path), sequence, part, start, end,
                    section.paragraph_start, section.paragraph_end, section.page,
                ))
                offset = end
        return chunks

    def _split(self, content: str) -> list[str]:
        if len(content) <= self.max_chars:
            return [content]
        sentences = [item.strip() for item in re.split(r"(?<=[。！？；])", content) if item.strip()]
        parts, current = [], ""
        for sentence in sentences:
            if len(sentence) > self.max_chars:
                if current:
                    parts.append(current)
                    current = ""
                parts.extend(sentence[index:index + self.max_chars] for index in range(0, len(sentence), self.max_chars))
            elif current and len(current) + len(sentence) > self.max_chars:
                parts.append(current)
                current = sentence
            else:
                current += sentence
        if current:
            parts.append(current)
        return parts
