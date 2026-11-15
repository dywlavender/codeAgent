from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from business_code_agent.query.agent.question_contract import (
    CONTRACT_EXTRACTION_PROMPT,
    MAX_OBJECTIVES,
    QuestionContract,
    QuestionObjective,
    aextract_question_contract,
    assess_question_coverage,
    extract_question_contract,
    parse_contract_response,
    question_segments,
)

QUESTION = "订单取消后库存如何回补，由哪个服务处理，失败时怎么重试？"
FULL_EXTRACTION = [
    {"requirement": "订单取消后库存如何回补",
     "quote": "订单取消后库存如何回补", "kind": "process",
     "segmentIds": ["S1"]},
    {"requirement": "由哪个服务处理", "quote": "由哪个服务处理",
     "kind": "relationship", "segmentIds": ["S2"]},
    {"requirement": "失败时怎么重试", "quote": "失败时怎么重试",
     "kind": "behavior", "segmentIds": ["S3"]},
]


def _user_objective(objective_id="Q0", requirement=QUESTION, required=True,
                    kind="other", notes=(), question_quote=None, parent_id=None,
                    blocking=False, necessity=None):
    return QuestionObjective(
        id=objective_id, requirement=requirement, origin="user",
        required=required, kind=kind, notes=tuple(notes),
        question_quote=question_quote, parent_id=parent_id,
        blocking=blocking, necessity=necessity)


def _discovered_objective(objective_id="Q1", requirement="取消消息由订单服务发出",
                          source="order/CancelService.java:42", source_kind="source_code",
                          required=False, kind="relationship", parent_id=None,
                          blocking=False, necessity=None):
    return QuestionObjective(
        id=objective_id, requirement=requirement, origin="discovered",
        required=required, kind=kind, source=source, source_kind=source_kind,
        parent_id=parent_id, blocking=blocking, necessity=necessity)


class QuestionContractCreateTests(unittest.TestCase):
    def test_original_question_is_first_required_objective(self):
        contract = QuestionContract.create(QUESTION, FULL_EXTRACTION)

        self.assertEqual(contract.original_question, QUESTION)
        self.assertEqual(len(contract), 4)
        root = contract.objective("Q0")
        self.assertEqual(root.requirement, QUESTION)
        self.assertEqual(root.origin, "user")
        self.assertTrue(root.required)
        self.assertEqual(root.notes, ("完整回答原问题",))
        self.assertEqual(contract.objectives[0], root)
        self.assertEqual(contract.objective("Q1").origin, "user")
        self.assertTrue(contract.objective("Q1").required)
        self.assertEqual(contract.extraction_status, "ok")
        self.assertEqual(contract.coverage_status, "verified")
        self.assertEqual(contract.missing_segments, ())
        self.assertTrue(contract.structural_coverage_verified)

    def test_long_original_question_is_preserved(self):
        question = "订单" * 400
        contract = QuestionContract.create(question)

        self.assertEqual(contract.original_question, question)
        self.assertEqual(contract.objective("Q0").requirement, question)

    def test_long_extracted_objective_is_not_truncated(self):
        requirement = "核对完整字段透传关系" * 80
        question = f"请说明：{requirement}。"
        contract = QuestionContract.create(question, [{
            "requirement": requirement, "quote": requirement}])

        self.assertEqual(contract.objective("Q1").requirement, requirement)

    def test_extracted_objectives_are_deduped_and_trimmed(self):
        contract = QuestionContract.create(QUESTION, [
            {"requirement": "  由哪个服务处理  ", "quote": "由哪个服务处理",
             "kind": "process"},
            {"requirement": "由哪个服务处理", "quote": "由哪个服务处理",
             "kind": "behavior"},
            {"requirement": "失败时怎么重试", "quote": "失败时怎么重试"},
            {"requirement": "  ", "quote": "失败时怎么重试"},
            {"requirement": "订单取消后库存如何回补",
             "quote": "订单取消后库存如何回补", "kind": "not-a-kind"},
        ])

        requirements = [obj.requirement for obj in contract.objectives]
        self.assertEqual(requirements, [QUESTION, "由哪个服务处理",
                                        "失败时怎么重试", "订单取消后库存如何回补"])
        self.assertEqual(contract.objective("Q2").kind, "other")
        self.assertEqual(contract.objective("Q3").kind, "other")

    def test_extraction_yielding_only_the_original_question_is_fallback(self):
        contract = QuestionContract.create(QUESTION, [{"requirement": QUESTION}])

        self.assertEqual(len(contract), 1)
        self.assertEqual(contract.extraction_status, "fallback")
        self.assertEqual(contract.coverage_status, "partial")
        self.assertIn("由哪个服务处理", contract.missing_segments)

    def test_extraction_limit_is_marked_not_silent(self):
        question = "请说明目标一。请说明目标二。请说明目标三。请说明目标四。"
        question += "请说明目标五。请说明目标六。请说明目标七。请说明目标八。"
        extracted = [{"requirement": f"目标{index}", "quote": f"请说明目标{index}"}
                     for index in "一二三四五六七八"]
        contract = QuestionContract.create(question, extracted)

        self.assertEqual(len(contract), MAX_OBJECTIVES - 5)
        self.assertTrue(contract.truncated)
        self.assertEqual(contract.truncated_count, 2)
        self.assertEqual(contract.extraction_status, "partial")
        self.assertNotEqual(contract.coverage_status, "verified")
        self.assertFalse(contract.structural_coverage_verified)

    def test_empty_question_is_rejected(self):
        with self.assertRaises(ValueError):
            QuestionContract.create("   ")
        with self.assertRaises(ValueError):
            QuestionContract.fallback("")

    def test_fallback_keeps_original_and_marks_status(self):
        contract = QuestionContract.fallback(QUESTION, "parse")

        self.assertEqual(contract.objective("Q0").requirement, QUESTION)
        self.assertEqual(contract.extraction_status, "fallback")
        self.assertIn("目标提取回退：parse", contract.objective("Q0").notes)
        self.assertEqual(contract.coverage_status, "partial")
        self.assertEqual(len(contract.missing_segments), 3)
        self.assertFalse(contract.structural_coverage_verified)
        self.assertEqual(QuestionContract.fallback(QUESTION, "error").extraction_status,
                         "error")

    def test_unknown_extraction_status_is_rejected(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective()], extraction_status="guessed")

    def test_revision_must_be_positive(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective()], revision=0)

    def test_ok_status_requires_verified_coverage(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective()], extraction_status="ok")

    def test_ok_status_requires_question_quotes(self):
        with self.assertRaises(ValueError):
            QuestionContract(
                QUESTION,
                [_user_objective(), _user_objective(
                    "Q1", "由哪个服务处理", question_quote=None)],
                extraction_status="ok", coverage_status="verified")


