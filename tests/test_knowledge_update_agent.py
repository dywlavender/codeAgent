from __future__ import annotations

import unittest
import tempfile
from unittest.mock import patch

from business_code_agent.knowledge_update import (
    KnowledgeUpdateAgent,
    LangChainUpdateAnalyzer,
    ModelConfig,
    UpdateSource,
    UpdateSourceType,
)
from business_code_agent.knowledge_update.repository import KnowledgeGovernanceRepository
from business_code_agent.schema import connect


class MemoryGovernanceStore:
    def __init__(self):
        self.functions = [{
            "id": "BF-ORDER-CANCEL", "name": "取消订单", "domain": "订单",
            "summary": "取消未完成订单", "scenarios": [], "rules": [], "entries": [], "data_impacts": [],
        }]
        self.proposals = {}
        self.items = []

    def list_functions(self, status="PUBLISHED", limit=50):
        return self.functions[:limit]

    def get_function(self, function_id, version=None):
        value = next(item for item in self.functions if item["id"] == function_id)
        return {"function": value, "version": {"id": f"{function_id}-V1"}, "snapshot": value}

    def create_proposal(self, title, trigger_type, trigger_id, proposed_snapshot, created_by, target_function_id=None, action="CREATE", summary="", base_version_id=None):
        value = {"id": f"KUP-{len(self.proposals) + 1}", "status": "DRAFT", "title": title,
                 "triggerType": trigger_type, "triggerId": trigger_id, "targetFunctionId": target_function_id,
                 "action": action, "snapshot": proposed_snapshot, "summary": summary, "createdBy": created_by}
        self.proposals[value["id"]] = value
        return value

    def add_proposal_item(self, proposal_id, item_type, target_type, before=None, after=None, target_id=None, rationale="", confidence=0.0, evidence_ids=()):
        value = {"proposalId": proposal_id, "itemType": item_type, "targetType": target_type,
                 "before": before, "after": after, "targetId": target_id, "rationale": rationale,
                 "confidence": confidence, "evidenceIds": list(evidence_ids)}
        self.items.append(value)
        return value

    def submit_proposal(self, proposal_id):
        self.proposals[proposal_id]["status"] = "PENDING_REVIEW"
        return self.proposals[proposal_id]

    def review_proposal(self, proposal_id, decision, reviewer, comment=""):
        statuses = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "REQUEST_CHANGES": "CHANGES_REQUESTED"}
        self.proposals[proposal_id]["status"] = statuses[decision]
        self.proposals[proposal_id]["reviewer"] = reviewer
        return self.proposals[proposal_id]

    def publish_proposal(self, proposal_id, published_by):
        if self.proposals[proposal_id]["status"] != "APPROVED":
            raise ValueError("only approved proposals can be published")
        self.proposals[proposal_id]["status"] = "PUBLISHED"
        return self.proposals[proposal_id]

    def get_proposal(self, proposal_id):
        return self.proposals[proposal_id]


class FakeAnalyzer:
    def analyze(self, source, candidates):
        target = candidates[0]
        return {
            "title": "更新取消订单规则", "action": "UPDATE", "target_function_id": target.function_id,
            "summary": "需求增加已支付订单处理规则",
            "proposed_snapshot": {
                "name": target.name, "domain": "订单", "summary": "取消未完成订单",
                "scenarios": [], "rules": [{"statement": "已支付订单转退款流程", "evidence_ids": ["EV-REQ-1"]}],
                "entries": [], "data_impacts": [],
            },
            "items": [{
                "item_type": "ADD_RULE", "target_type": "RULE", "after": "已支付订单转退款流程",
                "rationale": "需求明确新增处理规则", "confidence": 0.9, "evidence_ids": ["EV-REQ-1"],
            }],
            "conflicts": [], "unknowns": ["代码是否已经实现"],
        }


