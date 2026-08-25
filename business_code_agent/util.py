from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts)
    return f"{prefix}-{uuid.uuid5(uuid.NAMESPACE_URL, raw).hex[:16]}"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def tokens(text: str) -> list[str]:
    return [x for x in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]{2,}", text) if len(x) > 1]


def camel_fields(text: str) -> list[str]:
    # Chinese characters are word characters in Unicode regex, so a trailing
    # `\b` misses explicit identifiers such as `projectNo同步`. Use ASCII
    # identifier boundaries instead.
    values = re.findall(
        r"(?<![A-Za-z0-9_])[a-z][A-Za-z0-9]*(?:Type|Code|No|Id|Status|Flag|Date|Time)(?![A-Za-z0-9_])",
        text,
    )
    return list(dict.fromkeys(values))


def chinese_phrases(text: str, suffixes: tuple[str, ...]) -> list[str]:
    results: list[str] = []
    for suffix in suffixes:
        pattern = rf"[\u4e00-\u9fff]{{1,12}}{re.escape(suffix)}"
        results.extend(re.findall(pattern, text))
    return list(dict.fromkeys(results))
