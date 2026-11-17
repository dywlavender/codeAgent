"""Structure navigation over an existing, scoped AST version."""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from itertools import islice
from pathlib import Path, PurePosixPath

from business_code_agent.codeintel.analysis import CALLABLE_KINDS, RESOLVABLE_TYPE_KINDS, RESOLVED_EDGE_FACTS
from business_code_agent.storage.connections import open_sqlite

from .material import AstMaterial

_SYMBOL_COLUMNS = """s.id, s.name, s.kind, s.qualified_name, s.line_start, s.line_end,
                     c.path, c.repository_id"""
_SYMBOLS = f"""SELECT {_SYMBOL_COLUMNS}
               FROM code_symbol s JOIN code_file c ON c.id=s.file_id"""
_FACTS = f"""SELECT {_SYMBOL_COLUMNS}, f.id AS relation_id, f.fact_type, f.target,
                    e.line_start AS fact_start, e.line_end AS fact_end
            FROM code_fact f JOIN code_symbol s ON s.id=f.symbol_id
            JOIN code_file c ON c.id=s.file_id
            JOIN evidence e ON e.id=f.evidence_id AND e.source_id=c.id"""


@dataclass(frozen=True)
class AstRepository:
    repository_id: str
    root: Path
    mount: str


class AstQueryError(ValueError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


class AstReader:
    """Read saved symbols and edges without writing the index or parsing source."""

    def __init__(self, material: AstMaterial, version_id: str,
                 repositories: tuple[AstRepository, ...], *, project_id: str | None = None):
        self.material = material
        self.version_id = version_id
        self.repositories = repositories
        self.project_id = project_id or material.project_id

    def check(self, *, verify_sources: bool = True) -> None:
        """Check availability; callers may reuse a just-checked source status."""
        with self._open(verify_sources=verify_sources):
            pass

    def find_symbol(self, query: str, repository_id: str | None = None, limit: int = 20) -> dict:
        query = _text(query, "query").removesuffix("()").strip().lower()
        if not query:
            raise AstQueryError("invalid_input", "请提供类名、方法名或全限定名")
        limit = _limit(limit)
        with self._open() as (db, allowed):
            repositories = tuple(key for key, repo in allowed.items()
                                 if repository_id is None or repo.repository_id == repository_id)
            if not repositories:
                raise AstQueryError("denied", "指定仓库不在本轮授权范围")
            rows = db.execute(
                f"""{_SYMBOLS}
                    WHERE c.repository_id IN ({_marks(repositories)})
                      AND (instr(lower(s.qualified_name), ?) > 0
                           OR instr(lower(s.name), ?) > 0)
                    ORDER BY CASE WHEN lower(s.qualified_name)=? THEN 0
                                  WHEN lower(s.name)=? THEN 1 ELSE 2 END,
                             CASE WHEN s.kind='FIELD' THEN 1 ELSE 0 END,
                             s.qualified_name,c.repository_id,c.path,s.line_start
                    LIMIT ?""",
                (*repositories, query, query, query, query, limit + 1),
            ).fetchall()
            symbols = [self._location(row, allowed) for row in rows[:limit]]
        return self._result(symbols=symbols, count=len(symbols), truncated=len(rows) > limit)

    def get_symbol(self, symbol_id: str) -> dict:
        with self._open() as (db, allowed):
            symbol = self._location(self._symbol(db, allowed, symbol_id), allowed)
        return self._result(symbol=symbol, count=1)

    def get_source(self, symbol_id: str, relation_id: str | None = None) -> dict:
        with self._open() as (db, allowed):
            row = self._symbol(db, allowed, symbol_id)
            if relation_id is None:
                location = self._location(row, allowed)
            else:
                kinds = (*RESOLVED_EDGE_FACTS, "CALL")
                row = db.execute(
                    f"{_FACTS} WHERE f.id=? AND f.symbol_id=? AND f.fact_type IN ({_marks(kinds)})",
                    (_text(relation_id, "relationId"), row["id"], *kinds),
                ).fetchone()
                if row is None:
                    raise AstQueryError("not_found", "未找到该符号的关系位置")
                location = self._relation_location(row, allowed)["location"]
            start = max(1, location["lineStart"] - 3)
            end = min(start + 59, location["lineEnd"] + 3)
            path = allowed[row["repository_id"]].root / PurePosixPath(row["path"])
            try:
                with path.open(encoding="utf-8", errors="replace") as stream:
                    lines = list(islice(stream, start - 1, end))
            except OSError as exc:
                raise AstQueryError("unavailable", "此位置的源码暂不可读") from exc
        text = "".join(lines)
        return self._result(path=location["path"], lineStart=start, lineEnd=start + len(lines) - 1,
                            focusLine=location["lineStart"], content=text[:16000],
                            truncated=location["lineEnd"] > end or len(text) > 16000)

    def find_relations(self, symbol_id: str, relation_type: str | None = None,
                       direction: str = "outgoing", limit: int = 20) -> dict:
        if relation_type is not None and relation_type not in RESOLVED_EDGE_FACTS:
            raise AstQueryError("invalid_input", "relationType 必须是 CALLS、INHERITS 或 IMPLEMENTS")
        if direction not in {"incoming", "outgoing"}:
            raise AstQueryError("invalid_input", "direction 必须是 incoming 或 outgoing")
        limit = _limit(limit)
        relations, unresolved, raw_calls = [], [], []
        with self._open() as (db, allowed):
            symbol = self._symbol(db, allowed, symbol_id)
            repository = symbol["repository_id"]
            edge_types = (relation_type,) if relation_type else RESOLVED_EDGE_FACTS
            incoming = direction == "incoming"
            selector = "f.target" if incoming else "f.symbol_id"
            value = symbol["qualified_name"] if incoming else symbol["id"]
            candidates = {}
            rows = db.execute(
                f"""{_FACTS} WHERE c.repository_id=? AND {selector}=?
                        AND f.fact_type IN ({_marks(edge_types)})
                    ORDER BY c.path,e.line_start,f.id LIMIT ?""",
                (repository, value, *edge_types, limit + 1),
            ).fetchall()
            truncated = len(rows) > limit
            for row in rows[:limit]:
                kind, target_name = row["fact_type"], row["target"]
                kinds = CALLABLE_KINDS if kind == "CALLS" else RESOLVABLE_TYPE_KINDS
                if incoming and symbol["kind"] not in kinds:
                    continue
                key = (target_name, kinds)
                if key not in candidates:
                    candidates[key] = db.execute(
                        f"""{_SYMBOLS} WHERE c.repository_id=? AND s.qualified_name=?
                              AND s.kind IN ({_marks(kinds)}) LIMIT 2""",
                        (repository, target_name, *kinds),
                    ).fetchall()
                targets = candidates[key]
                edge = self._relation_location(row, allowed)
                if len(targets) == 1 and (not incoming or targets[0]["id"] == symbol["id"]):
                    relations.append({**edge, "status": "resolved", "to": self._location(targets[0], allowed)})
                else:
                    status = "ambiguous" if len(targets) > 1 else "unresolved"
                    unresolved.append({**edge, "status": status})
            if not incoming and relation_type in (None, "CALLS"):
                rows = db.execute(
                    f"""{_FACTS} WHERE f.symbol_id=? AND f.fact_type='CALL'
                        ORDER BY e.line_start,f.id LIMIT ?""",
                    (symbol["id"], limit + 1),
                ).fetchall()
                raw_calls = [{**self._relation_location(row, allowed), "status": "raw"}
                             for row in rows[:limit]]
                truncated = truncated or len(rows) > limit
        return self._result(relations=relations, unresolved=unresolved, rawCalls=raw_calls,
                            count=len(relations) + len(unresolved) + len(raw_calls),
                            truncated=truncated)

    @contextmanager
    def _open(self, *, verify_sources: bool = True):
        state = self.material._index()
        if not state.get("currentVersionId"):
            raise AstQueryError("missing", "尚无可查询的 AST 版本")
        if state.get("currentVersionId") != self.version_id:
            raise AstQueryError("stale", "本轮绑定的 AST 已不是当前版本")
        if state.get("status") == "stale":
            raise AstQueryError("stale", "AST 版本需要手动更新")
        if state.get("status") != "available":
            raise AstQueryError("unavailable", "本轮绑定的 AST 版本暂不可用")
        version = self.material.version(self.version_id)
        if version is None:
            raise AstQueryError("missing", "此 AST 版本没有可用的结构索引，请手动重新生成")
        _, metadata, database = version
        if metadata.get("projectId") != self.project_id or metadata.get("versionId") != self.version_id:
            raise AstQueryError("unavailable", "AST 版本或工程归属不匹配")
        stamp = metadata.get("sourceStamp")
        allowed, snapshot_ids = self._repository_scope(metadata)
        if verify_sources and self.material._current_source_stamp(stamp, write_cache=False) != stamp:
            raise AstQueryError("stale", "源码已变化，结构索引需要手动更新")
        try:
            with closing(open_sqlite(database, readonly=True)) as db:
                db.row_factory = sqlite3.Row
                indexed = {row[0] for row in db.execute("SELECT DISTINCT repository_id FROM code_file")}
                if not indexed <= snapshot_ids:
                    raise AstQueryError("unavailable", "AST 索引的仓库标识与版本记录不一致")
                yield db, allowed
        except sqlite3.Error as exc:
            raise AstQueryError("unavailable", "读取 AST 结构索引失败") from exc

    def _repository_scope(self, metadata: dict) -> tuple[dict[str, AstRepository], set[str]]:
        stamp = metadata.get("sourceStamp")
        entries = stamp.get("repositories") if isinstance(stamp, dict) else None
        if not entries or metadata.get("sourceRepositories", entries) != entries:
            raise AstQueryError("unavailable", "此 AST 版本缺少可确认的仓库映射")
        try:
            saved = {entry["id"]: Path(entry["path"]).resolve() for entry in entries}
        except (KeyError, TypeError, ValueError) as exc:
            raise AstQueryError("unavailable", "此 AST 版本缺少可确认的仓库映射") from exc
        legacy_map = {f"repo-{index}": repo for index, repo in enumerate(saved)}
        mapping = metadata.get("repositoryMap", legacy_map)
        if len(saved) != len(entries) or mapping != legacy_map:
            raise AstQueryError("unavailable", "此 AST 版本的仓库映射无法确认")
        requested = {repo.repository_id: repo for repo in self.repositories}
        if not requested or any(saved.get(key) != repo.root.resolve() for key, repo in requested.items()):
            raise AstQueryError("stale", "AST 版本与本轮授权仓库目录不一致")
        return {snapshot: requested[repo] for snapshot, repo in mapping.items() if repo in requested}, set(mapping)

    @staticmethod
    def _symbol(db, allowed, symbol_id):
        symbol_id = _text(symbol_id, "symbolId")
        row = db.execute(
            f"{_SYMBOLS} WHERE s.id=? AND c.repository_id IN ({_marks(allowed)})",
            (symbol_id, *allowed),
        ).fetchone()
        if row is None:
            raise AstQueryError("not_found", "在本轮授权仓库中未找到该符号")
        return row

    def _location(self, row, allowed) -> dict:
        repo = allowed[row["repository_id"]]
        path = PurePosixPath(row["path"])
        mount = PurePosixPath(repo.mount)
        source = repo.root / path
        if (path.is_absolute() or ".." in path.parts or "\\" in row["path"]
                or len(mount.parts) != 2 or mount.parts[0] != "repos" or mount.name == ".."
                or not source.resolve().is_relative_to(repo.root.resolve()) or not source.is_file()):
            raise AstQueryError("unavailable", "AST 索引中的源码位置无法映射到授权仓库")
        return {"symbolId": row["id"], "repositoryId": repo.repository_id,
                "qualifiedName": row["qualified_name"], "name": row["name"], "kind": row["kind"],
                "path": (mount / path).as_posix(), "lineStart": row["line_start"],
                "lineEnd": row["line_end"], "versionId": self.version_id}

    def _relation_location(self, row, allowed) -> dict:
        source = self._location(row, allowed)
        return {"relationId": row["relation_id"], "relationType": row["fact_type"],
                "from": source, "targetName": row["target"],
                "location": {"path": source["path"], "lineStart": row["fact_start"],
                             "lineEnd": row["fact_end"]}}

    def _result(self, **data) -> dict:
        return {"status": "ok", "kind": "navigation", "versionId": self.version_id, **data}


def _marks(values) -> str:
    return ",".join("?" for _ in values)


def _limit(limit: int) -> int:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise AstQueryError("invalid_input", "limit 必须是 1 到 50 的整数")
    return limit


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not (1 <= len(value.strip()) <= 256):
        raise AstQueryError("invalid_input", f"{field} 必须是 1 到 256 字符的文本")
    return value.strip()
