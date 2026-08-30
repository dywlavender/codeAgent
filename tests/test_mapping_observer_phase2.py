from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from business_code_agent.code_intelligence import JavaIndexer
from business_code_agent.knowledge_update.baseline_service import BaselineKnowledgeService
from business_code_agent.knowledge_update.mapping_observer import MappingObservationService
from business_code_agent.query_agent.api import make_server
from business_code_agent.query_agent.service import QueryService
from business_code_agent.schema import connect


class _Extractor:
    def extract(self, *, source_path: str, text: str):
        return {
            "entities": [{
                "type": "BUSINESS_TERM", "name": "极优", "aliases": ["JY"],
                "definition": "再担保类型产品", "attributes": {"codeHints": ["JY"]},
                "sourceQuote": "极优是再担保类型产品，代码中一般用 JY 表示。",
            }],
            "relations": [],
        }


class MappingObserverPhase2Test(unittest.TestCase):
    def test_query_creates_candidate_without_changing_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            baseline = root / "baseline"
            baseline.mkdir()
            (baseline / "business.md").write_text(
                "# 业务基线\n\n## 极优\n\n极优是再担保类型产品，代码中一般用 JY 表示。\n",
                encoding="utf-8",
            )
            repository = root / "repo"
            repository.mkdir()
            (repository / "GuaranteeFileTask.java").write_text(
                """public class GuaranteeFileTask {
    void run(String productCode) {
        if (productCode.equals("JY")) sender.pushJYFile();
    }
}
""",
                encoding="utf-8",
            )
            config = root / "project.json"
            config.write_text(json.dumps({"knowledge": {"baselineRoot": "baseline"}}), encoding="utf-8")
            db_path = root / "knowledge.db"
            db = connect(str(db_path))
            JavaIndexer(db).ingest(str(repository), "guarantee-service")
            BaselineKnowledgeService(db, project_config=config, extractor=_Extractor()).refresh()

            result = QueryService(db, db_path=str(db_path)).query("极优是否推送文件？")

            self.assertEqual("SUFFICIENT", result["evidenceStatus"])
            self.assertTrue(result["mappingSuggestions"])
            observation = result["mappingSuggestions"][0]
            self.assertEqual("CANDIDATE", observation["status"])
            self.assertEqual("ENTITY", observation["businessType"])
            self.assertTrue(observation["evidenceIds"])
            self.assertEqual("VERIFIED", db.execute(
                "SELECT status FROM business_entity WHERE id=?", (observation["businessId"],)
            ).fetchone()[0])

            accepted = MappingObservationService(db).accept(observation["id"], "确认该类是实现入口")
            self.assertEqual("ACCEPTED", accepted["status"])
            mapping = db.execute(
                "SELECT status,source_type FROM business_code_mapping WHERE business_id=? AND code_reference=?",
                (observation["businessId"], observation["codeReference"]),
            ).fetchone()
            self.assertEqual(("VERIFIED", "QUERY_REVIEW"), (mapping["status"], mapping["source_type"]))

            # The value of MVP2 is not only that a row is persisted.  A later
            # differently worded question must navigate through that verified
            # mapping and load the previously discovered code symbol.
            second = QueryService(db, db_path=str(db_path)).query(
                "极优的实现入口在哪里？", conversation_id="CONV-MAPPING-REUSE"
            )
            self.assertIn("CODE", second["metrics"]["sourceCoverage"])
            second_code = " ".join(
                item["statement"] for item in second["answer"]["facts"] if item["sourceType"] == "CODE"
            )
            self.assertIn(observation["codeReference"], second_code)
            checkpoints = db.execute(
                "SELECT state_json FROM query_checkpoint WHERE run_id=? ORDER BY sequence",
                (second["runId"],),
            ).fetchall()
            self.assertTrue(any(
                observation["codeReference"] in row["state_json"] for row in checkpoints
            ))

            # A static remap must not erase a mapping that an administrator
            # already confirmed from a query.
            BaselineKnowledgeService(db, project_config=config, extractor=_Extractor()).rebuild_mappings()
            persisted = db.execute(
                "SELECT status,source_type FROM business_code_mapping WHERE business_id=? AND code_reference=?",
                (observation["businessId"], observation["codeReference"]),
            ).fetchone()
            self.assertEqual(("VERIFIED", "QUERY_REVIEW"), (persisted["status"], persisted["source_type"]))
            db.close()

    def test_rejected_candidate_is_not_materialized(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            db = connect(handle.name)
            db.execute("INSERT INTO business_baseline_source VALUES ('S','/tmp/a.md','a','1','a','ACTIVE','now')")
            db.execute("INSERT INTO business_entity VALUES ('B','BUSINESS_TERM','业务','[]','业务','{}','HUMAN','S',NULL,1,'VERIFIED','now')")
            db.execute("INSERT INTO repository VALUES ('R','/tmp','now')")
            db.execute("INSERT INTO code_file VALUES ('F','R','/tmp/a.java','v')")
            db.execute("INSERT INTO code_symbol VALUES ('C','F','CLASS','BusinessService','BusinessService',1,2)")
            db.execute("INSERT INTO evidence VALUES ('EB','BUSINESS','B','1','/tmp/a.md',1,1,NULL,'b','业务')")
            db.execute("INSERT INTO evidence_lifecycle VALUES ('EB','ACTIVE',NULL,NULL,NULL)")
            db.execute("INSERT INTO evidence VALUES ('E','CODE','C','1','/tmp/a.java',1,1,NULL,'v','调用')")
            db.execute("INSERT INTO evidence_lifecycle VALUES ('E','ACTIVE',NULL,NULL,NULL)")
            db.commit()
            service = MappingObservationService(db)
            items = service.observe_query(
                "RUN-1", "业务在哪里实现？", {
                    "evidenceStatus": "SUFFICIENT",
                    "businessCandidates": [{"id": "B"}],
                    "codeCandidates": [],
                    "evidence": [{"evidenceId": "E", "sourceType": "CODE", "sourceId": "C", "content": "业务"}],
                    "answer": {"facts": [
                        {"sourceType": "BUSINESS", "evidenceIds": ["EB"]},
                        {"sourceType": "CODE", "evidenceIds": ["E"]},
                    ]},
                },
            )
            self.assertTrue(items)
            service.reject(items[0]["id"], "不是该业务")
            self.assertEqual(0, db.execute("SELECT count(*) FROM business_code_mapping").fetchone()[0])
            db.close()

    def test_http_review_flow(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "knowledge.db"
            db = connect(str(db_path))
            db.execute("INSERT INTO business_baseline_source VALUES ('S','/tmp/a.md','a','1','a','ACTIVE','now')")
            db.execute("INSERT INTO business_entity VALUES ('B','BUSINESS_TERM','业务','[]','业务','{}','HUMAN','S',NULL,1,'VERIFIED','now')")
            db.execute("INSERT INTO repository VALUES ('R','/tmp','now')")
            db.execute("INSERT INTO code_file VALUES ('F','R','/tmp/a.java','v')")
            db.execute("INSERT INTO code_symbol VALUES ('C','F','CLASS','BusinessService','BusinessService',1,2)")
            db.execute("INSERT INTO evidence VALUES ('E','CODE','C','1','/tmp/a.java',1,1,NULL,'v','调用')")
            db.execute("INSERT INTO evidence_lifecycle VALUES ('E','ACTIVE',NULL,NULL,NULL)")
            db.execute(
                """INSERT INTO business_code_mapping_observation
                   (id,run_id,question,business_type,business_id,relation_type,code_symbol_id,code_reference,
                    status,confidence,evidence_ids_json,reason,created_at,reviewed_at,reviewer_note)
                   VALUES ('O','RUN','业务在哪里','ENTITY','B','REPRESENTED_BY','C','BusinessService',
                    'CANDIDATE',0.8,'[\"E\"]','需要确认','now',NULL,'')""",
            )
            db.commit(); db.close()
            server = make_server(str(db_path), port=0, project_config=str(root / "missing.json"))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                listing = json.loads(urlopen(base + "/api/knowledge/mapping-observations?status=CANDIDATE").read())
                self.assertEqual("O", listing["items"][0]["id"])
                accepted = _json_post(base + "/api/knowledge/mapping-observations/O/accept", {"note": "确认"})
                self.assertEqual("ACCEPTED", accepted["status"])
                detail = json.loads(urlopen(base + "/api/knowledge/mapping-observations/O").read())
                self.assertEqual("ACCEPTED", detail["status"])
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_search_only_business_candidates_are_not_observed(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            db = connect(handle.name)
            db.execute("INSERT INTO business_baseline_source VALUES ('S','/tmp/a.md','a','1','a','ACTIVE','now')")
            db.execute("INSERT INTO business_entity VALUES ('B1','BUSINESS_TERM','业务一','[]','业务一','{}','HUMAN','S',NULL,1,'VERIFIED','now')")
            db.execute("INSERT INTO business_entity VALUES ('B2','BUSINESS_TERM','业务二','[]','业务二','{}','HUMAN','S',NULL,1,'VERIFIED','now')")
            db.execute("INSERT INTO repository VALUES ('R','/tmp','now')")
            db.execute("INSERT INTO code_file VALUES ('F','R','/tmp/a.java','v')")
            db.execute("INSERT INTO code_symbol VALUES ('C','F','CLASS','BusinessService','BusinessService',1,2)")
            db.execute("INSERT INTO evidence VALUES ('EB1','BUSINESS','B1','1','/tmp/a.md',1,1,NULL,'b1','业务一')")
            db.execute("INSERT INTO evidence_lifecycle VALUES ('EB1','ACTIVE',NULL,NULL,NULL)")
            db.execute("INSERT INTO evidence VALUES ('EC','CODE','C','1','/tmp/a.java',1,1,NULL,'c','业务一')")
            db.execute("INSERT INTO evidence_lifecycle VALUES ('EC','ACTIVE',NULL,NULL,NULL)")
            db.commit()
            items = MappingObservationService(db).observe_query(
                "RUN-SEARCH-CANDIDATES", "业务一在哪里实现？", {
                    "evidenceStatus": "SUFFICIENT",
                    "businessCandidates": [{"id": "B1"}, {"id": "B2"}],
                    "evidence": [
                        {"evidenceId": "EB1", "sourceType": "BUSINESS", "sourceId": "B1", "content": "业务一"},
                        {"evidenceId": "EC", "sourceType": "CODE", "sourceId": "C", "content": "业务一"},
                    ],
                    "answer": {"facts": [
                        {"sourceType": "BUSINESS", "evidenceIds": ["EB1"]},
                        {"sourceType": "CODE", "evidenceIds": ["EC"]},
                    ]},
                },
            )
            self.assertTrue(items)
            self.assertEqual({"B1"}, {item["businessId"] for item in items})
            db.close()


def _json_post(url: str, body: dict) -> dict:
    request = Request(
        url, method="POST", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urlopen(request).read())


if __name__ == "__main__":
    unittest.main()
