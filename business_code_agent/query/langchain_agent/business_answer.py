"""Source-bound business interpretations and conditional change options."""

from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field
from .verification import Ref


def question_needs(question):
    return {
        "business": bool(re.search(r"状态|区分|为什么|为何|失败|重试|条件|业务|改动|修改|改一|收益|status|fail|retry|why|change|impact", question, re.I)),
        "consumer": bool(re.search(r"页面|前端|界面|\bUI\b|frontend|consumer", question, re.I)),
        "changes": bool(re.search(r"改|收益|建议|优化|change|fix|improv", question, re.I)),
    }


class BusinessCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: str
    rules: list[str] = Field(min_length=1, max_length=5)
    meaning: str = Field(min_length=1, max_length=360)


class ChangeOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: str
    action: Literal["reuse_fields", "adjust_projection", "add_fields", "inspect_consumer"]
    fields: list[str] = Field(default_factory=list, max_length=8)


def render_business(selection, bundles, cite, catalog):
    lines, accepted, issues, limits = [], [], [], []
    available = {b["contractId"]: b for b in bundles}
    for case in selection.cases:
        bundle = available.get(case.contract)
        rules = {r["id"]: r for r in bundle.get("businessRules", [])} if bundle else {}
        if any(rid not in rules for rid in case.rules):
            issues.append("业务解释引用的分支未交付：" + ",".join(case.rules))
            continue
        facts = [rules[rid] for rid in dict.fromkeys(case.rules)]
        fields = {f["path"]: f for f in bundle["fields"]}
        required = {field for fact in facts for field in fact["fields"]}
        if not required <= fields.keys():
            issues.append("业务分支依赖的返回字段尚未交付")
            continue
        evidence = list(dict.fromkeys([eid for fact in facts for eid in fact["evidence"]]
                                     + [eid for field in required for eid in fields[field]["evidence"]]))
        sources = {e["id"]: e for e in bundle["evidence"]}
        def delivered(eid):
            ref = sources.get(eid)
            source = next((s for s in catalog.sources if ref and s.path == ref["path"] and s.kind == "code"), None)
            return source is not None and catalog.resolve(Ref(source=source.id, start=ref["start"], end=ref["end"])) is not None
        if not all(delivered(eid) for eid in evidence):
            issues.append("业务分支原文未完整交付收尾：" + ",".join(case.rules))
            continue
        clauses = []
        for fact in facts:
            value = fact["assigned"]["expression"]
            where = "异常 catch 分支" if fact["phase"] == "catch" else "try 分支" if fact["phase"] == "try" else "非 catch 写入点"
            conditions = "，".join(("满足 " if c["truth"] else "不满足 ") + c["expression"] for c in fact["conditions"])
            target = "／".join(f"`{field}`" for field in fact["fields"])
            clauses.append(f"{where}{'，' + conditions if conditions else ''}：{target} 写入 `{value}`")
        catches = [fact for fact in facts if fact["phase"] == "catch"]
        meaning = case.meaning
        if catches:
            # A catch write proves its own members, not a complete exception outcome.
            # Earlier writes in the try/caller may have happened before the exception.
            outcomes = ["／".join(f"`{field}`" for field in fact["fields"]) + " 对应异常处理中的写入 `" + fact["assigned"]["expression"] + "`" for fact in catches]
            meaning = "；".join(outcomes) + "。可据这些实际返回字段定位异常处理状态；其它成员的最终值还取决于异常发生位置，不能把调用异常前的值当成异常后的保证。"
            limits.append({"contract": case.contract, "rules": case.rules,
                           "reason": "exception_members_only; earlier_and_later_writes_not_proven"})
        lines.append("；".join(clauses) + "。\n\n业务解读：" + meaning + "\n\n依据：" + cite(bundle, evidence) + "。")
        accepted.append({**case.model_dump(), "renderedMeaning": meaning,
                         "displayMode": "catch_members_only" if catches else "source_bound_interpretation"})
    if accepted:
        lines.insert(0, "源码中的相关写入分支如下。它们是局部条件，不能代替对提前返回、后续写入和实际调用时机的核对；业务解读仍需独立复核。")
    return lines, accepted, issues, limits


def render_changes(selection, bundles):
    available = {b["contractId"]: b for b in bundles}
    lines, accepted, issues = [], [], []
    for option in selection.changes:
        bundle = available.get(option.contract)
        if not bundle:
            issues.append("改动选项引用未核对契约")
            continue
        returned = {f["path"] for f in bundle["fields"]}
        known = [field for field in option.fields if field in returned]
        unknown = [field for field in option.fields if field not in returned]
        names = "、".join(f"`{field}`" for field in known)
        if option.action == "inspect_consumer":
            lines.append("改动前先核对实际页面读取哪些响应字段；当前没有消费端依据，无法确定单点改动的收益排序。")
        elif option.action == "reuse_fields" or (option.action == "add_fields" and known):
            if known:
                lines.append(f"{names} 已在响应中，先核对消费端能否复用；无需再次新增这些字段。页面改动是否足够仍取决于实际消费代码。")
            else:
                issues.append("复用建议未列出已确认字段")
        elif option.action == "adjust_projection":
            mappings = [m for m in bundle["mappings"] if m["outputField"] in known]
            if not mappings:
                issues.append("展示映射建议没有对应的已核对转换")
                continue
            targets = {field for case in selection.cases if case.contract == option.contract
                       for rule in bundle.get("businessRules", []) if rule["id"] in case.rules for field in rule["fields"]}
            for mapping in mappings:
                inputs = set(mapping["inputFields"])
                missing = sorted(targets - inputs)
                message = (f"可评估调整 `{mapping['outputField']}` 的展示映射；当前输入为 "
                           + "、".join(f"`{field}`" for field in mapping["inputFields"]) + "。")
                if missing:
                    message += "本题分支还涉及 " + "、".join(f"`{field}`" for field in missing) + "；仅拆分现有输入的映射值，不能保证区分这些字段的不同状态。需复用相关明细或调整判别输入。"
                message += "消费端是否接受新值仍未核对，不能保证只改此处即可实现页面目标。"
                lines.append(message)
        if unknown:
            lines.append("尚未确认的建议字段：" + "、".join(f"`{field}`" for field in unknown) + "。未解析到不等于不存在，先补查完整响应与实际需求。")
        accepted.append(option.model_dump())
    return list(dict.fromkeys(lines)), accepted, issues
