"""Existing index reuse, frozen method positions, and complete-unit selection."""
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from business_code_agent.codeintel.retrieval import candidate_chars, candidate_prefix, pack_context, passages
from business_code_agent.codeintel.structure import existing_method_ranges, method_passages
from business_code_agent.evaluation.retrieval import (evaluate, freeze_structure, load_structure,
                                                       prepare, read_json, validate_selection, write_json)
from business_code_agent.platform.projects import ProjectContext
from business_code_agent.evaluation.retrieval_service import RetrievalEvaluationService
from business_code_agent.evaluation.fixed_context import answer_context
from tests.test_retrieval_evaluation import ContextRuntime


SOURCE = (Path(__file__).parent / "fixtures/retrieval_structure/Ledger.java").read_text(encoding="utf-8")


class RetrievalStructureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "Ledger.java").write_text(SOURCE)
        (repo / "settings.xml").write_text("<settings>normal configuration</settings>")
        config = self.root / "project.json"
        config.write_text(json.dumps({"project": {"id": "test"},
                                      "repositories": [{"id": "core", "localPath": "repo"}]}))
        self.context = ProjectContext.legacy(self.root / ".data/knowledge.db", config)
        self.context.data_root.mkdir()
        db = sqlite3.connect(self.context.db_path)
        self.addCleanup(db.close)
        db.executescript("""
            CREATE TABLE repository (id TEXT, root_path TEXT, indexed_at TEXT);
            CREATE TABLE code_file (id TEXT, repository_id TEXT, path TEXT);
            CREATE TABLE code_symbol (id TEXT,file_id TEXT,kind TEXT,name TEXT,qualified_name TEXT,line_start INT,line_end INT);
            CREATE TABLE code_fact (symbol_id TEXT,fact_type TEXT,evidence_id TEXT);
            CREATE TABLE evidence (id TEXT,excerpt TEXT);
        """)
        db.execute("INSERT INTO repository VALUES (?,?,?)", ("core", str(repo), (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()))
        db.execute("INSERT INTO code_file VALUES ('file','core','Ledger.java')")
        for name, first, last in [("settle", 7, 13), ("retry", 15, 17)]:
            db.execute("INSERT INTO code_symbol VALUES (?,?,?,?,?,?,?)", (name, "file", "METHOD", name, "sample.Ledger." + name, first, last))
            db.execute("INSERT INTO code_fact VALUES (?,'CODE_DECLARATION',?)", (name, name))
            db.execute("INSERT INTO evidence VALUES (?,?)", (name, SOURCE.splitlines()[first - 1].strip()))
        db.commit()
        self.db = db
        self.output = self.context.evaluations_root / "retrieval/retrieval-test"
        self.suite = {"id": "evidence", "projectId": "test", "cases": [{
            "id": "settle", "question": "settle checks closed and calls record", "split": "development",
            "evidence": [{"id": "condition-and-action", "role": "implementation", "alternatives": [[{
                "sourceId": "core", "path": "Ledger.java", "quote": "        if (closed) {\n            return;\n        }\n\n        record();"}]]}]}]}
        prepare(self.context, self.suite, self.output)

    def test_explicit_snapshot_then_evaluation_uses_only_frozen_positions(self):
        structure = freeze_structure(self.context, self.output)
        self.assertEqual(2, structure["methodCount"])
        self.assertFalse(self.context.ast_root.exists())
        (self.root / "repo/Ledger.java").write_text("changed live source")
        with patch("business_code_agent.evaluation.retrieval.existing_method_ranges", side_effect=AssertionError("no implicit index export")):
            result = evaluate(self.output, "test", strategy="methods", retrieval_unit="methods",
                              structure_version=structure["versionId"], context_policy="whole", budgets=[2000])
        row = read_json(self.output / "strategies/methods/settle-2000.json")
        self.assertEqual(1, row["contextMetrics"]["coverage"])
        self.assertIn("record();", row["context"])
        self.assertEqual("methods", result["retrievalUnit"])
        self.assertEqual(structure["versionId"], result["structureVersionId"])
        answered = answer_context(self.output, "test", "settle", strategy="methods", budget=2000,
                                  model="test-model", runtime=ContextRuntime("test-model"))
        self.assertEqual("methods", answered["retrievalUnit"])
        self.assertEqual(structure["versionId"], answered["structureVersionId"])
        replay = evaluate(self.output, "test", strategy="methods-replay", candidate_source="methods", budgets=[2000], context_policy="whole")
        self.assertTrue(replay["items"][0]["comparison"]["candidatesUnchanged"])
        self.assertFalse(self.context.ast_root.exists())

    def test_methods_preserve_conditions_comments_fields_and_fallback_text(self):
        structure = freeze_structure(self.context, self.output)
        documents = read_json(self.output / "corpus.json")["documents"]
        items = method_passages(documents, structure)
        body = next(item for item in items if item.get("symbolName") == "sample.Ledger.settle")
        self.assertIn("/** Finish", body["content"])
        self.assertIn("if (closed)", body["content"])
        self.assertTrue(body["content"].rstrip().endswith("}"))
        java_lines = {n: text for item in items if item["path"] == "Ledger.java"
                      for n, text in enumerate(item["content"].split("\n"), item["lineStart"])}
        self.assertEqual(dict(enumerate(SOURCE.splitlines(), 1)), java_lines)
        original = passages([d for d in documents if d["path"] == "settings.xml"])
        fallback = [item for item in items if item["path"] == "settings.xml"]
        self.assertEqual(original, [{k: v for k, v in item.items() if k != "unitType"} for item in fallback])

    def test_missing_structure_does_not_parse_or_generate(self):
        with self.assertRaisesRegex(ValueError, "不会自动生成"):
            evaluate(self.output, "test", strategy="methods", retrieval_unit="methods", structure_version="index-absent")
        self.assertFalse((self.output / "structures").exists())
        self.assertFalse(self.context.ast_root.exists())

    def test_project_and_snapshot_boundaries_with_identical_repo_name(self):
        structure = freeze_structure(self.context, self.output)
        path = self.output / "structures" / f"{structure['versionId']}.json"
        write_json(path, {**structure, "projectId": "another-project"})
        with self.assertRaisesRegex(ValueError, "工程"):
            load_structure(self.output, "test", structure["versionId"])
        write_json(path, {**structure, "snapshotId": "another-batch"})
        with self.assertRaisesRegex(ValueError, "冻结版本"):
            load_structure(self.output, "test", structure["versionId"])
        self.db.execute("UPDATE repository SET root_path=?", (str(self.root / "another-repo"),))
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "仓库目录"):
            freeze_structure(self.context, self.output)

    def test_changed_source_does_not_reuse_old_boundaries(self):
        (self.root / "repo/Ledger.java").write_text(SOURCE.replace("record();", "recordTwice();"))
        with self.assertRaisesRegex(ValueError, "没有通过"):
            freeze_structure(self.context, self.output)
        self.assertFalse((self.output / "structures").exists())

    def test_service_reads_metadata_and_only_exports_on_explicit_action(self):
        write_json(self.output / "state.json", {"id": self.output.name, "projectId": "test", "status": "completed"})
        service = RetrievalEvaluationService(self.context)
        self.assertEqual([], service.get_run(self.output.name)["structures"])
        evaluate(self.output, "test", budgets=[2000])
        with self.assertRaisesRegex(ValueError, "请选择"):
            service.compare_methods(self.output.name, {"budgets": [2000]})
        self.assertFalse((self.output / "structures").exists())
        metadata = service.save_structure(self.output.name)
        self.assertEqual(2, metadata["methodCount"])
        self.assertNotIn("files", metadata)
        self.assertEqual(1, len(service.get_run(self.output.name)["structures"]))
        compared = service.compare_methods(self.output.name, {"budgets": [2000], "structureVersionId": metadata["versionId"]})
        self.assertEqual("lexical", compared["comparisonSource"])
        self.assertFalse(compared["items"][0]["comparison"]["candidatesUnchanged"])
        self.assertFalse(self.context.ast_root.exists())

    def test_whole_policy_skips_oversized_unit_instead_of_truncating_its_guard(self):
        def item(path, text):
            return {"id": path, "sourceId": "core", "kind": "source", "path": path,
                    "lineStart": 1, "lineEnd": text.count("\n") + 1, "content": text, "unitType": "method"}
        large = item("Large.java", "if (closed) return;\n" + "write();\n" * 100)
        small = item("Small.java", "boolean ready() { return true; }")
        selected, context = pack_context([large, small], 200, policy="whole")
        self.assertLessEqual(len(context), 200)
        self.assertEqual(["Small.java"], [p["path"] for p in selected])
        validate_selection([large, small], selected)

    def test_matching_candidate_budget_uses_only_baseline_input_size(self):
        structure = freeze_structure(self.context, self.output)
        evaluate(self.output, "test", budgets=[2000])
        baseline = read_json(self.output / "strategies/lexical/settle-2000.json")
        result = evaluate(self.output, "test", strategy="matched", retrieval_unit="methods",
                          structure_version=structure["versionId"], comparison_source="lexical",
                          match_candidate_budget=True, candidate_limit=500, budgets=[2000])
        row = result["items"][0]
        self.assertEqual(candidate_chars(baseline["candidates"]), row["candidateBudgetChars"])
        self.assertLessEqual(row["candidateChars"], row["candidateBudgetChars"])
        self.assertEqual("lexical", result["candidateBudgetSource"])
        self.assertEqual(500, result["candidateLimit"])

    def test_candidate_budget_keeps_a_complete_ranked_prefix(self):
        candidates = passages([{"sourceId": "core", "kind": "source", "path": "Ledger.java", "content": SOURCE}], max_lines=14)
        budget = candidate_chars(candidates[:1])
        self.assertEqual(candidates[:1], candidate_prefix(candidates, budget))


if __name__ == "__main__":
    unittest.main()