class QuestionContractAppendTests(unittest.TestCase):
    def test_user_followup_is_required_not_optional_discovered(self):
        contract = QuestionContract.create(QUESTION, FULL_EXTRACTION)
        followup = contract.add_user_followup("失败重试时会不会重复扣款？")
        contract.validate()

        self.assertEqual(followup.origin, "user")
        self.assertTrue(followup.required)
        self.assertFalse(followup.blocking)
        self.assertEqual(followup.source_kind, "user_followup")
        self.assertIn(followup, contract.required_objectives)
        self.assertNotIn(followup, (obj for obj in contract.objectives
                                    if obj.origin == "discovered"))
        self.assertIn("失败重试时会不会重复扣款", contract.prompt_view())
        self.assertEqual(contract.revision, 2)

    def test_discovered_objective_is_appended_as_optional(self):
        contract = QuestionContract.create(QUESTION)
        discovered = contract.add_discovered(
            "取消消息由订单服务发出", source="order/CancelService.java:42")

        self.assertEqual(discovered.id, "Q1")
        self.assertEqual(discovered.origin, "discovered")
        self.assertFalse(discovered.required)
        self.assertFalse(discovered.blocking)
        self.assertEqual(discovered.source, "order/CancelService.java:42")
        self.assertEqual(discovered.source_kind, "source_code")
        self.assertEqual(contract.revision, 2)
        self.assertEqual([obj.id for obj in contract.objectives], ["Q0", "Q1"])

    def test_blocking_relation_is_not_a_user_goal_but_blocks_its_parent(self):
        contract = QuestionContract.create(QUESTION, FULL_EXTRACTION)
        discovered = contract.add_discovered(
            "审批过程必须经过命令包装与命令路由",
            source="fineract-commands/service/CommandWrapperBuilder.java:1-20",
            parent_id="Q1", blocking=True,
            necessity="没有命令路由就无法确认审批动作如何触发状态变化")

        self.assertFalse(discovered.required)
        self.assertTrue(discovered.blocking)
        self.assertEqual(discovered.parent_id, "Q1")
        self.assertIn(discovered, contract.blocking_objectives)
        self.assertNotIn(discovered, contract.required_objectives)
        self.assertIn("阻断性必要关系", contract.prompt_view())
        self.assertIn("父目标：Q1", contract.prompt_view())

    def test_blocking_relation_requires_parent_and_necessity(self):
        contract = QuestionContract.create(QUESTION)
        with self.assertRaises(ValueError):
            contract.add_discovered("无父目标阻断项", source="order/A.java:1",
                                    blocking=True, necessity="必要性")
        with self.assertRaises(ValueError):
            contract.add_discovered("无必要性阻断项", source="order/A.java:1",
                                    parent_id="Q0", blocking=True)
        with self.assertRaises(ValueError):
            contract.add_discovered("父目标不存在", source="order/A.java:1",
                                    parent_id="Q9", blocking=True, necessity="必要性")
        self.assertEqual(len(contract), 1)

    def test_discovered_objective_cannot_be_marked_required(self):
        contract = QuestionContract.create(QUESTION)

        with self.assertRaises(TypeError):
            contract.add_discovered("取消后必须发布库存回补事件",
                                    source="knowledge/order.md:取消约定",
                                    source_kind="business_context", required=True)
        self.assertEqual(len(contract), 1)

    def test_discovered_objective_requires_source_and_valid_kind(self):
        contract = QuestionContract.create(QUESTION)

        with self.assertRaises(ValueError):
            contract.add_discovered("无依据目标", source="")
        with self.assertRaises(ValueError):
            contract.add_discovered("错误依据类型", source="order/A.java:1",
                                    source_kind="guess")
        with self.assertRaises(ValueError):
            contract.add_discovered("  ", source="order/A.java:1")
        self.assertEqual(len(contract), 1)
        self.assertEqual(contract.revision, 1)

    def test_unknown_discovered_kind_is_normalized_to_other(self):
        contract = QuestionContract.create(QUESTION)
        discovered = contract.add_discovered(
            "取消消息由订单服务发出", source="order/CancelService.java:42",
            kind="not-a-kind")

        self.assertEqual(discovered.kind, "other")

    def test_duplicate_discovery_is_idempotent(self):
        contract = QuestionContract.create(QUESTION)
        first = contract.add_discovered("取消消息由订单服务发出",
                                        source="order/CancelService.java:42")
        second = contract.add_discovered(" 取消消息由订单服务发出 ",
                                         source="order/Other.java:9")

        self.assertIs(first, second)
        self.assertEqual(len(contract), 2)
        self.assertEqual(contract.revision, 2)
        self.assertEqual(second.source, "order/CancelService.java:42")

    def test_discovered_objectives_respect_cap(self):
        contract = QuestionContract.create(QUESTION)
        for index in range(MAX_OBJECTIVES - 1):
            contract.add_discovered(f"派生目标 {index}", source=f"order/A.java:{index}")

        self.assertEqual(len(contract), MAX_OBJECTIVES)
        with self.assertRaises(ValueError):
            contract.add_discovered("超出上限目标", source="order/A.java:99")
        self.assertEqual(len(contract), MAX_OBJECTIVES)
        self.assertEqual(contract.revision, MAX_OBJECTIVES)

    def test_supplement_only_appends_notes(self):
        contract = QuestionContract.create(QUESTION, FULL_EXTRACTION[:1])
        before = contract.objective("Q1")
        revised = contract.supplement("Q1", "订单服务调用库存服务")

        self.assertEqual(revised.requirement, before.requirement)
        self.assertEqual(revised.origin, before.origin)
        self.assertEqual(revised.kind, before.kind)
        self.assertEqual(revised.question_quote, before.question_quote)
        self.assertEqual(revised.notes, (*before.notes, "订单服务调用库存服务"))
        self.assertEqual(contract.objective("Q0").requirement, QUESTION)
        self.assertEqual(contract.revision, 2)
        self.assertEqual(contract.supplement("Q1", "订单服务调用库存服务"), revised)
        self.assertEqual(contract.revision, 2)
        self.assertEqual(contract.supplement("Q1", "  "), revised)

    def test_supplement_can_annotate_the_original_question(self):
        contract = QuestionContract.create(QUESTION)
        revised = contract.supplement("Q0", "用户补充：重点看取消场景")

        self.assertEqual(revised.requirement, QUESTION)
        self.assertEqual(revised.notes, ("完整回答原问题", "用户补充：重点看取消场景"))
        self.assertEqual(contract.original_question, QUESTION)
        self.assertEqual(contract.objective("Q0").requirement, QUESTION)

    def test_supplement_preserves_discovered_source(self):
        contract = QuestionContract.create(QUESTION)
        contract.add_discovered("取消消息由订单服务发出", source="order/CancelService.java:42")
        revised = contract.supplement("Q1", "消息表 order_event")

        self.assertEqual(revised.source, "order/CancelService.java:42")
        self.assertEqual(revised.source_kind, "source_code")

    def test_supplement_unknown_objective_raises(self):
        contract = QuestionContract.create(QUESTION)
        with self.assertRaises(KeyError):
            contract.supplement("Q9", "备注")