class KnowledgeUpdateAgentTest(unittest.TestCase):
    def test_requirement_creates_review_proposal_and_human_publishes(self):
        store = MemoryGovernanceStore()
        agent = KnowledgeUpdateAgent(store, analyzer=FakeAnalyzer())
        result = agent.from_requirement(
            "REQ-1", "订单取消需求：已支付订单转入退款流程", evidence_ids=["EV-REQ-1"],
        )
        proposal_id = result["proposal"]["id"]
        self.assertEqual("PENDING_REVIEW", result["proposal"]["status"])
        self.assertEqual("BF-ORDER-CANCEL", result["proposal"]["targetFunctionId"])
        self.assertEqual(["EV-REQ-1"], store.items[0]["evidenceIds"])
        with self.assertRaises(ValueError):
            agent.publish(proposal_id, published_by="admin")
        agent.review(proposal_id, "APPROVE", reviewer="admin")
        self.assertEqual("PUBLISHED", agent.publish(proposal_id, published_by="admin")["status"])

    def test_model_cannot_cite_unknown_evidence_or_control_status(self):
        store = MemoryGovernanceStore()

        class BadAnalyzer(FakeAnalyzer):
            def analyze(self, source, candidates):
                result = super().analyze(source, candidates)
                result["status"] = "APPROVED"
                result["items"][0]["evidence_ids"] = ["EV-NOT-SUPPLIED"]
                return result

        with self.assertRaisesRegex(ValueError, "cannot control review"):
            KnowledgeUpdateAgent(store, analyzer=BadAnalyzer()).from_requirement(
                "REQ-1", "订单取消需求", evidence_ids=["EV-REQ-1"],
            )

    def test_without_model_uses_conservative_review_item(self):
        store = MemoryGovernanceStore()
        result = KnowledgeUpdateAgent(store).from_feedback("FB-1", "取消订单的退款描述可能有误")
        self.assertEqual("PENDING_REVIEW", result["proposal"]["status"])
        self.assertEqual(0.0, store.items[0]["confidence"])
        self.assertTrue(result["unknowns"])

    def test_code_fact_refresh_is_separate_from_semantic_proposal(self):
        class Maintainer:
            def refresh(self, repository_id, root_path):
                return {"repositoryId": repository_id, "changed": 3, "businessKnowledgeChanged": False}

        store = MemoryGovernanceStore()
        agent = KnowledgeUpdateAgent(store, code_facts=Maintainer())
        result = agent.refresh_code_facts("repo", "/project")
        self.assertEqual(3, result["changed"])
        self.assertFalse(store.proposals)


