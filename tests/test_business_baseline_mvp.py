from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from business_code_agent.business_tools import BusinessTools
from business_code_agent.code_intelligence import JavaIndexer
from business_code_agent.knowledge_update.baseline_service import BaselineKnowledgeService
from business_code_agent.knowledge_graph import KnowledgeGraphService
from business_code_agent.query_agent.retriever import QueryRetriever
from business_code_agent.query_agent.agent import BusinessCodeQueryAgent
from business_code_agent.schema import connect


ROOT = Path(__file__).resolve().parent.parent


BASELINE = """# 提款业务基线

## 极优

极优是再担保类型产品，代码中一般用 JY 表示。

## 担保处理

极优提款成功后，需要进行担保后处理，代码入口通常包含 GuaranteeFileTask。
"""


class _Extractor:
    def extract(self, *, source_path: str, text: str):
        return {
            "entities": [
                {
                    "type": "BUSINESS_TERM", "name": "极优", "aliases": ["JY"],
                    "definition": "再担保类型产品", "attributes": {"codeHints": ["JY"]},
                    "sourceQuote": "极优是再担保类型产品，代码中一般用 JY 表示。",
                },
                {
                    "type": "CAPABILITY", "name": "担保处理", "aliases": [],
                    "definition": "提款成功后执行担保后处理", "attributes": {"codeHints": ["GuaranteeFileTask"]},
                    "sourceQuote": "极优提款成功后，需要进行担保后处理，代码入口通常包含 GuaranteeFileTask。",
                },
            ],
            "relations": [
                {
                    "from": "极优提款成功", "relation": "TRIGGERS", "to": "担保处理",
                    "scope": "极优产品", "attributes": {},
                    "sourceQuote": "极优提款成功后，需要进行担保后处理，代码入口通常包含 GuaranteeFileTask。",
                }
            ],
        }