class QuestionContractValidationTests(unittest.TestCase):
    def test_root_objective_cannot_be_removed(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective("Q1", "另一个目标")])

    def test_root_objective_cannot_be_modified(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective(requirement="被改写的问题")])

    def test_user_objectives_must_stay_required(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective(required=False)])

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective(),
                                        _user_objective("Q0", "另一个目标")])

    def test_duplicate_requirements_are_rejected(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective(),
                                        _user_objective("Q1", f" {QUESTION} ")])

    def test_blank_requirement_is_rejected(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective(),
                                        _user_objective("Q1", "   ")])

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective(kind="not-a-kind")])

    def test_discovered_objective_without_source_is_rejected(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective(),
                                        _discovered_objective(source=None)])
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective(),
                                        _discovered_objective(source_kind=None)])

    def test_required_discovered_objective_is_rejected(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective(),
                                        _discovered_objective(required=True)])

    def test_user_objective_cannot_be_blocking(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective(
                blocking=True, parent_id="Q0", necessity="必要性")])

    def test_manual_blocking_relation_requires_parent_and_necessity(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [
                _user_objective(),
                _discovered_objective(blocking=True, necessity="必要性")])
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [
                _user_objective(),
                _discovered_objective(blocking=True, parent_id="Q0")])

    def test_blocking_relation_parent_must_exist(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [
                _user_objective(),
                _discovered_objective(blocking=True, parent_id="Q9",
                                      necessity="必要性")])

    def test_parent_cycle_is_rejected(self):
        with self.assertRaises(ValueError):
            QuestionContract(
                QUESTION,
                [_user_objective(),
                 _discovered_objective("Q1", "派生一", parent_id="Q2"),
                 _discovered_objective("Q2", "派生二", parent_id="Q1")])

    def test_question_quote_must_come_from_original_question(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective(
                question_quote="原问题中不存在的句子")])

    def test_objective_count_is_bounded(self):
        objectives = [_user_objective()]
        objectives += [_discovered_objective(f"Q{index + 1}", f"目标 {index}")
                       for index in range(MAX_OBJECTIVES - 1)]

        contract = QuestionContract(QUESTION, objectives)
        self.assertEqual(len(contract), MAX_OBJECTIVES)
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [*objectives,
                                        _discovered_objective("QX", "多出的目标")])

    def test_returned_state_is_frozen(self):
        contract = QuestionContract.create(QUESTION)

        with self.assertRaises(AttributeError):
            contract.objectives = ()
        with self.assertRaises(FrozenInstanceError):
            contract.objective("Q0").requirement = "改写"
        with self.assertRaises(KeyError):
            contract.objective("Q9")


