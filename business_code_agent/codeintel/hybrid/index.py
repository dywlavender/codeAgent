"""Explicit incremental index: Git diffs, structural units, reused embeddings."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from business_code_agent.codeintel.retrieval import collect_documents
from .chunks import structural_chunks


def diff_ranges(before, after):
    with tempfile.TemporaryDirectory(prefix="codeagent-diff-") as directory:
        a, b = Path(directory) / "before", Path(directory) / "after"
        a.write_text(before, encoding="utf-8")
        b.write_text(after, encoding="utf-8")
        result = subprocess.run(
            [
                "git",
                "diff",
                "--no-index",
                "--no-ext-diff",
                "--unified=0",
                "--",
                str(a),
                str(b),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode not in (0, 1):
            raise ValueError("Git diff 执行失败，请确认 Git 可用")
        return [
            {"start": int(m[0]), "lines": int(m[1] or 1)}
            for m in re.findall(r"^@@ .*? \+(\d+)(?:,(\d+))? @@", result.stdout, re.M)
        ]


def load_index(root):
    root = Path(root)
    value = json.loads((root / "index.json").read_text(encoding="utf-8"))
    vectors = np.load(root / value["vectorsFile"], allow_pickle=False)
    if len(vectors) != len(value["chunks"]):
        raise ValueError("索引的向量与片段数量不一致，请显式重新准备")
    return value, vectors


def update_index(root, sources, project_id, embedder, *, model_directory):
    started = time.monotonic()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    old, old_vectors = (
        load_index(root)
        if (root / "index.json").exists()
        else ({"files": {}, "chunks": []}, np.empty((0, 384)))
    )
    if (
        old.get("projectId", project_id) != project_id
        or old.get("sources", sources) != sources
    ):
        raise ValueError("不能将其他工程或来源目录的索引用于本工程")
    if old.get("modelDirectory", str(model_directory)) != str(model_directory):
        raise ValueError("嵌入模型已改变，请使用独立索引目录重建")
    observations = {}
    documents, skipped = collect_documents(
        sources, previous=old, observations=observations
    )
    files, chunks, changes = {}, [], []
    old_chunks = {}
    refresh_structure = bool(old["chunks"]) and old.get("chunkerVersion") != 2
    for chunk in old["chunks"]:
        old_chunks.setdefault(chunk["sourceId"] + "/" + chunk["path"], []).append(chunk)
    for document in documents:
        key = document["sourceId"] + "/" + document["path"]
        content = document["content"]
        files[key] = content
        unchanged = old["files"].get(key) == content
        if unchanged and not refresh_structure:
            chunks.extend(old_chunks.get(key, []))
            continue
        ranges = (
            diff_ranges(old["files"].get(key, ""), content)
            if key in old["files"] and not unchanged
            else [{"start": 1, "lines": len(content.splitlines())}]
        )
        if unchanged:
            ranges = []
        try:
            units = structural_chunks(document)
        except (SyntaxError, ImportError) as exc:
            raise ValueError(f"结构索引失败：{key} ({type(exc).__name__})") from exc
        chunks.extend(units)
        changes.append(
            {
                "path": key,
                "action": "rechunked"
                if unchanged
                else ("modified" if key in old["files"] else "added"),
                "ranges": ranges,
            }
        )
    for key in old["files"].keys() - files.keys():
        changes.append({"path": key, "action": "deleted", "ranges": []})
    if not changes and "version" in old:
        if old.get("sourceObservations") != observations:
            old = {**old, "sourceObservations": observations}
            temporary = root / "index.next.json"
            temporary.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
            temporary.replace(root / "index.json")
        return {
            **old,
            "update": {
                "changedFiles": 0,
                "embeddedChunks": 0,
                "reusedChunks": len(chunks),
                "seconds": round(time.monotonic() - started, 3),
                "changes": [],
            },
        }
    reusable = {
        (c["sourceId"], c["path"], c.get("symbolName", ""), c["content"]): old_vectors[
            i
        ]
        for i, c in enumerate(old["chunks"])
    }
    missing = [
        i
        for i, c in enumerate(chunks)
        if (c["sourceId"], c["path"], c.get("symbolName", ""), c["content"])
        not in reusable
    ]
    fresh = embedder.encode(
        [
            chunks[i]["path"]
            + "\n"
            + chunks[i].get("symbolName", "")
            + "\n"
            + chunks[i]["content"]
            for i in missing
        ]
    )
    replacements = dict(zip(missing, fresh))
    vectors = (
        np.stack(
            [
                replacements[i]
                if i in replacements
                else reusable[
                    (c["sourceId"], c["path"], c.get("symbolName", ""), c["content"])
                ]
                for i, c in enumerate(chunks)
            ]
        )
        if chunks
        else np.empty((0, 384), dtype=np.float32)
    )
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    vector_name = f"vectors-{version}.npy"
    np.save(root / vector_name, vectors)
    value = {
        "projectId": project_id,
        "chunkerVersion": 2,
        "version": version,
        "sources": sources,
        "modelDirectory": str(model_directory),
        "files": files,
        "sourceObservations": observations,
        "chunks": chunks,
        "vectorsFile": vector_name,
        "skipped": skipped,
        "update": {
            "changedFiles": sum(c["action"] != "rechunked" for c in changes),
            "rechunkedFiles": sum(c["action"] == "rechunked" for c in changes),
            "embeddedChunks": len(missing),
            "reusedChunks": len(chunks) - len(missing),
            "seconds": round(time.monotonic() - started, 3),
            "changes": changes,
        },
    }
    temporary = root / "index.next.json"
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(root / "index.json")
    return value
