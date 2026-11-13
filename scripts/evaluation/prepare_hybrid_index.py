"""Explicitly download local models and build/update one project's index."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from business_code_agent.platform.projects import ProjectContext, ProjectRegistry
from business_code_agent.evaluation.retrieval import project_sources
from business_code_agent.codeintel.hybrid.index import update_index
from business_code_agent.codeintel.hybrid.models import (
    EMBEDDING_MODEL,
    RERANK_MODEL,
    load_model,
)


def download_models(directory):
    for name, model in [("embedding", EMBEDDING_MODEL), ("reranker", RERANK_MODEL)]:
        target = directory / name
        with urlopen(
            "https://huggingface.co/api/models/" + model, timeout=30
        ) as response:
            names = [x["rfilename"] for x in json.load(response)["siblings"]]
        choices = [
            "onnx/model_qint8_arm64.onnx",
            "onnx/model_quantized.onnx",
            "onnx/model_quint8_avx2.onnx",
            "onnx/model_qint8_avx512.onnx",
        ]
        weight = next((n for n in choices if n in names), None)
        if not weight:
            raise ValueError("模型没有预期的量化 ONNX 文件：" + model)
        for remote, local in [
            ("config.json", "config.json"),
            ("tokenizer.json", "tokenizer.json"),
            (weight, "onnx/model_quantized.onnx"),
        ]:
            path = target / local
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            print("Downloading", model, remote, flush=True)
            temporary = path.with_suffix(".download")
            with (
                urlopen(
                    "https://huggingface.co/" + model + "/resolve/main/" + remote,
                    timeout=120,
                ) as response,
                temporary.open("wb") as output,
            ):
                shutil.copyfileobj(response, output)
            temporary.replace(path)
        (target / "origin.json").write_text(
            json.dumps({"model": model, "weights": weight})
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-config", type=Path)
    parser.add_argument("--project-registry", type=Path)
    parser.add_argument("--project-id")
    parser.add_argument("--index-root", type=Path)
    parser.add_argument(
        "--model-directory", type=Path, default=Path(".data/retrieval-models")
    )
    parser.add_argument("--download-models", action="store_true")
    parser.add_argument("--models-only", action="store_true")
    args = parser.parse_args()
    if args.download_models:
        download_models(args.model_directory.resolve())
    if args.models_only:
        return
    if args.project_registry:
        context = ProjectRegistry(args.project_registry).get(args.project_id)
    elif args.project_config:
        context = ProjectContext.legacy(
            args.project_config.parent / ".data/knowledge.db", args.project_config
        )
    else:
        parser.error("需要工程配置或注册表")
    embedder = load_model(str(args.model_directory.resolve() / "embedding"))
    embedder.progress = lambda done, total: print(
        f"Embedded {done}/{total}", flush=True
    )
    result = update_index(
        args.index_root or context.data_root / "retrieval-index",
        project_sources(context),
        context.project_id,
        embedder,
        model_directory=args.model_directory.resolve(),
    )
    print(
        json.dumps(
            {
                "version": result["version"],
                "files": len(result["files"]),
                "chunks": len(result["chunks"]),
                "changedFiles": result["update"]["changedFiles"],
                "embeddedChunks": result["update"]["embeddedChunks"],
                "reusedChunks": result["update"]["reusedChunks"],
                "seconds": result["update"]["seconds"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
