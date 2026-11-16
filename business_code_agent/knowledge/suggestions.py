"""Single-request suggestions for a selected Business Context document or section."""

from __future__ import annotations

import json
from collections.abc import Callable

from .business_context import BusinessContextConflict, BusinessContextFiles


SYSTEM_PROMPT = """你在帮助管理员维护业务补充知识 Markdown。只依据请求中的原文和补充材料，
比较两者并提出一份供人工审阅的建议。保留与变更无关的原文、范围和依据。
授权业务人员明确确认的约定或权威业务资料可作为业务修改的依据。需求描述表示目标，
源码或问答调查表示已看到的实现线索，单独出现时不能据此更改业务约定或断言已经上线。
现有规则是否取消、迁移或适用范围有歧义时，列为待业务确认。不要编造依据、实现或流程，
也不要把源码中的类、接口、调用链和字段细节复制到业务约定。

仅返回 JSON 对象，字段为：
- action: add（向当前范围增加内容）、revise（修订）、unchanged（维持原文）或 needs_confirmation（待业务确认）。
- reason: 简要说明有哪些一致和差异，以及为何采取该处理。
- replacement: add 或 revise 时给出替换选定范围后的完整 Markdown，章节保留其标题层级。其余情况返回空字符串。
- basis: 字符串数组，列出本次建议使用的具体输入依据。
- pendingQuestions: 字符串数组，列出仍需人工确认的具体问题。
JSON 中的 Markdown 换行需转义。用空数组表示没有条目，JSON 外无需解释。
"""

_CHANGES = {"add", "revise"}
_ACTIONS = {*_CHANGES, "unchanged", "needs_confirmation"}


class InvalidSuggestion(RuntimeError):
    """The model did not provide a usable suggestion."""


def suggest(
    files: BusinessContextFiles, *, name: str, previous: str, note: str,
    section_start: int | None, complete: Callable[[str, str], str],
) -> dict:
    if not files.editable:
        raise ValueError("历史业务基线迁移后可生成修订建议。")
    if not isinstance(note, str) or not note.strip():
        raise ValueError("请填写变更说明或相关资料")
    document = files.read(name)
    if document["content"] != previous:
        raise BusinessContextConflict("文档已更新。请重新打开最新原文，再生成建议。")
    prefix, original, suffix = _selected_text(document, section_start)
    request = json.dumps({"original": original, "additionalMaterial": note}, ensure_ascii=False)
    proposed = _parse_suggestion(complete(SYSTEM_PROMPT, request))
    replacement = proposed["replacement"]
    proposal_content = None
    if proposed["action"] in _CHANGES:
        # Keep the existing line ending convention and a separator before the next heading.
        newline = "\r\n" if "\r\n" in original else "\n"
        replacement = replacement.replace("\r\n", "\n").replace("\n", newline)
        if suffix:
            replacement += newline * 2
        elif original.endswith("\n"):
            replacement += newline
        proposal_content = prefix + replacement + suffix
    return {**proposed, "original": original, "proposalContent": proposal_content}


def _selected_text(document: dict, section_start: int | None) -> tuple[str, str, str]:
    content = document["content"]
    if section_start is None:
        return "", content, ""
    section = next((item for item in document["sections"] if item["startLine"] == section_start), None)
    if type(section_start) is not int or section is None:
        raise ValueError("请选择有效的 Markdown 章节")
    lines = content.splitlines(keepends=True)
    start, end = section["startLine"] - 1, section["endLine"]
    return "".join(lines[:start]), "".join(lines[start:end]), "".join(lines[end:])


def _parse_suggestion(answer: str) -> dict:
    try:
        data, _ = json.JSONDecoder().raw_decode(answer, answer.index("{"))
    except ValueError as exc:
        raise InvalidSuggestion("Pi 未返回可解析的修订建议，请重试。") from exc
    if not isinstance(data, dict) or not isinstance(data.get("action"), str) or data["action"] not in _ACTIONS:
        raise InvalidSuggestion("Pi 返回的建议类型无效，请重试。")
    reason, replacement = data.get("reason"), data.get("replacement")
    basis, questions = data.get("basis"), data.get("pendingQuestions")
    if (not isinstance(reason, str) or not reason.strip()
            or not isinstance(replacement, str) or (data["action"] in _CHANGES and not replacement.strip())
            or not all(isinstance(items, list) and all(isinstance(item, str) for item in items)
                       for items in (basis, questions))):
        raise InvalidSuggestion("Pi 返回的建议缺少说明或正文，请重试。")
    return {
        "action": data["action"], "reason": reason.strip(),
        "replacement": replacement.strip() if data["action"] in _CHANGES else "",
        "basis": basis, "pendingQuestions": questions,
    }