class QuestionContractSnapshotTests(unittest.TestCase):
    def test_to_dict_is_a_stable_snapshot(self):
        contract = QuestionContract.create(QUESTION, FULL_EXTRACTION)
        snapshot = contract.to_dict()
        contract.add_discovered("取消消息由订单服务发出", source="order/CancelService.java:42",
                                source_kind="source_code")

        self.assertEqual(snapshot["version"], 3)
        self.assertEqual(snapshot["originalQuestion"], QUESTION)
        self.assertEqual(snapshot["revision"], 1)
        self.assertEqual(snapshot["extractionStatus"], "ok")
        self.assertEqual(snapshot["coverageStatus"], "verified")
        self.assertEqual(snapshot["missingQuestionSegments"], [])
        self.assertFalse(snapshot["truncated"])
        self.assertEqual([item["id"] for item in snapshot["objectives"]],
                         ["Q0", "Q1", "Q2", "Q3"])
        self.assertEqual(contract.revision, 2)
        self.assertEqual(len(contract.to_dict()["objectives"]), 5)
        self.assertIn("questionQuote", snapshot["objectives"][1])
        self.assertIn("parentObjectiveId", snapshot["objectives"][1])
        self.assertIn("blocking", snapshot["objectives"][1])

    def test_prompt_view_separates_required_blocking_and_optional(self):
        contract = QuestionContract.create(QUESTION, FULL_EXTRACTION)
        contract.add_discovered(
            "审批过程必须经过命令路由", source="order/CommandRouter.java:1-9",
            parent_id="Q1", blocking=True, necessity="缺少路由无法确认审批触发")
        contract.add_discovered("取消消息由订单服务发出",
                                source="order/CancelService.java:42")
        view = contract.prompt_view()

        required_part, blocking_part = view.split("阻断性必要关系", 1)
        blocking_part, optional_part = blocking_part.split("可选补充目标", 1)
        self.assertIn("Q0 [other] " + QUESTION, required_part)
        self.assertIn("Q1 [process] 订单取消后库存如何回补", required_part)
        self.assertNotIn("Q4", required_part)
        self.assertIn("Q4 [relationship] 审批过程必须经过命令路由", blocking_part)
        self.assertIn("父目标：Q1", blocking_part)
        self.assertIn("Q5 [relationship] 取消消息由订单服务发出", optional_part)
        self.assertIn("不作为必答", optional_part)

    def test_prompt_view_without_optional_objectives(self):
        contract = QuestionContract.create(QUESTION)
        self.assertEqual(contract.prompt_view(),
                         "调查契约（用户明确要求的目标不可删除）：\n"
                         f"- Q0 [other] {QUESTION}")

    def test_snapshot_exposes_segment_mapping(self):
        contract = QuestionContract.create("渠道调用中台吗，中台调用渠道吗？", [
            {"requirement": "渠道调用中台吗", "segmentIds": ["S1"]},
        ])
        snapshot = contract.to_dict()

        self.assertEqual(snapshot["segments"], [
            {"id": "S1", "text": "渠道调用中台吗"},
            {"id": "S2", "text": "中台调用渠道吗"},
        ])
        self.assertEqual(snapshot["missingSegmentIds"], ["S2"])
        self.assertEqual(snapshot["missingQuestionSegments"], ["中台调用渠道吗"])
        self.assertEqual(snapshot["objectives"][1]["segmentIds"], ["S1"])


