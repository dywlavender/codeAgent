"""Read, Glob and Grep over only the core run's authorized roots."""

from __future__ import annotations

import json
import os
import re
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path, PurePosixPath

from business_code_agent.query.materials import MaterialSource


CORE_SOURCE_SUFFIXES = {
    ".java", ".kt", ".scala", ".groovy", ".py", ".go", ".rs", ".cs", ".c", ".h",
    ".cpp", ".hpp", ".js", ".jsx", ".ts", ".tsx", ".vue", ".html", ".css", ".scss",
    ".xml", ".sql", ".yml", ".yaml", ".properties", ".json", ".toml", ".gradle",
    ".ini", ".conf", ".md", ".txt", ".sh", ".bat", ".ps1", ".proto", ".graphql",
}
CORE_FILE_NAMES = {"Dockerfile", "Makefile", "Jenkinsfile", "Procfile",
                   "README", "LICENSE", "NOTICE", "CHANGELOG"}
CORE_IGNORED_DIRS = {
    ".git", ".svn", ".hg", ".data", ".venv", "node_modules", "target", "build",
    "dist", "__pycache__", ".idea", ".gradle", "vendor", ".next", "out",
}


def matches(path: str, pattern: str) -> bool:
    """Match POSIX paths where ``**`` spans zero or more directories."""
    parts = PurePosixPath(path).parts
    pattern_parts = PurePosixPath(pattern).parts

    @lru_cache(None)
    def visit(i, j):
        if j == len(pattern_parts):
            return i == len(parts)
        if pattern_parts[j] == "**":
            return visit(i, j + 1) or (i < len(parts) and visit(i + 1, j))
        return i < len(parts) and fnmatchcase(parts[i], pattern_parts[j]) and visit(i + 1, j + 1)

    return visit(0, 0) or any(visit(i, 0) for i in range(1, len(parts)))


def _is_ignored(relative: Path) -> bool:
    return any(part.startswith(".") or part in CORE_IGNORED_DIRS for part in relative.parts)


