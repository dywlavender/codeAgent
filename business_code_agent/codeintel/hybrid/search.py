"""Parallel BM25+dense retrieval, weighted RRF, budgeted Top-50 reranking."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from business_code_agent.codeintel.retrieval import TextRetriever
from .index import load_index, read_manifest
from .models import load_model


def fuse(lexical, dense, *, bm25_weight=0.6, vector_weight=0.4, limit=50):
    if bm25_weight < vector_weight or vector_weight < 0:
        raise ValueError("BM25 权重不得低于向量权重，权重必须非负")
    values = {}
    for items, weight in ((lexical, bm25_weight), (dense, vector_weight)):
        for rank, item in enumerate(items, 1):
            value = values.setdefault(item["id"], {**item, "score": 0.0})
            value["score"] += weight / (60 + rank)
    return sorted(values.values(), key=lambda x: (-x["score"], x["id"]))[:limit]


def assemble(candidates, *, char_limit=12000, line_limit=300, per_unit_lines=100):
    blocks, selected, used_lines = [], [], 0
    for item in candidates:
        remaining = min(per_unit_lines, line_limit - used_lines)
        if remaining <= 0:
            break
        header = f"[{item['kind']}] {item['sourceId']}/{item['path']}:{item['lineStart']}-{item['lineEnd']}"
        if item.get("deprecated") or "@Deprecated" in item["content"]:
            header += " [源码含废弃标记]"
        all_lines = item["content"].splitlines()
        lines = all_lines[:remaining]
        while lines:
            clipped = len(lines) < len(all_lines)
            continuation = (
                f" [已截断，下一行 {item['lineStart'] + len(lines)}，请 Read 续读]"
                if clipped
                else ""
            )
            rendered = [
                f"{item['lineStart'] + i}: {line}" for i, line in enumerate(lines)
            ]
            block = header + continuation + "\n" + "\n".join(rendered)
            if len("\n\n".join(blocks + [block])) <= char_limit:
                break
            lines.pop()
        if not lines:
            continue
        blocks.append(block)
        used_lines += len(lines)
        selected.append(
            {
                **item,
                "lineEnd": item["lineStart"] + len(lines) - 1,
                "content": "\n".join(lines),
                "truncated": clipped,
            }
        )
    return {
        "content": "\n\n".join(blocks),
        "selected": selected,
        "lines": used_lines,
        "chars": len("\n\n".join(blocks)),
    }


class HybridSearch:
    def __init__(self, root, *, embedder=None, reranker=None, manifest=None):
        self.root = Path(root)
        self.index, self.vectors = load_index(root, manifest)
        models = Path(self.index["modelDirectory"])
        self.embedder = embedder or load_model(str(models / "embedding"))
        self.reranker = reranker or load_model(str(models / "reranker"), True)
        self.lexical = TextRetriever(self.index["chunks"])

    def search(self, question, *, allowed_sources=None, budget_ms=200, rerank=True):
        started = time.monotonic()
        allowed = (
            set(allowed_sources)
            if allowed_sources is not None
            else {s["id"] for s in self.index["sources"]}
        )
        permitted = [
            i for i, c in enumerate(self.index["chunks"]) if c["sourceId"] in allowed
        ]
        allowed_ids = {self.index["chunks"][i]["id"] for i in permitted}

        def sparse():
            return [
                x
                for x in self.lexical.search(question, limit=len(self.index["chunks"]))
                if x["id"] in allowed_ids
            ][:80]

        def dense():
            query = self.embedder.encode([question])[0]
            scores = self.vectors[permitted] @ query
            return [
                {**self.index["chunks"][permitted[i]], "score": float(scores[i])}
                for i in np.argsort(-scores)[:80]
            ]

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(sparse), pool.submit(dense)
            candidates = fuse(a.result(), b.result())
        retrieval_seconds = time.monotonic() - started
        rank_started = time.monotonic()
        applied = False
        reason = None
        if candidates and rerank:
            try:
                scores = self.reranker.run(
                    [(question, c["path"] + "\n" + c["content"]) for c in candidates],
                    budget_ms=budget_ms,
                )
                elapsed = (time.monotonic() - rank_started) * 1000
                if elapsed <= budget_ms:
                    candidates = [
                        {**c, "rerankScore": float(score)}
                        for c, score in sorted(
                            zip(candidates, scores), key=lambda x: -float(x[1])
                        )
                    ]
                    applied = True
                else:
                    reason = "latency_budget"
            except Exception as exc:
                reason = (
                    "latency_budget"
                    if (time.monotonic() - rank_started) * 1000 >= budget_ms
                    else type(exc).__name__
                )
        return {
            "candidates": candidates,
            "timings": {
                "retrievalSeconds": round(retrieval_seconds, 4),
                "rerankMs": round((time.monotonic() - rank_started) * 1000, 2),
            },
            "rerankApplied": applied,
            "rerankReason": reason,
            "rerankBudgetMs": budget_ms,
            "rerankCount": len(candidates) if rerank else 0,
            "weights": {"bm25": 0.6, "vector": 0.4},
            "indexVersion": self.index["version"],
        }


@dataclass(frozen=True)
class SearchSnapshot:
    root: Path
    version: str
    manifest: dict = field(compare=False, repr=False)

    @classmethod
    def open(cls, root, project_id):
        root = Path(root).resolve()
        manifest = read_manifest(root)
        if project_id and manifest["projectId"] != project_id:
            raise ValueError("检索索引不属于当前工程")
        if not (root / manifest["vectorsFile"]).is_file():
            raise FileNotFoundError("缺少混合检索的向量文件")
        models = Path(manifest["modelDirectory"])
        for role in ("embedding", "reranker"):
            if not all((models / role / name).is_file()
                       for name in ("tokenizer.json", "config.json", "onnx/model_quantized.onnx")):
                raise FileNotFoundError(f"缺少混合检索的本地 {role} 模型文件")
        return cls(root, manifest["version"], manifest)


@lru_cache(maxsize=4)
def cached_search(snapshot):
    return HybridSearch(snapshot.root, manifest=snapshot.manifest)


def search_tool(snapshot, files):
    from langchain_core.tools import tool

    manifest = snapshot.manifest
    allowed = [
        s["id"]
        for s in manifest["sources"]
        if Path(s["root"]).resolve() in {r for _, r in files.roots}
    ]

    @tool
    def SearchCode(question: str) -> str:
        """按业务问题混合搜索函数、类和资料，返回行号原文、废弃/截断标记。跨系统连接和大方法需继续 Read 核实。"""
        result = cached_search(snapshot).search(
            question, allowed_sources=allowed
        )
        current, stale = [], []
        roots = {s["id"]: Path(s["root"]).resolve() for s in manifest["sources"]}
        checked = {}
        for item in result.pop("candidates"):
            key = item["sourceId"] + "/" + item["path"]
            if key not in checked:
                file = roots[item["sourceId"]] / item["path"]
                try:
                    files.locate(str(file))
                    checked[key] = (
                        file.read_text(encoding="utf-8-sig") == manifest["files"][key]
                    )
                except (OSError, ValueError):
                    checked[key] = False
            if checked[key]:
                current.append(item)
            elif key not in stale:
                stale.append(key)
        packed = assemble(current)
        mounts = {root: mount for mount, root in files.roots}
        locations = [
            {
                "path": mounts[roots[c["sourceId"]]] + "/" + c["path"],
                "lineStart": c["lineStart"],
                "lineEnd": c["lineEnd"],
                "nextLine": c["lineEnd"] + 1 if c["truncated"] else None,
            }
            for c in packed["selected"]
        ]
        return json.dumps(
            {
                **result,
                "content": packed["content"],
                "locations": locations,
                "lines": packed["lines"],
                "chars": packed["chars"],
                "staleLocations": stale,
                "staleNotice": "索引已过期的条目仅供定位，必须 Read 当前原文"
                if stale
                else None,
            },
            ensure_ascii=False,
        )

    return SearchCode
