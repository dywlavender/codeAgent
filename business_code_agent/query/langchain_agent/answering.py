"""One bounded model call proposes facts; the program checks and renders it."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from .evidence import EvidenceCatalog
from .verification import verify_response


RULES = """你是源码事实整理助手。本阶段没有工具。只输出一个 JSON 对象，不输出自由撰写的标题或结论。
将问题拆为返回数据、单一展示映射、页面实际使用、业务行为、需求与业务约定中相关的层次。不要为不相关层次补检查项。
数据是否已返回与页面是否已使用是不同判断。多种输入映射为一个展示值，不能证明完整响应缺少其他字段；字段已返回也不能证明页面已实现。
只能依据本轮原文。每条事实附 source 编号和准确行号，程序从该范围取得真实引文，不要改写代码。可省略 quote；如提供 quote，必须逐字出现且不带行号前缀。不能把导航或需求当作当前实现。
接口透传对象的字段应写出完整路径，并引用调用端和对象生产端；不能仅凭同名就断定连接。未取得连接依据时列为 gaps。
只列足以回答的核心事实，优先引用最短支撑片段。没有观察到的字段不要编造为 returned_field，更不能当作已证明不存在。
resources.scope: api_data/display/ui/business/requirement/context。
facts.kind: returned_field/mapping/ui/behavior/requirement/context，须与资源层次一致。
returned_field 用 field 给出真实返回路径（数组写 []），每个路径分段都应在引文中；mapping 给 field、inputs、output，须有原文依据。
其他事实用 statement 概括，附原文。答案只能引用本次事实，不得把展示映射当作接口数据缺失证据。
answers.kind: data_fields/display/ui/behavior/requirement/context；data_fields 必须列 required_fields，说明本题需要哪些数据。
recommendations 是建议，不是已实现。adds_fields 列拟新增字段，unverified_impacts 列真正影响建议的未确认事项，不承诺未核实的前端兼容性。
格式如下（只保留相关资源和事实；所有列表可空，不能编造）：
{"resources":[{"id":"R1","label":"本题接口返回数据","scope":"api_data"}],
"facts":[{"id":"F1","resource":"R1","kind":"returned_field","field":"receipt.state","refs":[{"source":"S1","start":1,"end":2}]}],
"answers":[{"resource":"R1","kind":"data_fields","required_fields":["receipt.state"],"facts":["F1"],"verdict":"supported","summary":""}],
"recommendations":[{"resource":"R1","change":"拟改动","benefit":"预期收益","facts":["F1"],"adds_fields":[],"unverified_impacts":[]}],"gaps":[]}
verdict 仅为 supported/unsupported/unknown。不要复制示例字段；程序将核对原文与字段，并根据已确认数据形成最终结论。
"""


def request_messages(question, catalog: EvidenceCatalog, history="", investigation=""):
    state = f"\n\n{investigation}" if investigation else ""
    return [
        SystemMessage(content=RULES),
        HumanMessage(
            content=f"对话背景（不是实现证据）：\n{history}\n\n当前问题：{question}{state}\n\n本轮实际交付的原文：\n{catalog.render()}"
        ),
    ]


def complete_response(message, catalog):
    if message.tool_calls:
        return verify_response("", catalog)
    return verify_response(message.text, catalog)
