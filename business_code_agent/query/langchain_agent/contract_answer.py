"""Experimental extractive answer: response facts are rendered from source bindings."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .verification import Ref
from .business_answer import BusinessCase, ChangeOption, render_business, render_changes, question_needs


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contracts: list[str] = Field(default_factory=list, max_length=3)
    business_evidence: list[Ref] = Field(default_factory=list, max_length=6)
    cases: list[BusinessCase] = Field(default_factory=list, max_length=5)
    changes: list[ChangeOption] = Field(default_factory=list, max_length=4)


RULES = """本阶段没有工具，只输出 JSON：
{"contracts":["已核对的契约编号"],"cases":[{"contract":"契约编号","rules":["已交付分支编号"],"meaning":"结合分支原文解释业务区别"}],"changes":[{"contract":"契约编号","action":"reuse_fields","fields":["本题已有字段"]}],"business_evidence":[{"source":"S1","start":1,"end":4}]}。
选择能回答本题的最直接接口编号；每个接口的字段、透传关系和展示映射由程序从当前源码生成，不要重复撰写结论。
business_evidence 选择本轮已读原文中足以说明业务状态、失败与等待区别、异步恢复步骤的连续片段，优先每段12行以内，共最多6段、72行；需保留分支上下文时可用较长片段。
源码说明当前实现；业务补充知识说明约定；需求原文说明提出的目标，不能相互替代。
cases 引用本题 businessRules 的编号，不是映射规则编号。说明问题要求区分的情形，不能只重复字段；写入点的局部条件不等于全部路径，更不代表查询接口执行这些写入。异常 catch 中的写入与进入异常前的写入要分开，不能把异常前的准备状态都叫失败，也不能忽略后续覆盖。
cases.meaning 仅解释所选分支的业务含义，不撰写整个接口缺字段／页面已实现的总断言。程序检查来源绑定，不证明你的业务解释正确。
涉及 catch 时只说明该异常分支直接写入的成员；不能把调用方／try 中先前的值视为异常后的保证，程序将收紧这类解读。
changes.action 仅支持 reuse_fields、adjust_projection、add_fields、inspect_consumer；fields 必须是真实响应路径，adjust_projection 选择展示输出字段。不得自由撰写收益排名。已返回字段先复用，未核对消费端时建议有条件；只改一个字段的映射，无法保证区分其他输入字段的状态。
回答所问的区分和改动前提，避免把答案缩减成源码摘录。需求与业务资料可用 business_evidence 补充；它们不替代当前实现。
"""


def context_bundles(bundles):
    # Originals appear once in the source catalog, not again in every fact bundle.
    return [{**b, "evidence": [{k: v for k, v in e.items() if k != "content"} for e in b["evidence"]]} for b in bundles]


def request_messages(question, bundles, catalog, state=""):
    return [SystemMessage(content=RULES), HumanMessage(content=(
        f"当前问题：{question}\n\n已核对契约及原文：\n{json.dumps(context_bundles(bundles), ensure_ascii=False)}"
        f"\n\n{state}\n\n已读取的补充原文：\n{catalog.render()}"))]


def citation(bundle, ids, limit=3):
    sources = {e["id"]: e for e in bundle["evidence"]}
    refs = [sources[i] for i in ids if i in sources]
    # All evidence is retained in the trace; show the shortest useful source anchors.
    paths = {}
    for ref in refs:
        paths.setdefault(ref["path"], ref["start"])
    return "、".join(f"`{path}:{line}`" for path, line in list(paths.items())[:limit])


def complete_response(message, bundles, catalog, *, question=None):
    issues = []
    raw = message.text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        if message.tool_calls:
            raise ValueError("收尾阶段返回了工具调用")
        selection = Selection.model_validate_json(raw)
    except (ValidationError, ValueError):
        selection = Selection()
        issues.append("收尾格式不合格，保留程序已核对的结构事实；未重试模型")
    available = {b["contractId"]: b for b in bundles}
    chosen = []
    for cid in selection.contracts:
        if cid not in available:
            issues.append("丢弃未核对的契约编号：" + cid)
        elif cid not in chosen:
            chosen.append(cid)
    if not chosen:
        chosen = list(available)[:3]
    lines = []
    accepted = []
    for cid in chosen:
        b = available[cid]
        entry = b["entry"]
        lines.append(f"`{entry['method']} {entry['route']}`（{entry['application']}）")
        focus = {field for option in selection.changes if option.contract == cid for field in option.fields}
        focus.update(field for case in selection.cases if case.contract == cid for rule in b.get("businessRules", []) if rule["id"] in case.rules for field in rule["fields"])
        focus.update(m["outputField"] for m in b["mappings"])
        shown = [f for f in b["fields"] if f["path"] in focus] if selection.cases or selection.changes else b["fields"]
        certain = [f for f in shown if not f["conditional"]]
        conditional = [f for f in shown if f["conditional"]]
        if certain:
            names = "、".join(f"`{f['path']}`" for f in certain)
            lines.append("本题相关的已核对返回字段包括：" + names + "。")
            # Cite proof for the nested values, not merely the controller declaration.
            refs = list(dict.fromkeys(i for f in certain for i in f["evidence"]))
            lines.append("依据：" + citation(b, refs) + "。")
        if conditional:
            lines.append("部分返回分支包含：" + "、".join(f"`{f['path']}`" for f in conditional) + "；需结合分支条件判断。")
        for mapping in b["mappings"]:
            pieces = []
            for rule in mapping["rules"]:
                source = "其他值" if rule["default"] else "／".join(str(v) for v in rule["inputs"])
                target = "保留输入值" if rule["output"] == "input" else f"返回 `{rule['output']}`"
                pieces.append(source + " → " + target)
            lines.append("`" + mapping["outputField"] + "` 的映射输入是 " + "、".join(f"`{p}`" for p in mapping["inputFields"]) + "：" + "；".join(pieces) + "。")
            lines.append("依据：" + citation(b, mapping["evidence"]) + "。")
        if b["mappings"] and len(certain) > len(b["mappings"]):
            lines.append("单一展示字段的合并不等于完整响应缺少明细；以上已确认字段不需要再次新增。")
        if not b["fields"]:
            lines.append("本轮未取得可确认的返回字段；不能据此判断字段不存在。")
        if b["gaps"]:
            lines.append("仍有结构缺口：" + "；".join(dict.fromkeys(g["reason"] for g in b["gaps"])) + "。")
        accepted.append({"contractId": cid, "fields": [f["path"] for f in b["fields"]], "mappings": b["mappings"]})
    business_lines, business_refs, quote_blocks = 0, [], []
    for ref in selection.business_evidence:
        source = catalog.resolve(ref)
        if source is None or source.kind == "navigation":
            issues.append("丢弃未交付或导航类业务引用")
            continue
        count = ref.end - ref.start + 1
        if business_lines + count > 72:
            issues.append("业务原文超过总计72行预算，未交付后续片段")
            continue
        business_lines += count
        business_refs.append(ref.model_dump())
        labels = {"code": "源码", "business_context": "业务补充资料", "requirement": "需求原文", "project_document": "项目资料"}
        excerpt = catalog.excerpt(ref)
        quote_blocks.append((source.kind, f"{labels.get(source.kind, source.kind)} {catalog.citation(ref)}：\n```text\n{excerpt}\n```"))
    interpretations, accepted_cases, case_issues, interpretation_limits = render_business(selection, [available[cid] for cid in chosen], citation, catalog)
    changes, accepted_changes, change_issues = render_changes(selection, [available[cid] for cid in chosen])
    lines.extend(block for kind, block in quote_blocks if kind != "code" or not accepted_cases)
    lines.extend(interpretations)
    lines.extend(changes)
    issues.extend([*case_issues, *change_issues])
    needs = question_needs(question) if question is not None else {"business": True, "consumer": True, "changes": False}
    if chosen and (needs["consumer"] or needs["changes"]):
        lines.append("当前没有核对页面消费端。是否已展示、是否只需改一处以及改动收益仍需读取实际页面；若页面仅使用合并后的展示值，应先核对它是否可以利用现有明细。")
    elif not chosen:
        lines.append("本轮未完成契约字段核对，尚不能判断接口是否支持。需要定位实际入口并调用 TraceResponseFields。")
    if needs["business"] and not accepted_cases:
        lines.append("本轮尚未形成带分支依据的业务解释，以上字段与引文不能代替完整业务判断。")
    if needs["changes"] and not accepted_changes:
        lines.append("改动问题尚未形成有依据的条件选项，当前无法给出收益排序。")
    if issues:
        lines.append("核对说明：" + "；".join(issues) + "。")
    return "\n\n".join(lines), {"mode": "contracts", "accepted": accepted, "issues": issues,
                                    "businessEvidenceCount": len(business_refs), "businessEvidence": business_refs,
                                    "proposedBusinessEvidenceCount": len(selection.business_evidence),
                                    "businessCases": accepted_cases, "changeOptions": accepted_changes,
                                    "interpretationLimits": interpretation_limits,
                                    "answerVersion": 3,
                                    "questionNeeds": needs,
                                    "businessEvidenceLines": business_lines, "semanticProof": False}
