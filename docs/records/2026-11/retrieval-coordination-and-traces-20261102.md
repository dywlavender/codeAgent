# 检索调度与调用轨迹（2026-11-02）

用户确认实现按需检索调度，并保存全部公开调用轨迹、提供页面查看入口。本次完成程序调度、工程内持久归档、只读 API 和问答页抽屉；未追加外部模型调用，也未验证新的业务答题收益。

## 实现

- 主问答默认 `WORKFLOW=coordinated`，保留 basic/tracked 对照；默认答案仍为 plain。auto 仅复用已准备的工程混合／契约索引，files/off 仍有效，不隐式准备索引或生成 AST。
- 明确文件直接读取，标识用 Grep，业务问题用混合检索与契约候选定位。明确接口／符号优先对索引做精确定位，不依赖词项候选排名；同路由同方法有多个目标时保留候选。多个明确接口分别保留目标。
- 程序根据工具结果创建和更新检索事项，选定入口后按需核对连接、返回字段、状态分支与对应系统前端。新到达的证据可以生成新动作，结构缺口进入源码补读；完整当前原文可复用，失效／截断资料继续定位或读取。
- 自动动作最多 24 个、每批最多 3 个独立动作、自动入口最多 3 个。超预算事项保留为缺口。已读原文、结构证据已交付与语义证明分开记录；答题上下文丢失原文时重新标出缺口。
- 既有 `evaluate_langchain.py` 增加 `--workflow-mode coordinated`，保留其 basic 历史对照默认值，便于在相同工具、模型与预算下比较调度。
- 运行时保存公开模型输入／输出、工具定义、用量、耗时、完整工具原文、调用来源、调度原因、调查状态、错误与停止原因。供应商序列化前的框架快照不包含认证配置或模型私有思考，不称为 HTTP 抓包。
- QueryService 将记录独立归档到工程数据目录 `query-traces/<runId>.json`，工作区刷新后仍可回看。失败、取消和零模型调用也保留运行记录；旧数据优先复用已有工作区快照，缺失记录不补造。
- API 为 `GET /api/projects/<projectId>/query/<runId>/trace`。问答页每轮“查看调用轨迹”可查看模型、工具、调度、调查事项和完整记录，支持源码原文展开与 JSON 导出。运行中轮询，关闭或切换工程取消旧请求。

## 本地验证

后端相关回归 114 项通过：

```sh
.venv/bin/python -m unittest tests.test_langchain_agent tests.test_retrieval_coordinator tests.test_investigation_workflow tests.test_contract_index tests.test_call_chain_graph tests.test_query_traces tests.test_langchain_http tests.test_query_api tests.test_project_context tests.test_query_runtime_service tests.test_answer_verification tests.test_answer_replay tests.test_hybrid_retrieval tests.test_query_progress tests.test_agent_workspace
```

覆盖直接读文件、精确接口不依赖候选排序、多接口目标、调查中新增契约后自动补查、索引过期补读且不解析、字段清单不展开业务分支、原文复用与上下文丢失、取消中的请求状态、归档保留、旧轨迹兼容和跨工程隔离。兼容模型传输用本机 HTTP 模拟端点，未连接外部模型。

前端 16 项测试及生产构建通过。构建保留现有的大资源包提示。

实际页面由 `scripts/evaluation/preview_retrieval_trace.py` 用本地模拟模型产生独立成功／失败运行，读取 complex-loan 已准备的契约索引，原工程数据库未写入。页面使用 `scripts/evaluation/verify_retrieval_trace_ui.js` 验证：

1. 模型请求实际包含 TraceResponseFields、TraceBusinessRules 和 HTTP_RESPONSE 原文。
2. 工具记录可查看完整输入输出并展开带行号的源码。
3. 模型／工具／调度筛选、调查事项与完整记录可打开。
4. JSON 实际下载成功；下载文件的 calls、runtimeEvents、runId 与磁盘归档一致。
5. 失败的模型请求及新提交但模型禁用的零调用失败运行均可回看。
6. 验证过程中无浏览器运行错误。

成功样本：`RUN-9df20f68230840c5b93f65c958bf5305`。本地模拟模型请求 1 次、程序工具调用 2 次；字段工具交付 15 个解析支持字段和 21 段原文，业务工具交付 14 个局部规则和 9 段原文，模型消息正文 38,040 字符。实际导出 330,969 字节。这些数字证明记录与调度路径可用，不表示证据充分、业务答案正确或生产延迟收益。

截图与导出位于本机 `output/playwright/retrieval-trace-{model,tool,failure}.png` 和 `retrieval-trace-export.json`。最终本机预览为 `http://127.0.0.1:18084/?projectId=complex-loan-validation#/agent`，使用模拟样本且禁用外部模型；不属于部署或真实模型质量验证。

## 待验证收益

以相同模型、资料范围和预算比较文件基线、同工具自由调用、同工具加程序调度，覆盖开发题与保留题。分别统计关键连接遗漏、字段误判、无依据断言、工具调用、耗时和用量。当前启发式问题目标、有限契约解析与 plain 收尾都不能保证完整业务语义正确。
