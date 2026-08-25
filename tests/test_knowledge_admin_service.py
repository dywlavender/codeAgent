from __future__ import annotations

import tempfile
import unittest
import shutil
from pathlib import Path

from business_code_agent.business_tools import BusinessTools
from business_code_agent.code_intelligence import JavaIndexer
from business_code_agent.knowledge_update.service import KnowledgeAdminService
from business_code_agent.query_agent.agent import BusinessCodeQueryAgent
from business_code_agent.requirement.service import RequirementService
from business_code_agent.schema import connect


class KnowledgeAdminServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = connect(str(Path(self.temp.name) / "admin.db"))
        self.service = KnowledgeAdminService(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_manual_source_runs_review_publish_and_query_consumes_only_published(self):
        generated = self.service.generate({
            "sourceType": "ADMIN_NOTE",
            "sourceId": "note-1",
            "content": "取消订单功能负责关闭未完成订单并释放库存。",
            "functionName": "取消订单",
        })
        proposal_id = generated["id"]
        self.assertEqual("PENDING_REVIEW", generated["status"])
        self.assertEqual([proposal_id], [item["id"] for item in self.service.list_pending()])
        self.assertFalse(BusinessTools(self.db).search_business_knowledge("取消订单"))

        result = self.service.review(proposal_id, "ACCEPT", reviewer="admin-a")
        self.assertEqual("PUBLISHED", result["proposal"]["status"])
        functions = self.service.list_functions("取消订单")
        self.assertEqual(1, len(functions))
        self.assertEqual("取消订单", functions[0]["name"])
        self.assertGreaterEqual(functions[0]["evidenceCount"], 1)

        cards = BusinessTools(self.db).search_business_knowledge("取消订单")
        function_card = next(item for item in cards if item["id"].startswith("BF-"))
        detail = BusinessTools(self.db).get_business_knowledge(function_card["id"])
        self.assertEqual("CONFIRMED", detail["knowledge"]["status"])
        self.assertTrue(detail["evidence"])
        answer = BusinessCodeQueryAgent(self.db).run("取消订单功能是做什么的？")["answer"]
        self.assertIn("关闭未完成订单", answer["conclusion"])
        self.assertTrue(any(item["sourceType"] == "BUSINESS" for item in answer["facts"]))

    def test_deferred_proposal_remains_unpublished(self):
        generated = self.service.generate({
            "sourceType": "USER_FEEDBACK",
            "sourceId": "feedback-1",
            "content": "退款失败后不是直接重试，需要管理员确认。",
        })
        proposal_id = generated["id"]
        reviewed = self.service.review(proposal_id, "DEFER", reviewer="admin-b", comment="等待业务确认")
        self.assertEqual("DEFERRED", reviewed["proposal"]["status"])
        self.assertFalse(self.service.list_functions())
        self.assertEqual([proposal_id], [item["id"] for item in self.service.list_pending()])
        accepted = self.service.review(proposal_id, "ACCEPT", reviewer="admin-b")
        self.assertEqual("PUBLISHED", accepted["proposal"]["status"])

    def test_invalid_evidence_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.service.generate({
                "sourceType": "REQUIREMENT",
                "sourceId": "REQ-1",
                "content": "新增订单取消规则",
                "evidenceIds": ["EV-NOT-FOUND"],
            })

    def test_code_change_source_uses_indexed_facts_not_only_pasted_description(self):
        fixture = Path(__file__).resolve().parent.parent / "examples" / "business_phase2" / "java"
        JavaIndexer(self.db).ingest(str(fixture), "order-repo")
        generated = self.service.generate({
            "sourceType": "CODE_CHANGE",
            "sourceId": "order-repo",
            "content": "分析这次代码变化对业务功能的影响",
        })
        self.assertEqual("PENDING_REVIEW", generated["status"])
        self.assertTrue(any(item["source_type"] == "CODE" for item in generated["evidence"]))
        detail = self.service.repository.get_proposal(generated["id"])
        self.assertTrue(detail["items"][0]["evidence_ids"])

    def test_requirement_source_uses_digest_and_chunk_evidence(self):
        document = Path(self.temp.name) / "cancel.md"
        document.write_text(
            "# 取消订单\n\n已支付订单取消后创建退款申请。取消完成后释放库存。",
            encoding="utf-8",
        )
        RequirementService(self.db).import_document(
            str(document), requirement_id="REQ-CANCEL", title="取消订单需求"
        )
        generated = self.service.generate({
            "sourceType": "REQUIREMENT",
            "sourceId": "REQ-CANCEL",
            "content": "分析这个需求并生成业务知识提案",
        })
        self.assertEqual("PENDING_REVIEW", generated["status"])
        self.assertTrue(any(item["source_type"] == "REQUIREMENT" for item in generated["evidence"]))

    def test_target_function_name_resolves_to_versioned_update(self):
        created = self.service.generate({
            "sourceType": "ADMIN_NOTE", "sourceId": "note-create",
            "content": "取消订单负责关闭订单。", "functionName": "取消订单",
        })
        self.service.review(created["id"], "ACCEPT", reviewer="admin")
        update = self.service.generate({
            "sourceType": "ADMIN_NOTE", "sourceId": "note-update",
            "content": "取消订单完成后还需要释放库存。", "targetFunctionId": "取消订单",
        })
        self.assertEqual("UPDATE", update["action"])
        self.assertTrue(update["base_version_id"])

    def test_changed_code_evidence_creates_one_function_revalidation_proposal(self):
        source = Path(__file__).resolve().parent.parent / "examples" / "business_phase2" / "java"
        repository_root = Path(self.temp.name) / "repository"
        shutil.copytree(source, repository_root)
        JavaIndexer(self.db).ingest(str(repository_root), "change-repo")
        fact = self.db.execute(
            """SELECT cf.evidence_id,cs.id symbol_id,cfile.path
                 FROM code_fact cf JOIN code_symbol cs ON cs.id=cf.symbol_id
                 JOIN code_file cfile ON cfile.id=cs.file_id
                WHERE cfile.repository_id='change-repo' ORDER BY cfile.path LIMIT 1"""
        ).fetchone()
        proposal = self.service.repository.create_proposal(
            "建立代码关联功能", "MANUAL", "setup", {
                "name": "代码关联功能", "summary": "验证代码变化传播",
                "evidence_ids": [fact["evidence_id"]],
                "entries": [{
                    "entry_type": "METHOD", "label": "测试入口",
                    "target_type": "CODE_SYMBOL", "target_id": fact["symbol_id"],
                    "locator": fact["path"], "evidence_ids": [fact["evidence_id"]],
                }],
            }, "test",
        )
        proposal_id = proposal["proposal"]["id"]
        self.service.repository.add_proposal_item(
            proposal_id, "ADD", "FUNCTION", after={"name": "代码关联功能"},
            evidence_ids=[fact["evidence_id"]],
        )
        self.service.repository.submit_proposal(proposal_id)
        self.service.repository.review_proposal(proposal_id, "ACCEPT", "admin")
        self.service.repository.publish_proposal(proposal_id, "admin")
        function_id = self.service.repository.list_functions(status="PUBLISHED")[0]["id"]

        changed_file = repository_root / fact["path"]
        changed_file.write_text(changed_file.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")
        JavaIndexer(self.db).ingest(str(repository_root), "change-repo")
        pending = [item for item in self.service.list_pending() if item["trigger_type"] == "SOURCE_MODIFIED"]
        self.assertEqual(1, len(pending))
        self.assertEqual("代码关联功能", pending[0]["after"]["name"])
        self.assertEqual("STALE", BusinessTools(self.db).get_business_knowledge(function_id)["knowledge"]["status"])
        answer = BusinessCodeQueryAgent(self.db).run("代码关联功能是做什么的？")["answer"]
        self.assertFalse(any(item["sourceType"] == "BUSINESS" for item in answer["facts"]))
        self.service.review(pending[0]["id"], "ACCEPT", reviewer="admin")
        self.assertEqual("CONFIRMED", BusinessTools(self.db).get_business_knowledge(function_id)["knowledge"]["status"])

        changed_file.write_text(changed_file.read_text(encoding="utf-8") + "\n// changed again\n", encoding="utf-8")
        JavaIndexer(self.db).ingest(str(repository_root), "change-repo")
        rejected = [item for item in self.service.list_pending() if item["trigger_type"] == "SOURCE_MODIFIED"]
        self.service.review(rejected[0]["id"], "REJECT", reviewer="admin")
        self.assertEqual("STALE", BusinessTools(self.db).get_business_knowledge(function_id)["knowledge"]["status"])

        changed_file.unlink()
        JavaIndexer(self.db).ingest(str(repository_root), "change-repo")
        deleted = [item for item in self.service.list_pending() if item["trigger_type"] == "SOURCE_DELETED"]
        self.service.review(deleted[0]["id"], "ACCEPT", reviewer="admin")
        detail = BusinessTools(self.db).get_business_knowledge(function_id)
        self.assertEqual("STALE", detail["knowledge"]["status"])
        self.assertTrue(all(item["status"] == "STALE" for item in detail["relations"]))

    def test_new_requirement_version_revalidates_linked_function(self):
        document = Path(self.temp.name) / "versioned.md"
        document.write_text("# 退款规则\n\n退款失败后进入人工处理。", encoding="utf-8")
        requirements = RequirementService(self.db)
        first = requirements.import_document(
            str(document), requirement_id="REQ-REFUND", title="退款规则"
        )
        evidence_id = self.db.execute(
            "SELECT evidence_id FROM requirement_chunk_v2 WHERE requirement_version_id=? LIMIT 1",
            (first["versionId"],),
        ).fetchone()[0]
        proposal = self.service.repository.create_proposal(
            "建立退款知识", "REQUIREMENT", first["versionId"], {
                "name": "退款处理", "summary": "处理退款失败场景",
                "evidence_ids": [evidence_id],
                "rules": [{"statement": "退款失败后进入人工处理", "evidence_ids": [evidence_id]}],
            }, "test",
        )
        proposal_id = proposal["proposal"]["id"]
        self.service.repository.add_proposal_item(
            proposal_id, "ADD", "FUNCTION", after={"name": "退款处理"},
            evidence_ids=[evidence_id],
        )
        self.service.repository.submit_proposal(proposal_id)
        self.service.repository.review_proposal(proposal_id, "ACCEPT", "admin")
        self.service.repository.publish_proposal(proposal_id, "admin")
        function_id = self.service.repository.list_functions(status="PUBLISHED")[0]["id"]

        document.write_text("# 退款规则\n\n退款失败后进入自动结果查询。", encoding="utf-8")
        requirements.import_document(str(document), requirement_id="REQ-REFUND", title="退款规则")
        pending = [
            item for item in self.service.list_pending()
            if item["trigger_type"] == "REQUIREMENT_VERSION_CHANGED"
        ]
        self.assertEqual(1, len(pending))
        self.assertEqual("退款处理", pending[0]["after"]["name"])
        self.assertEqual("STALE", BusinessTools(self.db).get_business_knowledge(function_id)["knowledge"]["status"])
        self.service.review(pending[0]["id"], "ACCEPT", reviewer="admin")
        self.assertEqual("CONFIRMED", BusinessTools(self.db).get_business_knowledge(function_id)["knowledge"]["status"])


if __name__ == "__main__":
    unittest.main()
