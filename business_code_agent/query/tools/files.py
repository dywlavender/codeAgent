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
    """File access bound to one run's ready source and requirement roots."""

    def __init__(self, workspace, materials):
        self.workspace = Path(workspace).resolve()
        roots = []
        for source in materials:
            if not isinstance(source, MaterialSource) or not source.available:
                continue
            if source.kind not in {"source", "requirements"}:
                continue
            root = Path(source.directory).resolve()
            if not root.is_dir():
                continue
            roots.append((str(source.path).rstrip("/"), root))
        if not roots:
            raise ValueError("本轮没有可授权的源码或需求目录")
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
        prefix = str(path or "").strip().strip("/")
        for mount, root in self.roots:
            if prefix and prefix != mount and not prefix.startswith(mount + "/"):
                continue
            for directory, dirs, names in os.walk(root):
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
                    if file.suffix.lower() not in CORE_SOURCE_SUFFIXES and name not in CORE_FILE_NAMES:
                        continue
                    display = f"{mount}/{relative.as_posix()}"
                    if prefix and display != prefix and not display.startswith(prefix + "/"):
                        continue
                    yield file, display

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

    def grep(self, pattern, path="", glob="", literal=False, limit=60,
             case_sensitive=True, whole_word=False, offset=0):
        prefix = str(path or "").strip().strip("/")
        if prefix and not any(prefix == mount or prefix.startswith(mount + "/") for mount, _ in self.roots):
            raise ValueError("path 不在本轮授权资料范围内")
        expression = re.escape(pattern) if literal else pattern
        if whole_word:
            expression = r"(?<!\w)(?:" + expression + r")(?!\w)"
        compiled = re.compile(expression, 0 if case_sensitive else re.I)
        cap = max(1, min(int(limit or 60), 120))
        skip = max(0, int(offset or 0))
        found = []
        for file, display in self._walk_files(prefix):
            if glob and not matches(display, glob):
                continue
            if file.stat().st_size > 2_000_000:
                continue
            try:
                lines = file.read_text(encoding="utf-8-sig").splitlines()
            except (UnicodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                if not compiled.search(line):
                    continue
                if skip:
                    skip -= 1
                    continue
                found.append({"path": display, "line": number, "text": line[:600]})
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
            """读取授权源码或需求原文，返回真实行号和文件路径；截断后用 nextLine 续读。"""
            return result(self.read, path, offset, limit)

        @tool
        def Glob(pattern: str, limit: int = 100, kind: str = "files", offset: int = 0) -> str:
            """定位授权文件或目录；** 可跨多层目录。列仓库用 pattern='repos/*', kind='directories'。"""
            return result(self.glob, pattern, limit, kind, offset)

        @tool
        def Grep(pattern: str, path: str = "", glob: str = "", literal: bool = False,
                 limit: int = 60, case_sensitive: bool = True, whole_word: bool = False,
                 offset: int = 0) -> str:
            """按原文行检索授权资料，默认区分大小写；命中只是线索，结论须 Read。"""
            return result(self.grep, pattern, path, glob, literal, limit,
                          case_sensitive, whole_word, offset)

        return [Read, Glob, Grep]