class LangChainUpdateAdapterTest(unittest.TestCase):
    def test_model_config_rejects_inline_credential(self):
        with self.assertRaisesRegex(ValueError, "apiKeyEnv"):
            ModelConfig.from_mapping({"provider": "test", "name": "model", "apiKey": "secret"})

    def test_uses_create_agent_contract_and_validates_domain_result(self):
        captured = {}

        class Structured:
            def model_dump(self, mode="python"):
                return {
                    "title": "新增功能", "action": "CREATE", "summary": "创建支付对账知识",
                    "proposed_snapshot": {"name": "支付对账", "summary": "核对流水", "domain": "支付",
                                          "scenarios": [], "rules": [], "entries": [], "data_impacts": []},
                    "items": [{"item_type": "CREATE_FUNCTION", "target_type": "FUNCTION", "after": "支付对账",
                               "rationale": "管理员明确说明", "confidence": 0.8, "evidence_ids": ["EV-1"]}],
                    "conflicts": [], "unknowns": [],
                }

        class Runnable:
            def invoke(self, value):
                captured["invoke"] = value
                return {"structured_response": Structured()}

        def factory(**kwargs):
            captured.update(kwargs)
            return Runnable()

        adapter = LangChainUpdateAnalyzer(object(), agent_factory=factory)
        with patch("business_code_agent.knowledge_update.langchain_adapter._update_analysis_schema", return_value=Structured):
            result = adapter.analyze(UpdateSource(UpdateSourceType.MANUAL, "M-1", "支付对账", ("EV-1",)), [])
        self.assertEqual("支付对账", result.proposed_snapshot["name"])
        self.assertEqual([], captured["tools"])
        self.assertIs(captured["response_format"], Structured)
        self.assertIn("messages", captured["invoke"])

    def test_model_transport_failure_falls_back_to_review_proposal(self):
        class FailingRunnable:
            def invoke(self, value):
                raise TimeoutError("provider timeout")

        analyzer = LangChainUpdateAnalyzer(object(), agent_factory=lambda **kwargs: FailingRunnable())
        with patch("business_code_agent.knowledge_update.langchain_adapter._update_analysis_schema", return_value=object):
            result = KnowledgeUpdateAgent(MemoryGovernanceStore(), analyzer=analyzer).from_feedback(
                "FB-FAIL", "取消订单说明需要复核"
            )
        self.assertEqual("FALLBACK", result["analysisMode"])
        self.assertEqual("PENDING_REVIEW", result["proposal"]["status"])

    def test_agent_construction_and_invalid_structure_also_fall_back(self):
        def broken_factory(**kwargs):
            raise RuntimeError("provider construction failed")

        analyzer = LangChainUpdateAnalyzer(object(), agent_factory=broken_factory)
        with patch("business_code_agent.knowledge_update.langchain_adapter._update_analysis_schema", return_value=object):
            result = KnowledgeUpdateAgent(MemoryGovernanceStore(), analyzer=analyzer).from_feedback(
                "FB-BUILD", "取消订单说明需要复核"
            )
        self.assertEqual("FALLBACK", result["analysisMode"])

        class MissingStructured:
            def invoke(self, value):
                return {"messages": []}

        analyzer = LangChainUpdateAnalyzer(object(), agent_factory=lambda **kwargs: MissingStructured())
        with patch("business_code_agent.knowledge_update.langchain_adapter._update_analysis_schema", return_value=object):
            result = KnowledgeUpdateAgent(MemoryGovernanceStore(), analyzer=analyzer).from_feedback(
                "FB-FORMAT", "取消订单说明需要复核"
            )
        self.assertEqual("FALLBACK", result["analysisMode"])

    def test_model_evidence_must_match_the_proposed_change(self):
        class MisboundAnalyzer:
            def analyze(self, source, candidates):
                return {
                    "title": "新增功能", "action": "CREATE", "summary": "新增支付功能",
                    "proposed_snapshot": {"name": "支付", "summary": "处理支付", "evidence_ids": ["EV-1"]},
                    "items": [{
                        "item_type": "CREATE_FUNCTION", "target_type": "FUNCTION",
                        "after": "处理支付", "rationale": "新增支付功能", "confidence": 0.9,
                        "evidence_ids": ["EV-1"],
                    }],
                }

        source = UpdateSource(
            UpdateSourceType.MANUAL, "M-MISMATCH", "订单取消后释放库存", ("EV-1",),
            {"evidence_claims": {"EV-1": "订单取消后释放库存"}},
        )
        result = KnowledgeUpdateAgent(MemoryGovernanceStore(), analyzer=MisboundAnalyzer()).propose(source)
        self.assertEqual("FALLBACK", result["analysisMode"])

    def test_model_cannot_omit_item_evidence_or_misbind_snapshot_rule(self):
        class ZeroEvidenceAnalyzer:
            def analyze(self, source, candidates):
                return {
                    "title": "更新取消订单", "action": "UPDATE",
                    "target_function_id": candidates[0].function_id,
                    "base_version_id": "BF-ORDER-CANCEL-V1", "summary": "更新规则",
                    "proposed_snapshot": candidates[0].current,
                    "items": [{"item_type": "UPDATE", "target_type": "FUNCTION",
                               "after": "取消订单后释放库存", "rationale": "更新规则",
                               "confidence": 0.9, "evidence_ids": []}],
                }

        source = UpdateSource(
            UpdateSourceType.MANUAL, "M-ZERO", "取消订单后释放库存", ("EV-1",),
            {"evidence_claims": {"EV-1": "取消订单后释放库存"}},
        )
        result = KnowledgeUpdateAgent(MemoryGovernanceStore(), analyzer=ZeroEvidenceAnalyzer()).propose(source)
        self.assertEqual("FALLBACK", result["analysisMode"])

        class WrongRuleAnalyzer(ZeroEvidenceAnalyzer):
            def analyze(self, source, candidates):
                result = super().analyze(source, candidates)
                result["items"][0]["evidence_ids"] = ["EV-1"]
                result["proposed_snapshot"] = {
                    **candidates[0].current,
                    "rules": [{"statement": "支付到账后自动核销", "evidence_ids": ["EV-1"]}],
                }
                return result

        result = KnowledgeUpdateAgent(MemoryGovernanceStore(), analyzer=WrongRuleAnalyzer()).propose(source)
        self.assertEqual("FALLBACK", result["analysisMode"])

    def test_model_cannot_remove_snapshot_item_without_explicit_grounded_change(self):
        store = MemoryGovernanceStore()
        store.functions[0]["rules"] = [{
            "id": "RULE-OLD", "statement": "取消订单后释放库存",
            "conditions": [], "result": "释放库存", "status": "ACTIVE",
            "evidence_ids": ["EV-OLD"],
        }]

        class SilentRemovalAnalyzer:
            def analyze(self, source, candidates):
                return {
                    "title": "更新取消订单", "action": "UPDATE",
                    "target_function_id": candidates[0].function_id,
                    "base_version_id": "BF-ORDER-CANCEL-V1", "summary": "更新规则",
                    "proposed_snapshot": {**candidates[0].current, "rules": []},
                    "items": [{"item_type": "UPDATE", "target_type": "FUNCTION",
                               "after": "取消订单规则调整", "rationale": "取消订单规则调整",
                               "confidence": 0.9, "evidence_ids": ["EV-1"]}],
                }

        source = UpdateSource(
            UpdateSourceType.MANUAL, "M-REMOVE", "取消订单规则调整", ("EV-1",),
            {"evidence_claims": {"EV-1": "取消订单规则调整"}},
        )
        result = KnowledgeUpdateAgent(store, analyzer=SilentRemovalAnalyzer()).propose(source)
        self.assertEqual("FALLBACK", result["analysisMode"])


