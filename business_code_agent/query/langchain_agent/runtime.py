"""LangChain 1.2 agent: model/tool loop, context, scoped files and progress."""

from __future__ import annotations

import json
import asyncio
from contextlib import suppress
import os
import time
import uuid
from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from business_code_agent.query.runtime import RuntimeResult, RuntimeErrorBase
from business_code_agent.query.workspace import project_index, read_turn_context, workspace_sources
from business_code_agent.query.navigation import read_navigation, render_navigation
from .context import SessionStore, compact, retire_changed_sources, answer_evidence
from .files import SourceFiles
from .evidence import EvidenceCatalog
from .answering import RULES as FACT_RULES, request_messages, complete_response
from .investigation import Investigation, InvestigationState
from .trace import TraceRecorder
from .coordinator import RetrievalCoordinator
from .material import MATERIAL_GUIDANCE, MATERIAL_RULES


_AGENT_VERSION = 11
_SEARCH_TOOLS = {"Glob", "Grep", "SearchCode", "FindCapability"}


class _Stopped(Exception):
    pass


class LangChainRuntime:
    runtime_name = "LANGCHAIN"

    def __init__(
        self,
        *,
        model=None,
        environment=None,
        max_calls=None,
        max_seconds=None,
        index_root=None,
        contract_root=None,
        code_search_db=None,
        project_id=None,
        answer_mode=None,
        workflow_mode=None,
    ):
        self.environment = dict(os.environ if environment is None else environment)
        self.model = model
        # Production investigations are unbounded. Only explicit callers such
        # as controlled evaluations may supply their own limits.
        self.max_calls = int(max_calls) if max_calls else None
        self.max_seconds = float(max_seconds) if max_seconds else None
        if self.max_calls is not None and self.max_calls < 1:
            raise ValueError("max_calls 必须为正整数或不设置")
        if self.max_seconds is not None and self.max_seconds < 0:
            raise ValueError("max_seconds 必须为正数或不设置")
        self.index_root = Path(index_root) if index_root else None
        self.contract_root = Path(contract_root) if contract_root else None
        self.code_search_db = Path(code_search_db) if code_search_db else None
        if self.code_search_db and not self.code_search_db.is_file():
            raise RuntimeErrorBase("显式指定的代码符号索引不存在: %s" % self.code_search_db)
        self.project_id = project_id
        self.answer_mode = answer_mode or self.environment.get(
            "BUSINESS_CODE_AGENT_ANSWER_MODE", "plain"
        )
        if self.answer_mode not in {"facts", "plain", "contracts"}:
            raise ValueError("ANSWER_MODE 仅支持 facts、plain 或 contracts")
        if self.answer_mode == "contracts" and self.contract_root is None:
            raise ValueError("contracts 模式需要显式准备的工程契约索引")
        self.workflow_mode = workflow_mode or self.environment.get("BUSINESS_CODE_AGENT_WORKFLOW", "coordinated")
        if self.workflow_mode not in {"basic", "tracked", "coordinated"}:
            raise ValueError("WORKFLOW 仅支持 basic、tracked 或 coordinated")

    def chat_model(self):
        if self.model is not None:
            return self.model
        env = self.environment
        if env.get("BUSINESS_CODE_MODEL_ENABLED", "true").lower() not in {
            "true",
            "1",
            "yes",
        }:
            raise RuntimeErrorBase(
                "当前模型连接已禁用，请启用 BUSINESS_CODE_MODEL_ENABLED"
            )
        if env.get("BUSINESS_CODE_MODEL_PROVIDER", "openai").lower() != "openai":
            raise RuntimeErrorBase(
                "当前运行时需要 OpenAI 兼容协议；请设置 BUSINESS_CODE_MODEL_PROVIDER=openai"
            )
        if not all(
            env.get("BUSINESS_CODE_MODEL_" + key)
            for key in ("NAME", "API_KEY", "BASE_URL")
        ):
            raise RuntimeErrorBase(
                "请配置 BUSINESS_CODE_MODEL_NAME、BASE_URL、API_KEY 后启动 LangChain 问答"
            )
        extra = {}
        if env.get("BUSINESS_CODE_MODEL_ENABLE_THINKING") is not None:
            extra["enable_thinking"] = env[
                "BUSINESS_CODE_MODEL_ENABLE_THINKING"
            ].lower() in {"1", "true", "yes"}
        return ChatOpenAI(
            model=env["BUSINESS_CODE_MODEL_NAME"],
            api_key=env["BUSINESS_CODE_MODEL_API_KEY"],
            base_url=env.get("BUSINESS_CODE_MODEL_BASE_URL"),
            temperature=float(env.get("BUSINESS_CODE_MODEL_TEMPERATURE", ".2")),
            timeout=float(env.get("BUSINESS_CODE_MODEL_TIMEOUT", "120")),
            max_retries=0,
            max_tokens=int(env.get("BUSINESS_CODE_AGENT_OUTPUT_TOKENS", "4096")),
            use_responses_api=False,
            extra_body=extra,
        )

    def ask(
        self, question, *, workspace, session_id=None, event_callback=None,
        cancel_check=None, repositories=None,
    ):
        trace = TraceRecorder(workspace, self.project_id, question, {
            "agentVersion": _AGENT_VERSION, "requestedModel": self.environment.get("BUSINESS_CODE_MODEL_NAME"),
            "maxCalls": self.max_calls, "maxSeconds": self.max_seconds,
            "answerMode": self.answer_mode, "workflowMode": self.workflow_mode,
            "outputTokens": self.environment.get("BUSINESS_CODE_AGENT_OUTPUT_TOKENS", "4096"),
            "thinking": self.environment.get("BUSINESS_CODE_MODEL_ENABLE_THINKING"),
            "temperature": self.environment.get("BUSINESS_CODE_MODEL_TEMPERATURE", ".2"),
            "contextChars": self.environment.get("BUSINESS_CODE_AGENT_CONTEXT_CHARS", "64000"), "maxRetries": 0,
        })
        if event_callback:
            event_callback({"eventType": "trace", "payload": {"tracePath": str(trace.path)}})
        try:
            return self._ask(question, workspace=workspace, session_id=session_id, event_callback=event_callback,
                             cancel_check=cancel_check, repositories=repositories, trace=trace)
        except BaseException as exc:
            trace.complete("failed", errorType=type(exc).__name__, error=str(exc))
            raise

    def _ask(
        self,
        question,
        *,
        workspace,
        session_id=None,
        event_callback=None,
        cancel_check=None,
        repositories=None,
        trace=None,
    ):
        started = time.monotonic()
        deadline = self.max_seconds
        sources = workspace_sources(Path(workspace), repositories)
        files = SourceFiles(workspace, repositories)
        navigation = read_navigation(workspace, files.roots, self.project_id)
        navigation_text = render_navigation(navigation)
        investigation = InvestigationState(self.project_id or navigation.get("projectId"), files, navigation)
        contracts = None
        if self.contract_root is not None:
            from .contracts import ContractState

            contracts = ContractState(self.contract_root, self.project_id or navigation.get("projectId"), files, question, navigation)
        trace.value["projectId"] = self.project_id or navigation.get("projectId")
        coordinator = None
        store = SessionStore(workspace)
        scope = {
            "sources": [[label, str(root)] for label, root in sources],
            "answerMode": self.answer_mode,
            "workflowVersion": 2,
            "workflowMode": self.workflow_mode,
            "applications": navigation["applications"],
            "contractVersion": contracts.index.snapshot["version"] if contracts else None,
        }
        if contracts:
            scope["contractAnswerVersion"] = 3
        session = (
            session_id
            if session_id and session_id.startswith("LC-")
            else "LC-" + uuid.uuid4().hex
        )
        messages = retire_changed_sources(store.load(session, scope), files) + [
            HumanMessage(content=question)
        ]
        events, calls, token_usage, reported = [], 0, {}, set()
        tool_events = set()
        attempted_searches, skipped_searches = set(), set()

        def state_snapshot():
            value = investigation.snapshot()
            if contracts:
                value["contracts"] = contracts.snapshot()
            if coordinator:
                value["retrievalPlan"] = coordinator.snapshot()
            return value

        def emit(kind, **payload):
            if kind == "tool":
                key = (payload["id"], payload["status"])
                if key in tool_events:
                    return
                tool_events.add(key)
            event = {"sequence": len(events) + 1, "eventType": kind, "payload": payload,
                     "elapsedMs": round((time.monotonic() - started) * 1000, 3)}
            events.append(event)
            if kind != "trace":
                trace.event(event)
            if event_callback:
                event_callback(event)

        trace.on_update = lambda path: emit("trace", tracePath=path)
        trace.write()

        def decisions():
            if coordinator:
                for decision in coordinator.drain_decisions():
                    emit("retrieval", **decision, state=coordinator.snapshot())

        def skip_repeated_search(call):
            name = call["name"]
            if name not in _SEARCH_TOOLS or name not in tool_map:
                return None
            try:
                args = tool_map[name].args_schema.model_validate(call["args"]).model_dump(mode="json")
            except ValidationError:
                return None
            key = RetrievalCoordinator.key(name, args)
            if key not in attempted_searches:
                attempted_searches.add(key)
                return None
            skipped_searches.add(call["id"])
            return json.dumps({"skipped": True, "reason": "本轮已尝试相同检索，请复用已有结果。"}, ensure_ascii=False)

        def emit_tool_result(message):
            status = "error" if message.status == "error" else "completed"
            if message.tool_call_id in skipped_searches:
                status = "skipped"
            emit("tool", id=message.tool_call_id, name=message.name, status=status, output=str(message.content),
                 origin="workflow" if message.tool_call_id.startswith("workflow-") else "model")

        class ContextBudget(AgentMiddleware):
            async def awrap_tool_call(inner, request, handler):
                call = request.tool_call
                skipped = skip_repeated_search(call)
                if skipped is not None:
                    return ToolMessage(content=skipped, name=call["name"], tool_call_id=call["id"])
                return await handler(request)

            async def abefore_model(inner, state, runtime):
                if cancel_check and cancel_check():
                    raise _Stopped("cancelled")
                if deadline is not None and time.monotonic() - started >= deadline:
                    raise _Stopped("budget")
                observed = list(state["messages"])
                generated = []

                async def invoke(call):
                    skipped = skip_repeated_search(call)
                    if skipped is not None:
                        return skipped
                    try:
                        value = await tool_map[call["name"]].ainvoke(call["args"])
                        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                    except Exception as exc:
                        return json.dumps({"error": str(exc), "errorType": type(exc).__name__}, ensure_ascii=False)

                while True:
                    investigation.observe(observed)
                    if contracts:
                        contracts.observe(observed)
                    batch = []
                    if coordinator:
                        coordinator.observe(observed)
                        coordinator.reconcile_material(investigation)
                        decisions()
                        batch = coordinator.next_batch()
                    elif contracts:
                        cid = contracts.next_business()
                        if cid is not None:
                            batch = [{"name": "TraceBusinessRules", "args": {"contract_id": cid},
                                      "reason": "核对返回字段的业务分支写入点"}]
                    if not batch:
                        pending = (contracts.next_read() if contracts and not coordinator else None) or investigation.next_read()
                        if pending is None:
                            break
                        batch = [{"name": "Read", "args": pending.model_dump(), "reason": "补读待确认项指向的源码"}]
                    if cancel_check and cancel_check():
                        raise _Stopped("cancelled")
                    if deadline is not None and time.monotonic() - started >= deadline:
                        raise _Stopped("budget")
                    planned = [{"id": "workflow-" + uuid.uuid4().hex, "name": a["name"], "args": a["args"]} for a in batch]
                    for call, action in zip(planned, batch):
                        emit("tool", id=call["id"], name=call["name"], input=call["args"],
                             status="running", origin="workflow", reason=action["reason"])
                    outputs = await controlled(asyncio.gather(*(invoke(call) for call in planned)))
                    pair = [AIMessage(content="", tool_calls=planned)]
                    for call, output in zip(planned, outputs):
                        failed = bool(json.loads(output).get("error"))
                        result = ToolMessage(content=output, name=call["name"], tool_call_id=call["id"],
                                             status="error" if failed else "success")
                        pair.append(result)
                        emit_tool_result(result)
                    observed.extend(pair)
                    generated.extend(pair)

                if generated:
                    return {"messages": generated}
                last = next((message for message in reversed(observed) if isinstance(message, AIMessage)), None)
                if last and last.tool_calls and all(call["id"] in skipped_searches for call in last.tool_calls):
                    emit("retrieval", action="finalize", label="本轮补查只重复了已尝试的检索，使用已有原文作答")
                    raise _Stopped("finalize")
                return None

            def prepare(inner, request):
                nonlocal calls
                if cancel_check and cancel_check():
                    raise _Stopped("cancelled")
                if deadline is not None and time.monotonic() - started >= deadline:
                    raise _Stopped("budget")
                if self.max_calls is not None and calls >= self.max_calls - 1:
                    raise _Stopped("finalize")
                investigation.observe(request.messages)
                if contracts:
                    contracts.observe(request.messages)
                system = SystemMessage(content=str(request.system_message.content) + "\n\n" + investigation.render()
                                       + ("\n\n" + contracts.render() if contracts else "")
                                       + ("\n\n" + coordinator.render() if coordinator else ""))
                limit = int(self.environment.get("BUSINESS_CODE_AGENT_CONTEXT_CHARS", "64000"))
                bounded = compact(
                    request.messages,
                    max(0, limit - len(system.content)),
                )
                if coordinator:
                    original, _ = answer_evidence(bounded, float("inf"))
                    coordinator.revalidate(EvidenceCatalog.from_text(self.project_id, original))
                    system = SystemMessage(content=str(request.system_message.content) + "\n\n" + investigation.render()
                                           + ("\n\n" + contracts.render() if contracts else "") + "\n\n" + coordinator.render())
                if sum(len(str(m.content)) for m in bounded) + len(system.content) > limit:
                    raise _Stopped("context")
                calls += 1
                emit("status", phase="thinking", label=f"正在分析（第 {calls} 轮）")
                prepared = request.override(messages=bounded, system_message=system)
                entry = trace.begin("investigation", [system, *bounded], request.tools, state_snapshot())
                return prepared, entry

            def wrap_model_call(inner, request, handler):
                prepared, entry = inner.prepare(request)
                try:
                    response = handler(prepared)
                except BaseException as exc:
                    trace.finish(entry, error=exc)
                    raise
                trace.finish(entry, response.result)
                return response

            async def awrap_model_call(inner, request, handler):
                prepared, entry = inner.prepare(request)
                try:
                    response = await handler(prepared)
                except BaseException as exc:
                    trace.finish(entry, error=exc)
                    raise
                trace.finish(entry, response.result)
                return response

        instructions = (Path(workspace) / "CLAUDE.md").read_text(encoding="utf-8")
        prompt = (
            instructions
            + "\n"
            + read_turn_context(Path(workspace))
            + "\n"
            + project_index(sources)
        )
        prompt += (
            "\n先明确本题要核实的结论。文件明确时直接 Read，需要定位时使用 Glob/Grep。"
            "证据支持本题结论时直接回答，给出关键条件和路径行号。"
            "证据不足时，沿着有机会改变结论的未尝试线索补查，复用已有检索结果。"
            "本轮找不到进一步线索时，回答已能确认的部分并说明具体缺口。"
            "历史回答仅用于理解追问。"
        )
        prompt += "\n\n## 本轮授权的资料路径\n" + "\n".join(
            f"- {mount}/" for mount, _ in files.roots
        )
        prompt += (
            "\n工具路径使用这些根目录下的相对路径；仓库名以此清单为准，不要猜测仓库名。"
        )
        prompt += "\n\n" + navigation_text
        prompt += (
            "\n跨系统问题按实际路由、配置或事件核对调用端、连接和目标端。"
            "问题涉及页面时，核对该系统页面的请求、字段取值和展示规则，分别说明后端返回和页面行为。"
        )
        tools = files.tools()
        if contracts:
            tools.extend(contracts.tools())
            prompt += (
                "\n本轮已显式准备 Spring HTTP 契约索引，可用 FindCapability 定位入口，"
                "再用 TraceResponseFields 一次核对返回字段、透传对象和配置绑定的连接；"
                "仅追踪连接也可用 TraceContract。未解析不等于不存在，缺口将由程序安排 Read。"
                "这是结构证据；业务状态含义、故障原因、异步恢复仍需读取源码／业务资料，不能只看字段名。"
                "接口字段可用 TraceResponseFields 核对；索引未覆盖时可读取相关源码。"
                "涉及状态或分支条件时，用 TraceBusinessRules 定位字段写入点，并结合源码核对提前返回与执行时机。"
                "涉及改动收益时，按本题范围核对字段、映射输入和实际消费端。"
            )
            if self.workflow_mode != "coordinated":
                prompt += "\n当前问题的契约候选，仅用于定位。\n" + json.dumps(
                    contracts.index.find(question), ensure_ascii=False
                )
        if self.workflow_mode in {"tracked", "coordinated"}:
            tools.append(Investigation)
            prompt += (
                "\n用 Investigation 记录会影响本题结论的待确认项。"
                "知道具体文件和位置时提交 next_read，核对后更新同一项的引用。"
                "相关资料缺失时可标为 unavailable 并说明缺什么。"
            )
        if self.index_root and (self.index_root / "index.json").is_file():
            from business_code_agent.codeintel.hybrid.search import search_tool

            metadata = json.loads(
                (self.index_root / "index.json").read_text(encoding="utf-8")
            )
            if self.project_id and metadata["projectId"] != self.project_id:
                raise RuntimeErrorBase("检索索引不属于当前工程")
            tools.append(search_tool(self.index_root, files))
            prompt += "\n可先用 SearchCode 按业务问题定位函数、类和跨文件线索，再用 Read 核对完整条件与连接两端。"
        if self.code_search_db:
            from business_code_agent.query.langchain_agent.code_search import search_symbols_tool

            tools.append(search_symbols_tool(self.code_search_db))
            prompt += (
                "\n可用 SearchSymbols 按符号名或全限定名精确检索代码符号，得到 fqn 和 file:line 定位线索；"
                "结论必须 Read 源码核实，查不到不代表代码不存在。"
            )
        tool_map = {tool.name: tool for tool in tools}
        if self.workflow_mode == "coordinated":
            coordinator = RetrievalCoordinator(question, files, navigation, tool_map, contracts)
            prompt += "\n程序按本题目标安排定位、契约核对和缺口补读；已交付原文可复用。候选仍需核对，模型可补充 Investigation 和搜索。"
            if coordinator.material:
                prompt += "\n" + MATERIAL_RULES + MATERIAL_GUIDANCE
            decisions()
        chat = self.chat_model()
        agent = create_agent(
            chat,
            tools=tools,
            system_prompt=prompt,
            middleware=[ContextBudget()],
        )
        emit("status", phase="starting", label="正在准备资料")
        final = ""
        stop = None
        finalization = False
        verification = None
        discarded_draft = False

        def record_usage(message):
            for key, value in (message.usage_metadata or {}).items():
                if isinstance(value, (int, float)):
                    token_usage[key] = token_usage.get(key, 0) + value
            if message.response_metadata.get("model_name"):
                reported.add(message.response_metadata["model_name"])

        async def controlled(awaitable):
            pending = asyncio.ensure_future(awaitable)
            try:
                while not pending.done():
                    await asyncio.wait([pending], timeout=0.1)
                    if cancel_check and cancel_check():
                        raise _Stopped("cancelled")
                    if deadline is not None and time.monotonic() - started >= deadline:
                        raise _Stopped("budget")
                return pending.result()
            finally:
                if not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending
                elif not pending.cancelled():
                    pending.exception()

        async def compose():
            nonlocal calls, final, finalization, verification
            history = "\n".join(
                ("用户：" if m.type == "human" else "历史回答（非源码依据）：")
                + str(m.content)
                for m in messages
                if m.type == "human" or (isinstance(m, AIMessage) and not m.tool_calls)
            )[-6000:]
            limit = int(
                self.environment.get("BUSINESS_CODE_AGENT_CONTEXT_CHARS", "64000")
            )
            investigation.observe(messages)
            if contracts:
                contracts.observe(messages)
            handoff = navigation_text + "\n\n" + investigation.render()
            if contracts:
                handoff += "\n\n" + contracts.render()
            if coordinator:
                coordinator.reconcile_material(investigation)
                handoff += "\n\n" + coordinator.render()
            bundles = contracts.bundles() if contracts and self.answer_mode == "contracts" else []
            omitted_contracts = []
            if self.answer_mode == "contracts":
                from .contract_answer import context_bundles
                bundle_size = lambda items: len(json.dumps(context_bundles(items), ensure_ascii=False))
            else:
                bundle_size = lambda items: len(json.dumps(items, ensure_ascii=False))
            # Keep complete bundles under the same message budget as file evidence.
            while bundles and bundle_size(bundles) > limit // 2:
                omitted_contracts.append(bundles.pop()["contractId"])
            overhead = len(FACT_RULES) + 2000 if self.answer_mode == "facts" else 2600 + bundle_size(bundles)
            evidence, omitted = answer_evidence(
                messages, float("inf") if self.answer_mode == "contracts" else max(0, limit - len(history) - len(question) - len(handoff) - overhead)
            )
            catalog = EvidenceCatalog.from_text(self.project_id or str(Path(workspace).resolve()), evidence)
            if self.answer_mode == "contracts":
                catalog, omitted = catalog.merged().bounded(max(0, limit - len(question) - len(handoff) - overhead))
            investigation.revalidate(catalog)
            if coordinator:
                coordinator.revalidate(catalog)
            handoff = navigation_text + "\n\n" + investigation.render()
            if contracts:
                handoff += "\n\n" + contracts.render()
            if coordinator:
                handoff += "\n\n" + coordinator.render()
            if omitted_contracts:
                handoff += "\n[上下文预算未交付的契约，不能用作本次结论] " + "、".join(omitted_contracts)
            finalization = True
            emit("status", phase="composing", label="正在依据已读原文整理答案")
            rules = (
                "根据已取得原文回答当前问题。关键结论引用路径和行号，区分源码、需求和业务约定。"
                "说明哪部分已经确认，哪部分还缺原文或连接依据。只有检索记录时，说明实际查过的范围和结果。"
                "历史对话用于理解追问。"
                "根据题目分别说明接口数据、展示映射和页面使用情况，改动建议写明需要核实的前提。"
            )
            if coordinator and coordinator.material:
                rules += "\n" + MATERIAL_RULES
            if self.answer_mode == "facts":
                outgoing = request_messages(question, catalog, history, investigation=handoff)
            elif self.answer_mode == "contracts":
                from .contract_answer import request_messages as contract_messages

                outgoing = contract_messages(question, bundles, catalog, handoff)
            else:
                outgoing = [
                    SystemMessage(content=rules),
                    HumanMessage(
                        content=f"对话背景：\n{history}\n\n当前问题：{question}\n\n{handoff}\n\n已取得原文：\n{evidence or '尚无可用原文'}\n\n未交付原文块数：{omitted}"
                    ),
                ]
            if sum(len(str(m.content)) for m in outgoing) > limit:
                raise _Stopped("context")
            calls += 1
            entry = trace.begin("answer", outgoing, [], state_snapshot())
            try:
                message = await controlled(chat.ainvoke(outgoing))
            except BaseException as exc:
                trace.finish(entry, error=exc)
                raise
            trace.finish(entry, [message])
            record_usage(message)
            if self.answer_mode == "facts":
                emit("status", phase="checking", label="正在核对事实、引用和结论范围")
                final, verification = complete_response(message, catalog)
                verification.update(
                    {"omittedBlocks": omitted, "projectId": self.project_id,
                     "rawModelResponse": message.text}
                )
                messages.append(AIMessage(content=final))
                return
            if self.answer_mode == "contracts":
                from .contract_answer import complete_response as contract_response

                final, verification = contract_response(message, bundles, catalog, question=question)
                verification.update({"omittedBlocks": omitted, "projectId": self.project_id,
                                     "evidenceUnit": "merged_source", "conflictingSources": list(catalog.conflicts),
                                     "omittedContracts": omitted_contracts,
                                     "rawModelResponse": message.text})
                messages.append(AIMessage(content=final))
                return
            if message.tool_calls or not message.text:
                raise RuntimeErrorBase("无工具答题阶段未返回有效答案，详情见调用轨迹。")
            final = message.text
            messages.append(message)

        async def collect():
            nonlocal final, stop, discarded_draft
            iterator = agent.astream(
                {"messages": messages},
                stream_mode="updates",
                # LangGraph compares the step counter with this numeric bound;
                # infinity disables its otherwise finite default recursion cap.
                config={"recursion_limit": self.max_calls * 3 + 4 if self.max_calls is not None else float("inf")},
            ).__aiter__()
            while True:
                try:
                    update = await controlled(iterator.__anext__())
                except StopAsyncIteration:
                    break
                for node in update.values():
                    for message in (
                        node.get("messages", []) if isinstance(node, dict) else []
                    ):
                        if not isinstance(message, (AIMessage, ToolMessage)):
                            continue
                        messages.append(message)
                        if isinstance(message, AIMessage):
                            record_usage(message)
                            for call in message.tool_calls:
                                emit(
                                    "tool",
                                    id=call["id"],
                                    name=call["name"],
                                    input=call["args"],
                                    status="running",
                                    origin="workflow" if call["id"].startswith("workflow-") else "model",
                                )
                            if not message.tool_calls:
                                investigation.observe(messages)
                                pending = False
                                if coordinator:
                                    coordinator.observe(messages)
                                    coordinator.reconcile_material(investigation)
                                    decisions()
                                    pending = any(a["key"] not in coordinator.done for a in coordinator.actions)
                                open_critical = investigation.has_open_critical()
                                material_gap = bool(coordinator and coordinator.missing_material_evidence())
                                if pending or open_critical or material_gap or self.answer_mode != "plain":
                                    messages.pop()
                                    discarded_draft = True
                                    if pending and (self.max_calls is None or calls < self.max_calls - 1):
                                        investigation.reopened_drafts += 1
                                        emit("retrieval", action="reopen", label="新证据产生待执行检索，继续补查", state=coordinator.snapshot())
                                        raise _Stopped("reopen")
                                    if material_gap and not pending:
                                        emit("retrieval", action="finalize", label="按已核对的材料证据和未确认环节作答")
                                    elif open_critical and not pending:
                                        emit("retrieval", action="finalize", label="关键待办已无具体补查，保留证据缺口并作答")
                                    raise _Stopped("finalize")
                                final = (
                                    message.text
                                    if isinstance(message.text, str)
                                    else str(message.content)
                                )
                        else:
                            emit_tool_result(message)

        async def execute():
            while True:
                try:
                    await collect()
                    return
                except _Stopped as exc:
                    if str(exc) == "reopen":
                        continue
                    if str(exc) == "finalize":
                        await compose()
                        return
                    raise

        try:
            asyncio.run(execute())
        except _Stopped as exc:
            stop = str(exc)
        if stop:
            final = {"cancelled": "调查已停止。",
                     "context": "当前上下文容量不足，已有原文和调用轨迹已保存。可缩小问题范围后继续调查。",
                     "budget": "本次运行达到显式指定的调查限制，已有原文和调用轨迹已保存。"}[stop]
        if not final:
            raise RuntimeErrorBase("模型没有返回最终答案")
        if not stop:
            final = investigation.qualify(final)
            if coordinator:
                final = coordinator.qualify(final)
            if isinstance(messages[-1], AIMessage) and not messages[-1].tool_calls:
                messages[-1] = messages[-1].model_copy(update={"content": final})
        # Only complete tool exchanges can be reused on the next turn.
        if not stop:
            store.save(session, scope, messages)
        emit("text", id="answer", text=final, mode="replace")
        emit(
            "status",
            phase="cancelled" if stop == "cancelled" else "completed",
            label="已停止" if stop else "已完成",
        )
        trace.complete("cancelled" if stop == "cancelled" else "completed", finalAnswer=final, stopReason=stop,
                       investigation=investigation.snapshot(), contracts=contracts.snapshot() if contracts else None,
                       retrievalPlan=coordinator.snapshot() if coordinator else None)
        return RuntimeResult(
            final,
            session,
            events,
            {
                **token_usage,
                "reportedModels": sorted(reported),
                "requestedModel": self.environment.get("BUSINESS_CODE_MODEL_NAME"),
                "modelCalls": calls,
                "agentVersion": _AGENT_VERSION,
                "answerMode": self.answer_mode,
                "workflowMode": self.workflow_mode,
                "verification": verification,
                "discardedUnverifiedDraft": discarded_draft,
                "separateFinalization": finalization,
                "retrieval": "hybrid" if any(t.name == "SearchCode" for t in tools) else "files",
                "investigation": investigation.snapshot(),
                "contracts": contracts.snapshot() if contracts else None,
                "retrievalPlan": coordinator.snapshot() if coordinator else None,
                "tracePath": str(trace.path),
                "stopReason": stop,
                "durationSeconds": round(time.monotonic() - started, 3),
            },
            status="cancelled" if stop == "cancelled" else "completed",
        )
