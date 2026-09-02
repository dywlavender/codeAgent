from __future__ import annotations

import unittest

from business_code_agent.query_agent.answer_policy import (
    EVIDENCE_CONFLICT,
    INSUFFICIENT_EVIDENCE,
    NO_MODEL,
    NO_VERIFIED_FACTS,
    AnswerPolicy,
)
from business_code_agent.query_agent.answer import AnswerRenderer
from business_code_agent.query_agent.models import AnswerType, EvidenceConflict, EvidenceStatus


class AnswerPolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = AnswerPolicy()

    def test_sufficient_facts_with_model_is_full_and_uses_model(self):
        decision = self.policy.decide(
            evidence_status=EvidenceStatus.SUFFICIENT,
            facts=[{"statement": "事实 1"}, {"statement": "事实 2"}],
            conflicts=[], unknowns=[], model_available=True,
        )
        self.assertEqual(AnswerType.FULL, decision.answer_type)
        self.assertTrue(decision.use_model)
        self.assertEqual("", decision.reason)

    def test_sufficient_facts_without_model_is_deterministic_full(self):
        decision = self.policy.decide(
            evidence_status=EvidenceStatus.SUFFICIENT,
            facts=[{"statement": "事实"}],
            conflicts=[], unknowns=[], model_available=False,
        )
        self.assertEqual(AnswerType.FULL, decision.answer_type)
        self.assertFalse(decision.use_model)
        self.assertEqual(NO_MODEL, decision.reason)

    def test_insufficient_with_facts_is_partial_without_model(self):
        decision = self.policy.decide(
            evidence_status=EvidenceStatus.INSUFFICIENT,
            facts=[{"statement": "已确认事实"}],
            conflicts=[], unknowns=["缺少下游证据"], model_available=True,
        )
        self.assertEqual(AnswerType.PARTIAL, decision.answer_type)
        self.assertFalse(decision.use_model)
        self.assertEqual(INSUFFICIENT_EVIDENCE, decision.reason)

    def test_insufficient_without_facts_is_unknown(self):
        decision = self.policy.decide(
            evidence_status=EvidenceStatus.INSUFFICIENT,
            facts=[], conflicts=[], unknowns=["没有直接证据"], model_available=True,
        )
        self.assertEqual(AnswerType.UNKNOWN, decision.answer_type)
        self.assertFalse(decision.use_model)
        self.assertEqual(NO_VERIFIED_FACTS, decision.reason)

    def test_conflict_always_wins_and_never_uses_model(self):
        decision = self.policy.decide(
            evidence_status=EvidenceStatus.CONFLICT,
            facts=[{"statement": "事实"}],
            conflicts=[{"reason": "来源不一致"}], unknowns=[], model_available=True,
        )
        self.assertEqual(AnswerType.CONFLICT, decision.answer_type)
        self.assertFalse(decision.use_model)
        self.assertEqual(EVIDENCE_CONFLICT, decision.reason)

    def test_conflict_metadata_wins_over_inconsistent_sufficient_status(self):
        decision = self.policy.decide(
            evidence_status=EvidenceStatus.SUFFICIENT,
            facts=[{"statement": "事实"}],
            conflicts=[{"reason": "来源不一致"}], unknowns=[], model_available=True,
        )
        self.assertEqual(AnswerType.CONFLICT, decision.answer_type)
        self.assertFalse(decision.use_model)

    def test_sufficient_without_verified_facts_is_unknown(self):
        decision = self.policy.decide(
            evidence_status=EvidenceStatus.SUFFICIENT,
            facts=[], conflicts=[], unknowns=[], model_available=True,
        )
        self.assertEqual(AnswerType.UNKNOWN, decision.answer_type)
        self.assertFalse(decision.use_model)
        self.assertEqual(NO_VERIFIED_FACTS, decision.reason)

    def test_renderer_labels_partial_and_unknown_without_inventing_facts(self):
        partial = AnswerRenderer().render({
            "answerType": "PARTIAL",
            "conclusion": "已确认渠道服务接收请求。",
            "facts": [{"statement": "渠道服务接收请求", "sourceType": "CODE", "evidenceIds": ["EV-1"]}],
            "unknowns": ["尚未确认核心放款系统"],
        })
        self.assertIn("部分结论", partial)
        self.assertIn("已确认", partial)
        self.assertIn("未确认", partial)

        unknown = AnswerRenderer().render({
            "answerType": "UNKNOWN",
            "conclusion": "当前证据不足，无法形成确定结论。",
            "facts": [],
            "unknowns": ["缺少直接代码证据"],
        })
        self.assertIn("当前证据不足", unknown)
        self.assertIn("缺少直接代码证据", unknown)

    def test_answer_builder_keeps_dataclass_conflict_evidence_ids(self):
        from business_code_agent.query_agent.answer import AnswerBuilder

        answer = AnswerBuilder().build({
            "question": "纳税授权是否必须？",
            "intent": "RULE_REASON",
            "known_facts": [],
            "conflicts": [EvidenceConflict(
                "tax_authorization", ["EV-CODE"], ["EV-BUSINESS"], [], "业务规则与当前代码行为不一致",
            )],
        })
        self.assertEqual("CONFLICT", answer["conflicts"][0]["status"])
        self.assertEqual({"EV-CODE", "EV-BUSINESS"}, set(answer["conflicts"][0]["evidenceIds"]))


if __name__ == "__main__":
    unittest.main()