class QuestionSegmentationTests(unittest.TestCase):
    def test_comma_separated_asks_are_separate_segments(self):
        self.assertEqual(question_segments(QUESTION), (
            "订单取消后库存如何回补", "由哪个服务处理", "失败时怎么重试"))

    def test_leading_context_is_merged_into_the_first_ask(self):
        question = ("在当前项目中，一笔贷款申请从提交、审批到放款，账户状态如何流转？"
                    "状态定义和流转校验分别在代码哪里实现？")
        self.assertEqual(question_segments(question), (
            "在当前项目中，一笔贷款申请从提交、审批到放款，账户状态如何流转",
            "状态定义和流转校验分别在代码哪里实现"))

    def test_context_only_statement_is_not_a_requirement(self):
        question = ("供应商确认已经收到采购款，但客户查借款进度仍显示处理中。"
                    "请按当前实现说明应该查哪些系统。")
        self.assertEqual(question_segments(question),
                         ("请按当前实现说明应该查哪些系统",))

    def test_trailing_context_does_not_merge_into_previous_ask(self):
        self.assertEqual(question_segments("请说明A。这是背景说明。"), ("请说明A",))

    def test_question_without_asks_is_unverified(self):
        self.assertEqual(question_segments("这是一段背景说明。"), ())
        self.assertEqual(assess_question_coverage("这是一段背景说明。", []),
                         ("unverified", ()))