class BusinessBaselineMvpTest(unittest.TestCase):
    def test_safe_fallback_imports_the_six_mvp_knowledge_kinds(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            db = connect(handle.name)
            result = BaselineKnowledgeService(
                db, project_config=ROOT / "project.config.example.json"
            ).refresh(map_code=False, use_model=False)
            self.assertEqual({
                "BUSINESS_TERM": 1, "CAPABILITY": 1, "FLOW": 1,
                "RELATION": 1, "RULE": 1, "SYSTEM": 1,
            }, result["entityCounts"])
            db.close()

    def test_natural_baseline_is_structured_persisted_and_mapped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline_root = root / "baseline"
            baseline_root.mkdir()
            (baseline_root / "jiyou.md").write_text(BASELINE, encoding="utf-8")
            code_root = root / "repo"
            code_root.mkdir()
            (code_root / "GuaranteeFileTask.java").write_text(
                """public class GuaranteeFileTask {
    public void execute() {
        guaranteeFileSender.pushJYFile();
    }
}
""",
                encoding="utf-8",
            )
            config = root / "project.json"
            config.write_text(json.dumps({"knowledge": {"baselineRoot": "baseline"}}), encoding="utf-8")
            db = connect(str(root / "knowledge.db"))
            JavaIndexer(db).ingest(str(code_root), "guarantee-service")
            service = BaselineKnowledgeService(db, project_config=config, extractor=_Extractor())

            result = service.refresh()

            self.assertEqual(1, result["sourceCount"])
            self.assertEqual(1, result["entityCounts"]["BUSINESS_TERM"])
            self.assertEqual(1, result["entityCounts"]["CAPABILITY"])
            self.assertEqual(1, result["entityCounts"]["RELATION"])
            items = service.list_entities()
            self.assertEqual({"极优", "担保处理"}, {item["name"] for item in items})
            capability = next(item for item in items if item["name"] == "担保处理")
            self.assertTrue(any("GuaranteeFileTask" in item["codeReference"] for item in capability["mappings"]))
            self.assertEqual("VERIFIED", capability["status"])
            self.assertTrue(capability["sourceEvidenceId"])

            candidates = BusinessTools(db).search_business_knowledge("极优提款担保")
            self.assertTrue(any(item["knowledge_type"] == "RELATION" for item in candidates))
            detail = BusinessTools(db).get_business_knowledge(capability["id"])
            self.assertTrue(detail["evidence"])
            self.assertTrue(any(item["target_type"] == "CODE_SYMBOL" for item in detail["relations"]))
            graph = KnowledgeGraphService(db).search("担保处理")
            self.assertTrue(any(node["type"] == "CAPABILITY" for node in graph["nodes"]))
            self.assertTrue(any(node["type"] == "CODE" for node in graph["nodes"]))
            initial = QueryRetriever(db).initial_search({"searchTerms": ["极优提款担保"]})
            self.assertTrue(initial["business_candidates"])
            expanded = QueryRetriever(db).expand({
                "question": "极优提款担保", "business_candidates": initial["business_candidates"],
                "code_candidates": [], "requirement_candidates": [], "evidence_gaps": [],
            })
            self.assertTrue(any(item.get("target_id") or item.get("targetId") for item in expanded["code_candidates"]))
            answer = BusinessCodeQueryAgent(db).run("担保处理是什么，由什么代码实现？")
            self.assertIn("BUSINESS", {item["sourceType"] for item in answer["answer"]["facts"]})
            self.assertIn("CODE", {item["sourceType"] for item in answer["answer"]["facts"]})
            db.close()

    def test_model_output_without_literal_source_quote_is_rejected(self):
        class Ungrounded:
            def extract(self, **_kwargs):
                return {"entities": [{
                    "type": "RULE", "name": "不存在规则", "definition": "模型猜测内容",
                    "sourceQuote": "原文中不存在的内容",
                }], "relations": []}

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline_root = root / "baseline"; baseline_root.mkdir()
            (baseline_root / "baseline.md").write_text(BASELINE, encoding="utf-8")
            config = root / "project.json"
            config.write_text(json.dumps({"knowledge": {"baselineRoot": "baseline"}}), encoding="utf-8")
            db = connect(str(root / "knowledge.db"))
            with self.assertRaises(ValueError):
                BaselineKnowledgeService(db, project_config=config, extractor=Ungrounded()).refresh()
            db.close()

    def test_model_generated_aliases_and_code_hints_without_source_are_dropped(self):
        class HallucinatedHints:
            def extract(self, **_kwargs):
                return {"entities": [{
                    "type": "CAPABILITY", "name": "担保处理",
                    "aliases": ["Guarantee", "不存在别名"],
                    "definition": "模型自行补充的担保处理定义",
                    "attributes": {
                        "codeHints": ["GuaranteeFileTask"],
                        "steps": ["原文没有写过的步骤"],
                        "systems": ["UnknownSystem"],
                    },
                    "sourceQuote": "极优提款成功后，需要进行担保后处理。",
                }], "relations": []}

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline_root = root / "baseline"; baseline_root.mkdir()
            source = "# 业务基线\n\n## 担保处理\n\n极优提款成功后，需要进行担保后处理。\n"
            (baseline_root / "baseline.md").write_text(source, encoding="utf-8")
            config = root / "project.json"
            config.write_text(json.dumps({"knowledge": {"baselineRoot": "baseline"}}), encoding="utf-8")
            db = connect(str(root / "knowledge.db"))
            BaselineKnowledgeService(db, project_config=config, extractor=HallucinatedHints()).refresh(map_code=False)
            entity = db.execute("SELECT aliases_json,attributes_json,definition FROM business_entity").fetchone()
            self.assertIsNotNone(entity)
            self.assertEqual([], json.loads(entity["aliases_json"]))
            self.assertEqual({}, json.loads(entity["attributes_json"]))
            self.assertIn("极优提款成功后", entity["definition"])
            db.close()


if __name__ == "__main__":
    unittest.main()