class CoreFiles:
    """File access bound to the ready material roots authorized this run."""

    def __init__(self, workspace, materials):
        self.workspace = Path(workspace).resolve()
        roots = []
        for source in materials:
            if not isinstance(source, MaterialSource) or not source.available:
                continue
            if source.kind not in {"source", "requirements", "business_context"}:
                continue
            root = Path(source.directory).resolve()
            if not root.is_dir():
                continue
            roots.append((str(source.path).rstrip("/"), root))
        if not roots:
            raise ValueError("本轮没有可授权的源码、需求或业务资料目录")
        self.roots = roots

    def locate(self, path):
        value = str(path or "").strip().replace("\\", "/")
        if not value:
            raise ValueError("path 不能为空")
        candidate = PurePosixPath(value)
        if candidate.is_absolute():
            raise ValueError("只接受授权资料内的相对路径，不接受宿主绝对路径")
        if ".." in candidate.parts:
            raise ValueError("路径不能包含 ..")
        for mount, root in self.roots:
            root_path = PurePosixPath(mount)
            if candidate != root_path and root_path not in candidate.parents:
                continue
            relative = Path(*candidate.parts[len(root_path.parts):])
            if relative.parts and _is_ignored(relative):
                break
            file = (root / relative).resolve()
            if not file.is_relative_to(root):
                break
            display = mount if not relative.parts else f"{mount}/{relative.as_posix()}"
            return file, display
        raise ValueError("路径不在本轮授权资料范围内；请使用 Glob 返回的路径")

    def _walk_files(self, path: str = ""):
        raw = str(path or "").strip().replace("\\", "/")
        candidate = PurePosixPath(raw) if raw else None
        if candidate is not None:
            if candidate.is_absolute():
                raise ValueError("只接受授权资料内的相对路径，不接受宿主绝对路径")
            if ".." in candidate.parts:
                raise ValueError("路径不能包含 ..")
        prefix = raw.strip("/")
        targets = []
        for mount, root in self.roots:
            if not prefix or prefix == mount:
                targets.append((mount, root, Path()))
                continue
            if not prefix.startswith(mount + "/"):
                continue
            relative = Path(*PurePosixPath(prefix[len(mount) + 1:]).parts)
            if _is_ignored(relative):
                raise ValueError("路径不在本轮授权资料范围内")
            target = (root / relative).resolve()
            if not target.is_relative_to(root):
                raise ValueError("路径不在本轮授权资料范围内")
            targets.append((mount, root, relative))
        if prefix and not targets:
            raise ValueError("path 不在本轮授权资料范围内")
        for mount, root, relative_prefix in targets:
            start = root / relative_prefix
            if start.is_file():
                if self._is_source_file(start):
                    yield start, f"{mount}/{start.relative_to(root).as_posix()}"
                continue
            if not start.is_dir():
                continue
            for directory, dirs, names in os.walk(start):
                dirs[:] = sorted(
                    name for name in dirs
                    if name not in CORE_IGNORED_DIRS and not name.startswith(".")
                    and (Path(directory) / name).resolve().is_relative_to(root)
                )
                for name in sorted(names):
                    file = Path(directory) / name
                    try:
                        relative = file.relative_to(root)
                    except ValueError:
                        continue
                    if _is_ignored(relative):
                        continue
                    if not file.is_file() or not file.resolve().is_relative_to(root):
                        continue
                    if not self._is_source_file(file):
                        continue
                    yield file, f"{mount}/{relative.as_posix()}"

    @staticmethod
    def _is_source_file(file: Path) -> bool:
        return file.suffix.lower() in CORE_SOURCE_SUFFIXES or file.name in CORE_FILE_NAMES

    def read(self, path, offset=1, limit=160):
        file, display = self.locate(path)
        if not file.is_file():
            raise ValueError(f"文件不存在或不可读：{display}")
        if file.stat().st_size > 2_000_000:
            raise ValueError("文件过大，请先使用 Grep 定位")
        try:
            lines = file.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeError as exc:
            raise ValueError(f"无法按 UTF-8 读取 {display}: {exc}") from exc
        offset = max(1, int(offset or 1))
        limit = max(1, min(int(limit or 160), 240))
        stop = min(len(lines), offset - 1 + limit)
        return {
            "path": display,
            "lineStart": offset,
            "lineEnd": stop,
            "totalLines": len(lines),
            "sourceMtimeNs": file.stat().st_mtime_ns,
            "truncated": stop < len(lines),
            "nextLine": stop + 1 if stop < len(lines) else None,
            "content": "\n".join(
                f"{number}: {line}"
                for number, line in enumerate(lines[offset - 1:stop], offset)
            ),
        }

    def directories(self):
        for mount, root in self.roots:
            yield mount
            for directory, dirs, _ in os.walk(root):
                dirs[:] = sorted(
                    name for name in dirs
                    if name not in CORE_IGNORED_DIRS and not name.startswith(".")
                    and (Path(directory) / name).resolve().is_relative_to(root)
                )
                for name in dirs:
                    relative = Path(directory, name).relative_to(root)
                    yield f"{mount}/{relative.as_posix()}"

    def glob(self, pattern, limit=100, kind="files", offset=0):
        if kind not in {"files", "directories"}:
            raise ValueError("kind 仅支持 files 或 directories")
        candidates = self.directories() if kind == "directories" else (path for _, path in self._walk_files())
        found = sorted({path for path in candidates if matches(path, pattern)})
        offset = max(0, int(offset or 0))
        cap = max(1, min(int(limit or 100), 200))
        page = found[offset:offset + cap]
        truncated = offset + len(page) < len(found)
        return {
            "paths": page,
            "truncated": truncated,
            "nextOffset": offset + len(page) if truncated else None,
        }

    def _search_files(self, pattern, path, glob, literal, case_sensitive, whole_word):
        expression = re.escape(pattern) if literal else pattern
        if whole_word:
            expression = r"(?<!\w)(?:" + expression + r")(?!\w)"
        compiled = re.compile(expression, 0 if case_sensitive else re.I)
        for file, display in self._walk_files(path):
            if glob and not matches(display, glob):
                continue
            if file.stat().st_size > 2_000_000:
                continue
            try:
                lines = file.read_text(encoding="utf-8-sig").splitlines()
            except (UnicodeError, OSError):
                continue
            yield display, lines, compiled

    def grep(self, pattern, path="", glob="", literal=False, limit=60,
             case_sensitive=True, whole_word=False, offset=0,
             output_mode="matches", context=0):
        if output_mode not in {"matches", "files"}:
            raise ValueError("output_mode 仅支持 matches 或 files")
        skip = max(0, int(offset or 0))
        cap = max(1, min(int(limit or 60), 120))
        if output_mode == "files":
            found = {}
            for display, lines, compiled in self._search_files(
                    pattern, path, glob, literal, case_sensitive, whole_word):
                hits = [number for number, line in enumerate(lines, 1)
                        if compiled.search(line)]
                if hits:
                    found[display] = {"path": display, "matches": len(hits),
                                      "sampleLines": hits[:5]}
            ordered = [found[path] for path in sorted(found)]
            page = ordered[skip:skip + cap]
            truncated = skip + len(page) < len(ordered)
            return {
                "files": page,
                "truncated": truncated,
                "nextOffset": skip + len(page) if truncated else None,
            }
        context = max(0, min(int(context or 0), 5))
        found = []
        for display, lines, compiled in self._search_files(
                pattern, path, glob, literal, case_sensitive, whole_word):
            for number, line in enumerate(lines, 1):
                if not compiled.search(line):
                    continue
                if skip:
                    skip -= 1
                    continue
                item = {"path": display, "line": number, "text": line[:600]}
                if context:
                    item["before"] = [
                        {"line": row, "text": lines[row - 1][:600]}
                        for row in range(max(1, number - context), number)
                    ]
                    item["after"] = [
                        {"line": row, "text": lines[row - 1][:600]}
                        for row in range(number + 1,
                                         min(len(lines), number + context) + 1)
                    ]
                found.append(item)
                if len(found) >= cap:
                    return {"matches": found, "truncated": True,
                            "nextOffset": int(offset or 0) + len(found)}
        return {"matches": found, "truncated": False, "nextOffset": None}

    def tools(self):
        from langchain_core.tools import tool

        def result(function, *args):
            try:
                return json.dumps(function(*args), ensure_ascii=False)
            except (OSError, ValueError, UnicodeError, re.error) as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        @tool
        def Read(path: str, offset: int = 1, limit: int = 160) -> str:
            """读取本轮授权资料（源码、需求或业务补充知识），返回真实行号和文件路径；截断后用 nextLine 续读。"""
            return result(self.read, path, offset, limit)

        @tool
        def Glob(pattern: str, limit: int = 100, kind: str = "files", offset: int = 0) -> str:
            """定位本轮授权文件或目录；** 可跨多层目录。列仓库用 pattern='repos/*', kind='directories'。"""
            return result(self.glob, pattern, limit, kind, offset)

        @tool
        def Grep(pattern: str, path: str = "", glob: str = "", literal: bool = False,
                 limit: int = 60, case_sensitive: bool = True, whole_word: bool = False,
                 offset: int = 0, output_mode: str = "matches", context: int = 0) -> str:
            """按原文检索本轮授权资料，默认区分大小写。宽范围先用 output_mode='files' 看候选文件与命中行数；确认文件后用 output_mode='matches'，context=2 可带命中行前后两行。命中只是线索，结论须 Read。"""
            return result(self.grep, pattern, path, glob, literal, limit,
                          case_sensitive, whole_word, offset, output_mode, context)

        return [Read, Glob, Grep]
