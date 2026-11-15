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
    extract_question_contract,
    parse_contract_response,
)

QUESTION = "订单取消后库存如何回补，由哪个服务处理，失败时怎么重试？"


def _user_objective(objective_id="Q0", requirement=QUESTION, required=True,
                    kind="other", notes=()):
    return QuestionObjective(
        id=objective_id, requirement=requirement, origin="user",
        required=required, kind=kind, notes=tuple(notes))


def _discovered_objective(objective_id="Q1", requirement="取消消息由订单服务发出",
                          source="order/CancelService.java:42", source_kind="source_code",
                          required=False, kind="relationship"):
    return QuestionObjective(
        id=objective_id, requirement=requirement, origin="discovered",
        required=required, kind=kind, source=source, source_kind=source_kind)


class QuestionContractCreateTests(unittest.TestCase):
    def test_original_question_is_first_required_objective(self):
        contract = QuestionContract.create(QUESTION, [{"requirement": "库存回补由哪个服务处理",
                                                       "kind": "process"}])

        self.assertEqual(contract.original_question, QUESTION)
        self.assertEqual(len(contract), 2)
        root = contract.objective("Q0")
        self.assertEqual(root.requirement, QUESTION)
        self.assertEqual(root.origin, "user")
        self.assertTrue(root.required)
        self.assertEqual(root.notes, ("完整回答原问题",))
        self.assertEqual(contract.objectives[0], root)
        self.assertEqual(contract.objective("Q1").origin, "user")
        self.assertTrue(contract.objective("Q1").required)
        self.assertEqual(contract.extraction_status, "ok")

    def test_long_original_question_is_preserved(self):
        question = "订单" * 400
        contract = QuestionContract.create(question)

        self.assertEqual(contract.original_question, question)
        self.assertEqual(contract.objective("Q0").requirement, question)

    def test_long_extracted_objective_is_not_truncated(self):
        requirement = "核对完整字段透传关系" * 80
        contract = QuestionContract.create(QUESTION, [{"requirement": requirement}])

        self.assertEqual(contract.objective("Q1").requirement, requirement)

    def test_extracted_objectives_are_deduped_and_trimmed(self):
        contract = QuestionContract.create(QUESTION, [
            {"requirement": "  库存回补由哪个服务处理  ", "kind": "process"},
            {"requirement": "库存回补由哪个服务处理", "kind": "behavior"},
            "失败时怎么重试",
            {"requirement": "  ", "kind": "process"},
            {"requirement": "未知类型目标", "kind": "not-a-kind"},
        ])

        requirements = [obj.requirement for obj in contract.objectives]
        self.assertEqual(requirements, [QUESTION, "库存回补由哪个服务处理",
                                        "失败时怎么重试", "未知类型目标"])
        self.assertEqual(contract.objective("Q2").kind, "other")
        self.assertEqual(contract.objective("Q3").kind, "other")

    def test_extraction_yielding_only_the_original_question_is_fallback(self):
        contract = QuestionContract.create(QUESTION, [{"requirement": QUESTION}])

        self.assertEqual(len(contract), 1)
        self.assertEqual(contract.extraction_status, "fallback")

    def test_extraction_limit_keeps_first_six_objectives(self):
        extracted = [{"requirement": f"目标 {index}"} for index in range(10)]
        contract = QuestionContract.create(QUESTION, extracted)

        self.assertEqual(len(contract), 7)
        self.assertEqual(contract.objective("Q6").requirement, "目标 5")

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
        self.assertEqual(QuestionContract.fallback(QUESTION, "error").extraction_status,
                         "error")

    def test_unknown_extraction_status_is_rejected(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective()], extraction_status="guessed")

    def test_revision_must_be_positive(self):
        with self.assertRaises(ValueError):
            QuestionContract(QUESTION, [_user_objective()], revision=0)