class QuestionCoverageTests(unittest.TestCase):
    def test_full_extraction_verifies_all_explicit_asks(self):
        contract = QuestionContract.create(QUESTION, FULL_EXTRACTION)
        self.assertEqual(contract.coverage_status, "verified")
        self.assertEqual(contract.missing_segments, ())

    def test_one_objective_cannot_cover_three_explicit_asks(self):
        contract = QuestionContract.create(QUESTION, FULL_EXTRACTION[:1])
        self.assertEqual(contract.extraction_status, "partial")
        self.assertEqual(contract.coverage_status, "partial")
        self.assertEqual(contract.missing_segments,
                         ("由哪个服务处理", "失败时怎么重试"))
        self.assertFalse(contract.structural_coverage_verified)

    def test_compound_ask_can_be_covered_by_two_objectives(self):
        question = "状态定义和流转校验分别在代码哪里实现？"
        contract = QuestionContract.create(question, [
            {"requirement": "状态定义在代码哪里实现",
             "quote": "状态定义和流转校验分别在代码哪里实现",
             "segmentIds": ["S1"]},
            {"requirement": "流转校验在代码哪里实现",
             "quote": "状态定义和流转校验分别在代码哪里实现",
             "segmentIds": ["S1"]},
        ])
        self.assertEqual(contract.coverage_status, "verified")

    def test_compound_ask_accepts_paraphrased_position_wording(self):
        question = "状态定义和流转校验分别在代码哪里实现？"
        contract = QuestionContract.create(question, [
            {"requirement": "指出账户状态定义在代码中的实现位置",
             "quote": "状态定义和流转校验分别在代码哪里实现",
             "segmentIds": ["S1"]},
            {"requirement": "指出状态流转校验在代码中的实现位置",
             "quote": "状态定义和流转校验分别在代码哪里实现",
             "segmentIds": ["S1"]},
        ])
        self.assertEqual(contract.coverage_status, "verified")

    def test_compound_ask_missing_one_side_is_partial(self):
        question = "状态定义和流转校验分别在代码哪里实现？"
        contract = QuestionContract.create(question, [
            {"requirement": "指出账户状态定义在代码中的实现位置",
             "quote": "状态定义和流转校验分别在代码哪里实现",
             "segmentIds": ["S1"]},
        ])
        self.assertEqual(contract.coverage_status, "partial")
        self.assertIn("状态定义和流转校验分别在代码哪里实现",
                      contract.missing_segments)

    def test_compound_list_requires_each_item(self):
        question = "贷款产品如何定义利率、期限与还款安排？"
        full = QuestionContract.create(question, [
            {"requirement": "贷款产品如何定义利率、期限与还款安排",
             "quote": question, "segmentIds": ["S1"]},
        ])
        partial = QuestionContract.create(question, [
            {"requirement": "贷款产品如何定义利率", "quote": question,
             "segmentIds": ["S1"]},
        ])
        self.assertEqual(full.coverage_status, "verified")
        self.assertEqual(partial.coverage_status, "partial")

    def test_similar_but_different_asks_need_separate_objectives(self):
        cases = (
            ("贷款利率如何设置，贷款期限如何设置？",
             "贷款利率如何设置", "贷款期限如何设置"),
            ("渠道系统如何处理，中台系统如何处理？",
             "渠道系统如何处理", "中台系统如何处理"),
            ("是否会重复扣款，是否会重复放款？",
             "是否会重复扣款", "是否会重复放款"),
            ("贷款审批失败如何回滚，贷款审批成功如何回滚？",
             "贷款审批失败如何回滚", "贷款审批成功如何回滚"),
        )
        for question, extracted, missing_segment in cases:
            with self.subTest(question=question):
                contract = QuestionContract.create(
                    question, [{"requirement": extracted}])

                self.assertEqual(contract.coverage_status, "partial")
                self.assertIn(missing_segment, contract.missing_segments)
                self.assertFalse(contract.structural_coverage_verified)

    def test_similar_asks_verify_only_when_both_are_extracted(self):
        question = "贷款利率如何设置，贷款期限如何设置？"
        contract = QuestionContract.create(question, [
            {"requirement": "贷款利率如何设置", "segmentIds": ["S1"]},
            {"requirement": "贷款期限如何设置", "segmentIds": ["S2"]},
        ])

        self.assertEqual(contract.coverage_status, "verified")
        self.assertTrue(contract.structural_coverage_verified)

    def test_unmapped_extra_goal_prevents_ok_status(self):
        contract = QuestionContract.create(QUESTION, [
            *FULL_EXTRACTION,
            {"requirement": "库存如何回补", "quote": "订单取消后库存如何回补"},
        ])

        self.assertEqual(contract.coverage_status, "verified")
        self.assertEqual(contract.extraction_status, "partial")
        self.assertFalse(contract.structural_coverage_verified)

    def test_distributive_ask_needs_each_parallel_object(self):
        question = "两次投递各自走到哪一步？"
        one = QuestionContract.create(question, [
            {"requirement": "第一次投递走到哪一步", "segmentIds": ["S1"]},
        ])
        both = QuestionContract.create(question, [
            {"requirement": "第一次投递走到哪一步", "segmentIds": ["S1"]},
            {"requirement": "第二次投递走到哪一步", "segmentIds": ["S1"]},
        ])

        self.assertEqual(one.coverage_status, "partial")
        self.assertEqual(both.coverage_status, "verified")

    def test_conditional_wording_does_not_break_coverage(self):
        question = "如果只能改一处代码，改哪里收益最大？"
        contract = QuestionContract.create(question, [
            {"requirement": "在只能改一处代码的约束下确定收益最大的改动位置",
             "segmentIds": ["S1"]},
        ])

        self.assertEqual(contract.coverage_status, "verified")

    def test_temporal_connective_does_not_break_coverage(self):
        question = "企业占用 25 万、预占 3 万，此时申请把总授信降到 28 万整，会置位吗？"
        contract = QuestionContract.create(question, [
            {"requirement": "企业占用 25 万、预占 3 万时，申请把总授信降到 28 万整，是否会置位",
             "segmentIds": ["S1"]},
        ])

        self.assertEqual(contract.coverage_status, "verified")

    def test_reversed_call_direction_is_not_covered_by_the_other_ask(self):
        question = "渠道调用中台吗，中台调用渠道吗？"
        forward = QuestionContract.create(question, [
            {"requirement": "渠道调用中台吗", "segmentIds": ["S1"]}])
        backward = QuestionContract.create(question, [
            {"requirement": "中台调用渠道吗", "segmentIds": ["S2"]}])
        both = QuestionContract.create(question, [
            {"requirement": "渠道调用中台吗", "segmentIds": ["S1"]},
            {"requirement": "中台调用渠道吗", "segmentIds": ["S2"]},
        ])

        self.assertEqual(forward.missing_segment_ids, ("S2",))
        self.assertEqual(backward.missing_segment_ids, ("S1",))
        self.assertEqual(both.coverage_status, "verified")
        self.assertEqual(both.missing_segment_ids, ())

    def test_reversed_sync_direction_is_not_covered(self):
        question = "中台向渠道同步吗，渠道向中台同步吗？"
        contract = QuestionContract.create(question, [
            {"requirement": "中台向渠道同步吗", "segmentIds": ["S1"]}])

        self.assertEqual(contract.missing_segment_ids, ("S2",))

    def test_explicit_mapping_cannot_cover_a_reversed_segment(self):
        question = "渠道调用中台吗，中台调用渠道吗？"
        contract = QuestionContract.create(question, [
            {"requirement": "渠道调用中台吗", "segmentIds": ["S1", "S2"]},
        ])

        self.assertEqual(contract.missing_segment_ids, ("S2",))

    def test_reversed_direction_inside_a_compound_ask_is_missing(self):
        question = "渠道调用中台和中台调用渠道吗？"
        one = QuestionContract.create(question, [
            {"requirement": "渠道调用中台", "segmentIds": ["S1"]},
        ])
        both = QuestionContract.create(question, [
            {"requirement": "渠道调用中台", "segmentIds": ["S1"]},
            {"requirement": "中台调用渠道吗", "segmentIds": ["S1"]},
        ])

        self.assertEqual(one.coverage_status, "partial")
        self.assertEqual(both.coverage_status, "verified")

    def test_hallucinated_objective_is_not_accepted_as_user_goal(self):
        text = json.dumps({"objectives": [
            {"requirement": "必须检查数据库连接池",
             "quote": "订单取消后库存如何回补"},
        ]})
        contract = parse_contract_response(text, QUESTION)

        self.assertEqual(len(contract), 1)
        self.assertEqual(contract.ungrounded_count, 1)
        self.assertEqual(contract.extraction_status, "partial")
        self.assertEqual(contract.missing_segments,
                         ("订单取消后库存如何回补", "由哪个服务处理", "失败时怎么重试"))

    def test_invalid_model_quote_falls_back_to_segment_grounding(self):
        text = json.dumps({"objectives": [
            {"requirement": "库存如何回补", "quote": "原问题中不存在这句话",
             "segmentIds": ["S1"]},
        ]})
        contract = parse_contract_response(text, QUESTION)

        self.assertEqual(contract.ungrounded_count, 0)
        self.assertEqual(len(contract), 2)
        self.assertTrue(contract.objective("Q1").question_quote_derived)
        self.assertEqual(contract.objective("Q1").question_quote,
                         "订单取消后库存如何回补")

    def test_unmapped_objective_with_invalid_quote_is_ungrounded(self):
        text = json.dumps({"objectives": [
            {"requirement": "库存如何回补", "quote": "原问题中不存在这句话"},
        ]})
        contract = parse_contract_response(text, QUESTION)

        self.assertEqual(contract.ungrounded_count, 1)
        self.assertEqual(len(contract), 1)
        self.assertEqual(contract.extraction_status, "partial")

    def test_model_goals_without_quotes_are_grounded_from_the_question(self):
        text = json.dumps({"objectives": [
            {"requirement": "订单取消后库存如何回补", "segmentIds": ["S1"]},
            {"requirement": "由哪个服务处理", "segmentIds": ["S2"]},
            {"requirement": "失败时怎么重试", "segmentIds": ["S3"]},
        ]})
        contract = parse_contract_response(text, QUESTION)

        self.assertEqual(contract.extraction_status, "ok")
        self.assertEqual(contract.coverage_status, "verified")
        self.assertEqual(contract.ungrounded_count, 0)
        self.assertTrue(contract.structural_coverage_verified)
        self.assertTrue(all(obj.question_quote_derived
                            for obj in contract.objectives[1:]))
        self.assertEqual(contract.objective("Q2").question_quote, "由哪个服务处理")

    def test_paraphrased_field_goal_derives_quote_from_matching_segment(self):
        question = "当前渠道接口能直接支持吗？依据哪些字段？"
        text = json.dumps({"objectives": [
            {"requirement": "判断当前渠道接口能否直接支持", "segmentIds": ["S1"]},
            {"requirement": "明确支持该区分所依据的字段", "segmentIds": ["S2"]},
        ]})
        contract = parse_contract_response(text, question)

        self.assertEqual(contract.extraction_status, "ok")
        self.assertEqual(contract.coverage_status, "verified")
        self.assertEqual(contract.objective("Q2").question_quote, "依据哪些字段")
        self.assertTrue(contract.objective("Q2").question_quote_derived)

    def test_invented_requirement_without_any_question_grounding_is_dropped(self):
        text = json.dumps({"objectives": [
            {"requirement": "必须检查数据库连接池"},
        ]})
        contract = parse_contract_response(text, QUESTION)

        self.assertEqual(len(contract), 1)
        self.assertEqual(contract.ungrounded_count, 1)
        self.assertEqual(contract.extraction_status, "partial")
        self.assertFalse(contract.structural_coverage_verified)


