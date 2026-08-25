from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from business_code_agent.knowledge_update import KnowledgeGovernanceRepository
from business_code_agent.schema import connect


class KnowledgeGovernanceRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "knowledge.db"
        self.db = connect(str(self.path))
        self.repo = KnowledgeGovernanceRepository(self.db)
        self.evidence_ids = ("EV-CODE-1", "EV-REQ-1")
        for evidence_id, source_type in zip(self.evidence_ids, ("CODE", "REQUIREMENT")):
            self.db.execute(
                """INSERT INTO evidence
                   (id,source_type,source_id,source_version,locator,content_hash,excerpt)
                   VALUES (?, ?, ?, 'v1', ?, 'fixture', ?)""",
                (evidence_id, source_type, evidence_id, f"/{evidence_id}", f"evidence {evidence_id}"),
            )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def snapshot(self, *, rule="已支付订单取消后创建退款申请"):
        return {
            "name": "取消订单",
            "domain": "订单",
            "summary": "关闭订单并释放相关资源",
            "evidence_ids": ["EV-REQ-1"],
            "scenarios": [{
                "id": "SCENE-USER", "name": "用户主动取消",
                "summary": "用户取消未完成订单", "evidence_ids": ["EV-REQ-1"],
            }],
            "rules": [{
                "id": "RULE-PAID", "statement": rule,
                "conditions": ["订单已支付"], "result": "创建退款申请",
                "evidence_ids": ["EV-CODE-1", "EV-REQ-1"],
            }],
            "entries": [{
                "id": "ENTRY-HTTP", "entry_type": "HTTP", "label": "取消订单接口",
                "target_type": "METHOD", "target_id": "OrderController.cancel",
                "locator": "POST /orders/{id}/cancel", "evidence_ids": ["EV-CODE-1"],
            }],
            "data_impacts": [{
                "id": "DATA-ORDER", "object_type": "BUSINESS_OBJECT", "object_name": "订单状态",
                "operation": "STATE_CHANGE", "before_state": "WAIT_PAY", "after_state": "CANCELLED",
                "evidence_ids": ["EV-CODE-1"],
            }],
        }

    def propose_and_publish(self, snapshot=None):
        proposal = self.repo.create_proposal(
            "建立取消订单知识", "MANUAL", "admin-input-1", snapshot or self.snapshot(), "agent"
        )
        proposal_id = proposal["proposal"]["id"]
        function_id = proposal["proposal"]["target_function_id"]
        self.repo.add_proposal_item(
            proposal_id, "ADD", "BUSINESS_FUNCTION", after={"name": "取消订单"},
            rationale="代码和需求共同证明", confidence=0.95, evidence_ids=self.evidence_ids,
        )
        self.repo.submit_proposal(proposal_id)
        self.repo.review_proposal(proposal_id, "APPROVE", "reviewer", "确认业务语义")
        return proposal_id, function_id, self.repo.publish_proposal(proposal_id, "publisher")

    def test_create_review_publish_complete_function_knowledge(self):
        proposal_id, function_id, published = self.propose_and_publish()
        self.assertEqual(function_id, published["function"]["id"])
        self.assertEqual("PUBLISHED", published["function"]["status"])
        self.assertEqual(1, published["version"]["version"])
        self.assertEqual("取消订单", published["snapshot"]["name"])
        self.assertEqual(["EV-REQ-1"], published["snapshot"]["evidence_ids"])
        self.assertEqual(["EV-CODE-1", "EV-REQ-1"], published["snapshot"]["rules"][0]["evidence_ids"])
        self.assertEqual("HTTP", published["snapshot"]["entries"][0]["entry_type"])
        self.assertEqual("WAIT_PAY", published["snapshot"]["data_impacts"][0]["before_state"])

        proposal = self.repo.get_proposal(proposal_id)
        self.assertEqual("PUBLISHED", proposal["proposal"]["status"])
        self.assertEqual("APPROVE", proposal["reviews"][0]["decision"])
        self.assertEqual(list(self.evidence_ids), proposal["items"][0]["evidence_ids"])
        self.assertEqual([function_id], [item["id"] for item in self.repo.list_functions()])

        self.db.close()
        self.db = connect(str(self.path))
        self.repo = KnowledgeGovernanceRepository(self.db)
        self.assertEqual("取消订单", self.repo.get_function(function_id)["snapshot"]["name"])

    def test_update_creates_immutable_version_and_rejects_stale_proposal(self):
        _, function_id, first = self.propose_and_publish()
        base_version = first["version"]["id"]
        proposal = self.repo.create_proposal(
            "修改退款规则", "GIT_CHANGE", "change-2",
            self.snapshot(rule="已支付订单取消后进入异步退款流程"), "update-agent",
            target_function_id=function_id, action="UPDATE", base_version_id=base_version,
        )
        proposal_id = proposal["proposal"]["id"]
        self.repo.add_proposal_item(
            proposal_id, "MODIFY", "RULE", target_id="RULE-PAID",
            before={"statement": "已支付订单取消后创建退款申请"},
            after={"statement": "已支付订单取消后进入异步退款流程"},
            evidence_ids=["EV-CODE-1"], confidence=0.8,
        )
        self.repo.submit_proposal(proposal_id)
        self.repo.review_proposal(proposal_id, "APPROVE", "reviewer")
        second = self.repo.publish_proposal(proposal_id, "publisher")

        self.assertEqual(2, second["version"]["version"])
        self.assertEqual("已支付订单取消后进入异步退款流程", second["snapshot"]["rules"][0]["statement"])
        self.assertEqual("已支付订单取消后创建退款申请", self.repo.get_function(function_id, 1)["snapshot"]["rules"][0]["statement"])
        self.assertEqual([1, 2], [item["version"] for item in self.repo.list_versions(function_id)])

        stale = self.repo.create_proposal(
            "再修改", "MANUAL", "change-3", self.snapshot(), "agent",
            target_function_id=function_id, action="UPDATE",
        )
        stale_id = stale["proposal"]["id"]
        self.repo.submit_proposal(stale_id)
        self.repo.review_proposal(stale_id, "APPROVE", "reviewer")
        # A separate publication advances the function beyond stale_id's base.
        fresh = self.repo.create_proposal(
            "并发修改", "MANUAL", "change-4", self.snapshot(), "agent",
            target_function_id=function_id, action="UPDATE",
        )
        fresh_id = fresh["proposal"]["id"]
        self.repo.submit_proposal(fresh_id)
        self.repo.review_proposal(fresh_id, "APPROVE", "reviewer")
        self.repo.publish_proposal(fresh_id, "publisher")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.repo.publish_proposal(stale_id, "publisher")

    def test_state_machine_prevents_unreviewed_publication(self):
        proposal = self.repo.create_proposal(
            "建立知识", "MANUAL", "input", self.snapshot(), "agent"
        )
        proposal_id = proposal["proposal"]["id"]
        with self.assertRaisesRegex(ValueError, "approved"):
            self.repo.publish_proposal(proposal_id, "publisher")
        self.repo.submit_proposal(proposal_id)
        with self.assertRaisesRegex(ValueError, "draft"):
            self.repo.add_proposal_item(proposal_id, "ADD", "RULE")
        rejected = self.repo.review_proposal(proposal_id, "REJECT", "reviewer", "证据不足")
        self.assertEqual("REJECTED", rejected["proposal"]["status"])
        self.assertEqual([], self.repo.list_functions())

    def test_schema_creation_is_idempotent(self):
        self.db.close()
        first = connect(str(self.path))
        first.close()
        second = connect(str(self.path))
        tables = {row[0] for row in second.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        second.close()
        self.db = connect(str(self.path))
        self.repo = KnowledgeGovernanceRepository(self.db)
        self.assertTrue({
            "business_function", "business_function_version", "function_scenario",
            "function_rule", "function_entry", "function_data_impact",
            "knowledge_update_proposal", "knowledge_update_proposal_item",
            "knowledge_proposal_review",
        }.issubset(tables))

    def test_accept_reject_and_defer_review_semantics(self):
        deferred = self.repo.create_proposal(
            "暂缓知识", "DOCUMENT", "DOC-1", self.snapshot(), "agent"
        )
        deferred_id = deferred["proposal"]["id"]
        self.repo.submit_proposal(deferred_id)
        deferred = self.repo.review_proposal(deferred_id, "DEFER", "reviewer", "等待业务确认")
        self.assertEqual("DEFERRED", deferred["proposal"]["status"])
        with self.assertRaisesRegex(ValueError, "approved"):
            self.repo.publish_proposal(deferred_id, "publisher")
        self.assertEqual("PENDING_REVIEW", self.repo.submit_proposal(deferred_id)["proposal"]["status"])
        accepted = self.repo.review_proposal(deferred_id, "ACCEPT", "reviewer")
        self.assertEqual("APPROVED", accepted["proposal"]["status"])
        self.assertEqual(
            "PUBLISHED", self.repo.publish_proposal(deferred_id, "publisher")["function"]["status"]
        )


if __name__ == "__main__":
    unittest.main()