class QuestionContractAppendTests(unittest.TestCase):
    def test_discovered_objective_is_appended_as_optional(self):
        contract = QuestionContract.create(QUESTION)
        discovered = contract.add_discovered(
            "取消消息由订单服务发出", source="order/CancelService.java:42")

        self.assertEqual(discovered.id, "Q1")
        self.assertEqual(discovered.origin, "discovered")
        self.assertFalse(discovered.required)
        self.assertEqual(discovered.source, "order/CancelService.java:42")
        self.assertEqual(discovered.source_kind, "source_code")
        self.assertEqual(contract.revision, 2)
        self.assertEqual([obj.id for obj in contract.objectives], ["Q0", "Q1"])

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
        contract = QuestionContract.create(QUESTION, [{"requirement": "库存回补由哪个服务处理"}])
        before = contract.objective("Q1")
        revised = contract.supplement("Q1", "订单服务调用库存服务")

        self.assertEqual(revised.requirement, before.requirement)
        self.assertEqual(revised.origin, before.origin)
        self.assertEqual(revised.kind, before.kind)
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
        contract = QuestionContract.create(QUESTION, [{"requirement": "库存回补由哪个服务处理"}])
        snapshot = contract.to_dict()
        contract.add_discovered("取消消息由订单服务发出", source="order/CancelService.java:42",
                                source_kind="source_code")

        self.assertEqual(snapshot["version"], 1)
        self.assertEqual(snapshot["originalQuestion"], QUESTION)
        self.assertEqual(snapshot["revision"], 1)
        self.assertEqual(snapshot["extractionStatus"], "ok")
        self.assertEqual([item["id"] for item in snapshot["objectives"]], ["Q0", "Q1"])
        self.assertEqual(contract.revision, 2)
        self.assertEqual(len(contract.to_dict()["objectives"]), 3)

    def test_prompt_view_separates_required_and_optional(self):
        contract = QuestionContract.create(QUESTION, [{"requirement": "库存回补由哪个服务处理"}])
        contract.add_discovered("取消消息由订单服务发出", source="order/CancelService.java:42")
        view = contract.prompt_view()

        required_part, optional_part = view.split("可选补充目标", 1)
        self.assertIn("Q0 [other] " + QUESTION, required_part)
        self.assertIn("Q1 [other] 库存回补由哪个服务处理", required_part)
        self.assertNotIn("Q2", required_part)
        self.assertIn("Q2 [relationship] 取消消息由订单服务发出", optional_part)
        self.assertIn("不作为必答", optional_part)

    def test_prompt_view_without_optional_objectives(self):
        contract = QuestionContract.create(QUESTION)
        self.assertEqual(contract.prompt_view(),
                         "调查契约（用户明确要求的目标不可删除）：\n"
                         f"- Q0 [other] {QUESTION}")


class ContractParseTests(unittest.TestCase):
    def test_code_fenced_json_is_parsed(self):
        text = "```json\n{\"objectives\":[{\"requirement\":\"库存回补由哪个服务处理\"}]}\n```"
        contract = parse_contract_response(text, QUESTION)

        self.assertEqual(contract.extraction_status, "ok")
        self.assertEqual([obj.requirement for obj in contract.objectives],
                         [QUESTION, "库存回补由哪个服务处理"])

    def test_json_wrapped_in_prose_is_parsed(self):
        text = ("以下是结果：{\"objectives\":[{\"requirement\":\"失败时怎么重试\","
                "\"kind\":\"behavior\"}]} 请查收。")
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

    def test_empty_objectives_falls_back(self):
        contract = parse_contract_response("{\"objectives\":[]}", QUESTION)

        self.assertEqual(contract.extraction_status, "fallback")
        self.assertEqual(len(contract), 1)

    def test_invalid_items_are_skipped_but_valid_items_survive(self):
        text = json.dumps({"objectives": [
            None, 42, {}, {"requirement": "  "},
            {"requirement": "库存回补由哪个服务处理", "kind": "not-a-kind"},
        ]})
        contract = parse_contract_response(text, QUESTION)

        self.assertEqual(contract.extraction_status, "ok")
        self.assertEqual(len(contract), 2)
        self.assertEqual(contract.objective("Q1").kind, "other")

    def test_extraction_prompt_stays_short_and_json_only(self):
        self.assertLess(len(CONTRACT_EXTRACTION_PROMPT), 500)
        self.assertIn("只输出 JSON", CONTRACT_EXTRACTION_PROMPT)
        for tool_name in ("Read", "Glob", "Grep"):
            self.assertNotIn(tool_name, CONTRACT_EXTRACTION_PROMPT)


class ContractExtractionTests(unittest.TestCase):
    def test_model_extraction_adds_user_objectives(self):
        model = FakeMessagesListChatModel(responses=[AIMessage(content=json.dumps({
            "objectives": [
                {"requirement": "库存回补由哪个服务处理", "kind": "process"},
                {"requirement": "失败时怎么重试", "kind": "behavior"},
            ]}))])
        contract = extract_question_contract(model, QUESTION)

        self.assertEqual(contract.extraction_status, "ok")
        self.assertEqual([obj.origin for obj in contract.objectives],
                         ["user", "user", "user"])
        self.assertTrue(all(obj.required for obj in contract.objectives))
        self.assertEqual(contract.objective("Q2").requirement, "失败时怎么重试")

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
        model = FakeMessagesListChatModel(responses=[AIMessage(content=json.dumps({
            "objectives": [{"requirement": "库存回补由哪个服务处理", "kind": "process"}],
        }))])
        contract = await aextract_question_contract(model, QUESTION)

        self.assertEqual(contract.extraction_status, "ok")
        self.assertEqual(contract.original_question, QUESTION)
        self.assertEqual(contract.objective("Q1").requirement, "库存回补由哪个服务处理")

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