class ContractParseTests(unittest.TestCase):
    def test_code_fenced_json_is_parsed(self):
        text = json.dumps({"objectives": FULL_EXTRACTION})
        contract = parse_contract_response(f"```json\n{text}\n```", QUESTION)

        self.assertEqual(contract.extraction_status, "ok")
        self.assertEqual([obj.requirement for obj in contract.objectives],
                         [QUESTION, "订单取消后库存如何回补", "由哪个服务处理",
                          "失败时怎么重试"])

    def test_json_wrapped_in_prose_is_parsed(self):
        text = ("以下是结果：" + json.dumps({"objectives": [{
            "requirement": "失败时怎么重试", "quote": "失败时怎么重试",
            "kind": "behavior"}]}) + " 请查收。")
        contract = parse_contract_response(text, QUESTION)

        self.assertEqual(contract.objective("Q1").requirement, "失败时怎么重试")
        self.assertEqual(contract.objective("Q1").kind, "behavior")

    def test_invalid_response_falls_back_without_losing_question(self):
        for text in ("", "不是 JSON", "{broken", "[]", "{\"objectives\":\"全部\"}",
                     "{\"objectives\":{}}"):
            contract = parse_contract_response(text, QUESTION)
            self.assertEqual(contract.extraction_status, "fallback")
            self.assertEqual(len(contract), 1)
            self.assertEqual(contract.objective("Q0").requirement, QUESTION)
            self.assertFalse(contract.structural_coverage_verified)

    def test_empty_objectives_falls_back(self):
        contract = parse_contract_response("{\"objectives\":[]}", QUESTION)

        self.assertEqual(contract.extraction_status, "fallback")
        self.assertEqual(len(contract), 1)

    def test_invalid_items_are_skipped_but_grounded_items_survive(self):
        text = json.dumps({"objectives": [
            None, 42, {}, {"requirement": "  "},
            {"requirement": "由哪个服务处理", "quote": "由哪个服务处理",
             "segmentIds": ["S2"], "kind": "not-a-kind"},
        ]})
        contract = parse_contract_response(text, QUESTION)

        self.assertEqual(contract.extraction_status, "partial")
        self.assertEqual(len(contract), 2)
        self.assertEqual(contract.objective("Q1").kind, "other")
        self.assertEqual(contract.missing_segments,
                         ("订单取消后库存如何回补", "失败时怎么重试"))

    def test_truncation_is_marked_and_cannot_be_verified(self):
        text = json.dumps({"objectives": [
            {"requirement": f"目标{index}", "quote": f"请说明目标{index}"}
            for index in "一二三四五六七八"
        ]})
        question = "请说明目标一。请说明目标二。请说明目标三。请说明目标四。"
        question += "请说明目标五。请说明目标六。请说明目标七。请说明目标八。"
        contract = parse_contract_response(text, question)

        self.assertEqual(len(contract), MAX_OBJECTIVES - 5)
        self.assertTrue(contract.truncated)
        self.assertEqual(contract.truncated_count, 2)
        self.assertEqual(contract.extraction_status, "partial")
        self.assertNotEqual(contract.coverage_status, "verified")
        self.assertFalse(contract.structural_coverage_verified)

    def test_extraction_prompt_stays_short_and_json_only(self):
        self.assertLess(len(CONTRACT_EXTRACTION_PROMPT), 500)
        self.assertIn("只输出 JSON", CONTRACT_EXTRACTION_PROMPT)
        self.assertIn("segmentIds", CONTRACT_EXTRACTION_PROMPT)
        for tool_name in ("Read", "Glob", "Grep"):
            self.assertNotIn(tool_name, CONTRACT_EXTRACTION_PROMPT)

    def test_extraction_input_contains_numbered_segments(self):
        class CapturingModel:
            def __init__(self):
                self.messages = None

            def invoke(self, messages):
                self.messages = messages
                return AIMessage(content=json.dumps({"objectives": [
                    {"requirement": "由哪个服务处理", "segmentIds": ["S2"]},
                ]}))

        model = CapturingModel()
        contract = extract_question_contract(model, QUESTION)

        content = model.messages[-1].content
        self.assertIn("S1: 订单取消后库存如何回补", content)
        self.assertIn("S2: 由哪个服务处理", content)
        self.assertIn("S3: 失败时怎么重试", content)
        self.assertEqual(contract.objective("Q1").segment_ids, ("S2",))


