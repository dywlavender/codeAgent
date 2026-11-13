"""Read, Glob and Grep over the current turn's authorized sources."""

from __future__ import annotations

import json
import os
import re
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path, PurePosixPath

from business_code_agent.codeintel.retrieval import IGNORED_DIRS, SOURCE_SUFFIXES
from business_code_agent.query.workspace import workspace_sources


def matches(path, pattern):
    """Path glob with zero-or-more directory segments for **, on Python 3.11+."""
    parts = PurePosixPath(path).parts
    pattern_parts = PurePosixPath(pattern).parts

    @lru_cache(None)
    def visit(i, j):
        if j == len(pattern_parts):
            return i == len(parts)
        if pattern_parts[j] == "**":
            return visit(i, j + 1) or (i < len(parts) and visit(i + 1, j))
        return (
            i < len(parts)
            and fnmatchcase(parts[i], pattern_parts[j])
            and visit(i + 1, j + 1)
        )

    # Preserve filename/suffix patterns while treating ** consistently.
    return visit(0, 0) or any(visit(i, 0) for i in range(1, len(parts)))


class SourceFiles:
    def __init__(self, workspace, repositories=None):
        self.workspace = Path(workspace).resolve()
        mounts = {
            "业务补充知识": "knowledge/business-context",
            "业务基线": "knowledge/baseline",
            "自动代码地图": "knowledge/generated-code-map",
            "AST 结构资料": "knowledge/ast",
            "需求原文": "requirements",
        }
        self.roots = [
            (
                mounts.get(label, "repos/" + label.removeprefix("代码仓库 ")),
                Path(root).resolve(),
            )
            for label, root in workspace_sources(self.workspace, repositories)
        ]

    def locate(self, path):
        candidate = Path(path)
        candidate = (
            candidate if candidate.is_absolute() else self.workspace / candidate
        ).resolve()
        for label, root in self.roots:
            if candidate.is_relative_to(root):
                relative = candidate.relative_to(root)
                if any(p.startswith(".") or p in IGNORED_DIRS for p in relative.parts):
                    break
                return candidate, label if relative == Path(".") else f"{label}/{relative.as_posix()}"
        raise ValueError("路径不在本轮授权资料范围内；请使用 Glob 返回的路径")

    def files(self, path=""):
        scoped_roots = [source for source in self.roots if source[0] == path.rstrip("/")]
        for label, root in scoped_roots or self.roots:
            for directory, dirs, names in os.walk(root):
                dirs[:] = sorted(
                    d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")
                )
                for name in sorted(names):
                    file = Path(directory) / name
                    relative = file.relative_to(root)
                    if any(
                        p.startswith(".") or p in IGNORED_DIRS for p in relative.parts
                    ):
                        continue
                    if (
                        file.is_file()
                        and file.suffix.lower() in SOURCE_SUFFIXES
                        and file.resolve().is_relative_to(root)
                    ):
                        display = f"{label}/{relative.as_posix()}"
                        if (
                            not path
                            or display.startswith(path.rstrip("/"))
                            or str(file).startswith(path)
                        ):
                            yield file, display

    def read(self, path, offset=1, limit=160):
        file, display = self.locate(path)
        if file.stat().st_size > 2_000_000:
            raise ValueError("文件过大，请先使用 Grep 定位")
        lines = file.read_text(encoding="utf-8-sig").splitlines()
        offset, limit = max(1, offset), max(1, min(limit, 240))
        stop = min(len(lines), offset - 1 + limit)
        return {
            "path": display,
            "lineStart": offset,
            "lineEnd": stop,
            "totalLines": len(lines),
            "sourceMtimeNs": file.stat().st_mtime_ns,
            "truncated": stop < len(lines),
            "nextLine": stop + 1 if stop < len(lines) else None,
            "deprecated": bool(
                re.search(
                    r"@Deprecated|^deprecated:\s*true", "\n".join(lines), re.M | re.I
                )
            ),
            "content": "\n".join(
                f"{i + 1}: {line}"
                for i, line in enumerate(lines[offset - 1 : stop], offset - 1)
            ),
        }

    def directories(self):
        for label, root in self.roots:
            yield label
            for directory, dirs, _ in os.walk(root):
                dirs[:] = sorted(
                    d for d in dirs
                    if d not in IGNORED_DIRS and not d.startswith(".")
                    and (Path(directory) / d).resolve().is_relative_to(root)
                )
                for name in dirs:
                    relative = (Path(directory) / name).relative_to(root)
                    yield f"{label}/{relative.as_posix()}"

    def glob(self, pattern, limit=100, kind="files"):
        if kind not in {"files", "directories"}:
            raise ValueError("kind 仅支持 files 或 directories")
        scope = next((mount for mount, _ in self.roots if pattern.startswith(mount + "/")), "")
        candidates = self.directories() if kind == "directories" else (p for _, p in self.files(scope))
        found = sorted(p for p in candidates if matches(p, pattern))
        return {
            "paths": found[: max(1, min(limit, 200))],
            "truncated": len(found) > limit,
        }

    def grep(self, pattern, path="", glob="", literal=False, limit=60, case_sensitive=True, whole_word=False):
        expression = re.escape(pattern) if literal else pattern
        if whole_word:
            expression = r"(?<!\w)(?:" + expression + r")(?!\w)"
        expression = re.compile(expression, 0 if case_sensitive else re.I)
        found = []
        cap = max(1, min(limit, 120))
        for file, display in self.files(path):
            if glob and not matches(display, glob):
                continue
            if file.stat().st_size > 2_000_000:
                continue
            try:
                lines = file.read_text(encoding="utf-8-sig").splitlines()
            except (UnicodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                if expression.search(line):
                    found.append({"path": display, "line": number, "text": line[:600]})
                    if len(found) >= cap:
                        return {"matches": found, "truncated": True}
        return {"matches": found, "truncated": False}

    def tools(self):
        from langchain_core.tools import tool

        def result(function, *args):
            try:
                return json.dumps(function(*args), ensure_ascii=False)
            except (OSError, ValueError, re.error) as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        @tool
        def Read(path: str, offset: int = 1, limit: int = 160) -> str:
            """读取源码或资料原文，返回真实行号。path 使用 Glob/Grep 返回的路径；截断后可用 nextLine 续读。"""
            return result(self.read, path, offset, limit)

        @tool
        def Glob(pattern: str, limit: int = 100, kind: str = "files") -> str:
            """定位授权范围内的文件；** 可匹配零层或多层目录。列仓库目录用 pattern='repos/*', kind='directories'。"""
            return result(self.glob, pattern, limit, kind)

        @tool
        def Grep(
            pattern: str,
            path: str = "",
            glob: str = "",
            literal: bool = False,
            limit: int = 60,
            case_sensitive: bool = True,
            whole_word: bool = False,
        ) -> str:
            """搜索原文行，默认区分大小写。枚举可用 whole_word=True，忽略大小写须设 case_sensitive=False。path/glob 缩小范围；命中仅为线索，关键结论须 Read。"""
            return result(self.grep, pattern, path, glob, literal, limit, case_sensitive, whole_word)

        return [Read, Glob, Grep]