class KnowledgeUpdateRepositoryIntegrationTest(unittest.TestCase):
    def test_agent_updates_published_function_through_real_state_machine(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            db = connect(handle.name)
            store = KnowledgeGovernanceRepository(db)
            db.execute(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("EV-REQ-1", "REQUIREMENT", "REQ-1", "1", "req:1", None, None, None, "digest", "已支付订单转退款流程"),
            )
            db.execute("INSERT INTO evidence_lifecycle VALUES (?, 'ACTIVE', NULL, NULL, NULL)", ("EV-REQ-1",))
            db.commit()
            created = store.create_proposal(
                "创建取消订单功能", "MANUAL", "M-0",
                {"name": "取消订单", "domain": "订单", "summary": "取消未完成订单",
                 "scenarios": [], "rules": [], "entries": [], "data_impacts": [], "evidence_ids": []},
                "admin",
            )
            proposal_id = created["proposal"]["id"]
            store.add_proposal_item(
                proposal_id, "CREATE_FUNCTION", "FUNCTION", after="取消订单",
                rationale="初始化", confidence=1.0,
            )
            store.submit_proposal(proposal_id)
            store.review_proposal(proposal_id, "APPROVE", "admin")
            published = store.publish_proposal(proposal_id, "admin")
            function_id = published["function"]["id"]

            result = KnowledgeUpdateAgent(store, analyzer=FakeAnalyzer()).from_requirement(
                "REQ-1", "订单取消需求：已支付订单转入退款流程", evidence_ids=["EV-REQ-1"],
            )
            update_id = result["proposal"]["id"]
            self.assertEqual(function_id, result["proposal"]["target_function_id"])
            self.assertEqual("PENDING_REVIEW", result["proposal"]["status"])
            KnowledgeUpdateAgent(store).review(update_id, "APPROVE", reviewer="admin")
            current = KnowledgeUpdateAgent(store).publish(update_id, published_by="admin")
            self.assertEqual(2, current["version"]["version"])
            self.assertEqual("已支付订单转退款流程", current["snapshot"]["rules"][0]["statement"])
            db.close()


if __name__ == "__main__":
    unittest.main()