class ContractExtractionTests(unittest.TestCase):
    def test_model_extraction_adds_user_objectives(self):
        model = FakeMessagesListChatModel(responses=[AIMessage(
            content=json.dumps({"objectives": FULL_EXTRACTION}))])
        contract = extract_question_contract(model, QUESTION)

        self.assertEqual(contract.extraction_status, "ok")
        self.assertEqual([obj.origin for obj in contract.objectives],
                         ["user", "user", "user", "user"])
        self.assertTrue(all(obj.required for obj in contract.objectives))
        self.assertEqual(contract.objective("Q3").requirement, "失败时怎么重试")
        self.assertEqual(contract.objective("Q3").question_quote, "失败时怎么重试")

    def test_model_invalid_output_returns_fallback(self):
        model = FakeMessagesListChatModel(responses=[AIMessage(content="not json")])
        contract = extract_question_contract(model, QUESTION)
        self.assertEqual(contract.extraction_status, "fallback")
        self.assertEqual(contract.objective("Q0").requirement, QUESTION)

    def test_model_exception_returns_error_fallback(self):
        class BrokenModel:
            def invoke(self, messages):
                raise RuntimeError("provider down")

        contract = extract_question_contract(BrokenModel(), QUESTION)

        self.assertEqual(contract.extraction_status, "error")
        self.assertEqual(len(contract), 1)
        self.assertEqual(contract.objective("Q0").requirement, QUESTION)


class ContractAsyncExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_model_extraction_adds_user_objectives(self):
        model = FakeMessagesListChatModel(responses=[AIMessage(
            content=json.dumps({"objectives": FULL_EXTRACTION}))])
        contract = await aextract_question_contract(model, QUESTION)

        self.assertEqual(contract.extraction_status, "ok")
        self.assertEqual(contract.original_question, QUESTION)
        self.assertEqual(contract.objective("Q1").requirement, "订单取消后库存如何回补")

    async def test_async_model_failure_returns_error_fallback(self):
        class BrokenModel:
            async def ainvoke(self, messages):
                raise RuntimeError("provider down")

        contract = await aextract_question_contract(BrokenModel(), QUESTION)

        self.assertEqual(contract.extraction_status, "error")
        self.assertEqual(len(contract), 1)
        self.assertEqual(contract.objective("Q0").requirement, QUESTION)


if __name__ == "__main__":
    unittest.main()
