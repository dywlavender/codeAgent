from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

import numpy as np

from business_code_agent.codeintel.hybrid.chunks import structural_chunks
from business_code_agent.codeintel.hybrid.index import update_index
from business_code_agent.codeintel.hybrid.search import fuse, assemble, HybridSearch


class Embeddings:
    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return np.asarray(
            [[1.0, float("repay" in t)] for t in texts], dtype=np.float32
        ).reshape(-1, 2)


class Reranker:
    def run(self, pairs, **kwargs):
        return np.arange(len(pairs), dtype=float)


class HybridTest(unittest.TestCase):
    def test_typescript_class_methods_and_arrow_functions(self):
        text = "export class Channel {\n state = 1;\n query() { return this.state; }\n}\nconst status = () => 2;\n"
        chunks = structural_chunks(self.doc(text, "channel.ts"))
        names = {c["symbolName"] for c in chunks if c["unitType"] == "function"}
        self.assertIn("Channel.query", names)
        self.assertIn("status", names)

    def doc(self, text, path="Ledger.java"):
        return {"sourceId": "repo", "kind": "source", "path": path, "content": text}

    def test_java_functions_classes_and_uncovered_fields_preserve_original(self):
        text = "package a;\nclass Ledger {\n int total;\n @Deprecated\n int repay() { return total; }\n int draw() { return 2; }\n}\n"
        chunks = structural_chunks(self.doc(text))
        self.assertEqual(2, len([c for c in chunks if c["unitType"] == "function"]))
        self.assertTrue(
            next(c for c in chunks if c["symbolName"].endswith("repay"))["deprecated"]
        )
        lines = text.splitlines()
        for c in chunks:
            self.assertEqual(
                "\n".join(lines[c["lineStart"] - 1 : c["lineEnd"]]), c["content"]
            )
        self.assertEqual(
            set(range(1, len(lines) + 1)),
            {n for c in chunks for n in range(c["lineStart"], c["lineEnd"] + 1)},
        )

    def test_python_class_and_decorated_function(self):
        text = "class Ledger:\n    total = 1\n    @staticmethod\n    def repay():\n        return 2\n"
        chunks = structural_chunks(self.doc(text, "ledger.py"))
        method = next(c for c in chunks if c["unitType"] == "function")
        self.assertEqual(3, method["lineStart"])
        self.assertIn("Ledger.repay", method["symbolName"])

    def test_incremental_git_diff_add_modify_delete_and_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "source"
            source.mkdir()
            file = source / "ledger.py"
            file.write_text("def repay():\n    return 1\n")
            sources = [{"id": "repo", "kind": "source", "root": str(source)}]
            model = Embeddings()
            update_index(
                root / "index", sources, "A", model, model_directory="test-models"
            )
            from unittest.mock import patch

            original_read = Path.read_text
            source_reads = []

            def read(path, *args, **kwargs):
                if path == file:
                    source_reads.append(path)
                return original_read(path, *args, **kwargs)

            with patch.object(Path, "read_text", read):
                same = update_index(
                    root / "index", sources, "A", model, model_directory="test-models"
                )
            self.assertEqual([], source_reads)
            self.assertEqual(0, same["update"]["embeddedChunks"])
            self.assertEqual(0, same["update"]["changedFiles"])
            file.write_text("# explanation\ndef repay():\n    return 2\n")
            changed = update_index(
                root / "index", sources, "A", model, model_directory="test-models"
            )
            self.assertEqual(1, changed["update"]["changedFiles"])
            self.assertTrue(changed["update"]["changes"][0]["ranges"])
            with self.assertRaises(ValueError):
                update_index(
                    root / "index", sources, "B", model, model_directory="test-models"
                )
            file.unlink()
            deleted = update_index(
                root / "index", sources, "A", model, model_directory="test-models"
            )
            self.assertEqual("deleted", deleted["update"]["changes"][0]["action"])
            self.assertEqual([], deleted["chunks"])

    def test_weighted_fusion_and_cross_encoder_top_50(self):
        lexical = [{"id": "a", "score": 100}, {"id": "b", "score": 90}]
        dense = [{"id": "b", "score": 0.9}, {"id": "a", "score": 0.5}]
        self.assertEqual("a", fuse(lexical, dense)[0]["id"])
        with self.assertRaises(ValueError):
            fuse(lexical, dense, bm25_weight=0.3, vector_weight=0.7)
        self.assertEqual(50, len(fuse([{"id": str(i)} for i in range(100)], [])))

    def test_context_line_character_caps_and_deprecation(self):
        candidates = structural_chunks(
            self.doc("@Deprecated\nclass Ledger {\n int repay() { return 1; }\n}\n")
        )
        result = assemble(candidates, char_limit=500, line_limit=2, per_unit_lines=1)
        self.assertLessEqual(result["chars"], 500)
        self.assertLessEqual(result["lines"], 2)
        self.assertIn("废弃标记", result["content"])
        self.assertIn("截断", result["content"])

    def test_long_first_unit_keeps_a_cited_prefix_with_its_continuation(self):
        candidates = structural_chunks(
            self.doc(
                "class Ledger {\n int first = 1;\n"
                + (" int value = 123;\n" * 100)
                + "}\n"
            )
        )
        result = assemble(candidates, char_limit=300)
        self.assertTrue(result["selected"])
        self.assertIn("int first = 1;", result["content"])
        self.assertIn("Read 续读", result["content"])
        self.assertLessEqual(result["chars"], 300)

    def test_hybrid_search_filters_sources_and_uses_reranker(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a = root / "a"
            b = root / "b"
            a.mkdir()
            b.mkdir()
            (a / "x.py").write_text("def repay():\n return 1\n")
            (b / "x.py").write_text("def repay():\n return 2\n")
            model = Embeddings()
            sources = [
                {"id": s, "kind": "source", "root": str(p)}
                for s, p in [("a", a), ("b", b)]
            ]
            update_index(root / "index", sources, "A", model, model_directory="models")
            search = HybridSearch(root / "index", embedder=model, reranker=Reranker())
            result = search.search("repay", allowed_sources=["a"])
            self.assertTrue(result["rerankApplied"])
            self.assertEqual({"a"}, {c["sourceId"] for c in result["candidates"]})

    def test_rerank_budget_fallback_is_explicit(self):
        import time

        class Slow:
            def run(self, pairs, **kwargs):
                time.sleep(0.01)
                return np.ones(len(pairs))

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "src"
            source.mkdir()
            (source / "x.py").write_text("def repay():\n return 1\n")
            model = Embeddings()
            update_index(
                root / "index",
                [{"id": "a", "kind": "source", "root": str(source)}],
                "A",
                model,
                model_directory="models",
            )
            result = HybridSearch(
                root / "index", embedder=model, reranker=Slow()
            ).search("repay", budget_ms=1)
            self.assertFalse(result["rerankApplied"])
            self.assertEqual("latency_budget", result["rerankReason"])
