# 检索诊断约定

## 作用与边界

检索诊断分别检查候选召回、最终上下文与固定证据下的回答，减少对最终答题模型能力的依赖。它是现有问答评测旁的独立能力，不接管生产 Agent 的调查。资料边界与手动 AST 约定遵循[指导方向](knowledge-backbone-direction.md)第 11、12 节。

原诊断提供 BM25 基线、源码窗口与已有方法位置对照、外部候选导入、上下文选择和固定证据答题。2026-10-31 另增 `codeintel/hybrid/` 与独立混合检索脚本，支持显式结构索引、本地向量、BM25 与 Cross-Encoder；它们没有实现完整调用图，不能将检索覆盖解释成 Agent 的完整调查效果。

## 工程和冻结输入

契约实验位于 `codeintel/contracts/`，由 `scripts/evaluation/prepare_contract_index.py` 显式准备工程内 SQLite，查询只读。当前覆盖 Spring Controller、RestClient 的默认配置绑定、方法返回委托、字面量 `Map.of`、`List.of` 和普通 record 构造，以及受支持的字符串 switch。方法变更按文件复用未变结构，显式更新保存 Git diff 行范围；源码变化、同名歧义、动态表达式和未授权仓库保留为缺口。它不是完整 AST／控制流、事件图或运行时链路。

模型实验使用 `evaluate_langchain.py --contract-root <工程数据目录>/contract-index`，不由脚本生成索引。分别用 `--answer-mode plain` 测“文件工具加契约检索”，再用 `--answer-mode contracts` 测结构事实收尾，`--workflow-mode basic` 即可接受由真实关系缺口产生的程序补读，无需模型创建 Investigation。正常资料不删减，不向索引器或 Agent 提供参考答案。

`contracts` 收尾由程序生成字段与展示映射，模型引用实体成员写入分支解释业务，并提出条件化改动选项。新增的写入点保留局部 if 与 try/catch、setter 绑定、源码和未完整解析标记；不是完整状态机或执行证明。程序自动补查由问题中的显式词触发，不能以词项规则宣称完整问题理解。最终原文按文件／行号合并，来源丢弃和冲突独立记录。

避免字段误判不等于完整业务问答通过。分别复核连接／字段准确性、必要业务条件、正常与异常是否混淆、页面证据与改动前提是否回答。开发回归题与独立保留题分开；历史基线只作诊断参考，正式收益仍需同题、同模型和同预算的新配对。工具减少、索引条数和程序格式通过均不算整体问答收益。

每个批次归属于 `ProjectContext`，位于工程的 `evaluations/retrieval/<批次>/`；标注题库在工程的 `evaluation-suites/retrieval/`。注册工程和兼容工程都从显式配置取得仓库与资料，不按案例名称猜测源码。

兼容工程与正式问答共用 `WorkspaceManager` 解析后的需求目录；注册工程使用 `ProjectContext` 的资料目录。配置中正常提供的需求不能在冻结语料时遗漏，也不能通过删减正常资料放大业务补充知识的收益。

`protocol.json` 保存题库、原文引文、解析后的行号、来源清单和标注状态；`corpus.json` 冻结实际原文。检索只接收问题与冻结原文，不读取标注中的参考答案或证明路径。后续对照和固定证据答题重用该批冻结输入，不重新读取当前源码。

准备只遍历已有资料，不生成 AST、调用模型或触发仓库同步。排除的文件及原因写入协议；若标注引用的原文缺失或不唯一，阻止该批评分。不会默默用当前目录下的其他版本补齐。

方法位置必须通过显式操作，从本工程已经存在的代码索引只读导出到 `structures/<版本>.json`，并保存小型 `.meta.json` 供页面列举。当前只使用 Java 方法：核对仓库目录、源码修改时间不晚于索引时间、当前文件与冻结原文一致、声明及结束位置匹配。未通过的条目记录原因；不重新索引，不调用解析器，不生成 AST。它是机械定位资料，不能解释成编译器确认的类型关系或业务证据。后续评测只读取指定的已保存版本，版本必须同时属于当前工程与冻结批次；页面加载、读取状态和启动评测都不会自动保存位置。

## 证据标注

题库必须提供 `id`、`projectId`、标注来源、复核状态和 `cases`。每题包括问题、`development` 或 `holdout` 集合，以及必要证据组。

每组包括 `id`、`role` 和 `alternatives`。角色可为实现、条件、结果、业务语义、关系或导航。每个证明路径是一组同时需要的原文位置；不同路径之间满足任一即可。位置包括 `sourceId`、仓库相对 `path`、原文 `quote`，重复引文需额外指定 `lineStart`。关系组应标注两端及连接依据。

`sourceId` 使用配置中的仓库编号；业务补充知识为 `business-context`，需求为 `requirements`。示例见 `validation-projects/complex-loan/evaluations/retrieval-evidence-v2.json`。这套 20 题来自现有已知回归题，标注由助手核对源码整理，仍为草稿，未冒充人工复核。

## 检索与结果

- `lexical` 使用中文双字词及标识符词项、BM25 排序，源码窗口最多 70 行；Markdown 保留章节边界。
- `lexical_compact` 将源码窗口改为最多 30 行，其他逻辑一致；是否有效由同题结果判断，不预设越短越好。
- `lexical_methods` 使用已保存的方法边界组织源码，保留直接相邻的说明和分隔空行；方法之外的字段、配置、其他语言及没有可用定位的文件仍使用原文本能力，正常资料不删减。方法位置版本随结果保存。
- 候选默认 40 项；上下文预算默认 4,000、12,000 字符，包含位置头与原文，按完整行截取并去重。字符数不等同模型 token 数。
- 每个策略保存候选、选入上下文的片段、原文上下文和逐证据组的覆盖情况。全部已标注证据覆盖仍不等于上下文充分，充分性保留为待复核。
- 未标注的上下文不能自动判为无关；本版不输出伪精确的无关率。

上下文选择保留四个独立策略：`ranked` 是原顺序，仍为默认；`diverse` 在原相关性与候选文本重叠之间排序；`focused` 在候选已有原文内按空行分段，结合本题词项、原候选分数与段落成本选择，再合并已选的相邻原文；`whole` 按原顺序选择能完整装入预算的候选，不截取单元前缀，装不下时继续尝试后续单元。后三项仅用于实验。段落不等于完整方法；整段保留也不保证材料充分，超过整题预算的候选数量单独记录。

`diverse` 第一版使用 0.7 的相关性权重与 0.3 的最大文本余弦重叠惩罚；词项向量仅由候选原文构成。`focused` 当前版本为 2：本题段落匹配与原候选分数各占 0.5，按含定位成本的长度平方根归一化，再进行多样性排序与预算选择；空行保留为原始位置的一部分。策略编号、检索策略、选择方式与实现版本分别记录，不能只凭显示名称合并不同版本。

对照可以通过 `candidateSource` 复用同一批次已完成策略中的候选，重新从冻结原文核对位置、正文、顺序和原分数，不重跑全语料召回，不改变原候选数量设置。每个选择结果都核对其行号和正文属于候选原文，不能用行号覆盖代替实际原文交付。`retrievalSeconds` 保留候选来源的原检索耗时，`candidateReplaySeconds` 单列本次读取候选耗时，不能将复用耗时当作新的检索性能。

同预算下保存逐题 `comparison`，包括候选是否相同、原覆盖、新旧覆盖差、新增标注组和丢失标注组。即使平均覆盖相同，也须展示证据交换；覆盖改善不自动认定材料充分。来源策略的案例结果不覆盖，新策略使用独立编号；汇总报告追加新对照。

`comparisonSource` 可以指定本批次另一项同题同预算结果，允许比较不同候选，但必须明确显示候选变化。新增 `candidateCount`、`candidateChars` 和 `candidateBudgetChars` 区分候选数量与原文规模。方法单元变小后，同为 40 项并不等于相同候选输入量；可按对照策略逐题的候选原文与定位头总字符数，限制新候选的完整排名前缀。这个预算按各片段累加，含重复行，既不是最终去重上下文大小，也不是模型 token 或实际成本。上限 500 项只作检索数量边界，实际数量由字符预算决定。预算来源只读取旧候选大小，不读取证据标注。

外部检索器可提交 `{projectId, snapshotId, cases: {caseId: [位置...]}}`。位置包括 `sourceId`、`path`、`lineStart`、`lineEnd`，顺序表示排序。评测器重新从冻结资料提取原文，不相信导入结果自带的正文。工程或冻结版本不符会拒绝。自定义策略使用独立名称，已有策略结果不覆盖。外部检索器可用 `timings[caseId]` 提供实际 `retrievalSeconds` 与 `usage`；未提供时记为未知，不将本地装载时间伪装成外部检索成本。

## 固定证据与模型对照

只向模型提供所选问题和已保存的上下文，关闭全部工具、MCP、会话续用、项目和用户指令来源。默认使用 Claude CLI 时，保留本机模型连接所需的认证、地址和模型别名环境配置，不继承用户提示或 hooks，不在结果中保存认证值。每次使用源码目录和批次之外的空临时工作区。

若进程从 `.env` 加载 `BUSINESS_CODE_MODEL_ENABLED=true` 且 `BUSINESS_CODE_MODEL_PROVIDER=openai`，固定证据实验改用该配置的兼容接口。`BASE_URL`、`NAME`、`API_KEY`、`TEMPERATURE` 和 `TIMEOUT` 分别来自相同前缀的变量。请求只有固定系统规则与本题原文两条消息，不提供工具和历史。这里的 `openai` 表示兼容协议，不代表模型由 OpenAI 提供。

保存请求模型、解析模型、实际报告名称、原始用量、耗时和结果。`configured-default` 使用当前连接配置；指定另一个模型后，仍须核对实际报告名称，不能只凭请求别名认定成功切换了模型。失败调用保留失败状态，不记零分。Claude CLI 每次调用默认支出上限 $0.5；兼容接口限制最多输出 4096 token，不提供金额硬限，不能显示为具备美元支出保证。

命令行与标准服务启动时加载 `.env`。修改配置后需要重新启动进程；固定证据页面显示其加载的模型。生产主问答现使用独立的 LangChain 1.2 适配器读取同一组连接配置，固定证据与允许工具的 Agent 验证仍分别记录。

固定上下文实验用于诊断证据使用。只有完成多模型、相同证据和实际质量复核后，才能讨论模型是否掩盖检索差异。产品 Agent 允许继续搜索的预算对照属于后续能力，当前未新增自动大规模模型矩阵。

## 使用入口

页面：效果验证 → 检索诊断。可导入 JSON 标注、运行基准、查看逐题上下文，并明确启动固定证据答题。读取公开，导入和启动复用项目管理员认证。

页面的“复用候选对照”默认选择本批次原词项候选（没有原词项策略时取已有首项），也可手动选择候选来源；默认只比较开发题与 12,000 字符预算。本地完成后更新结果，不调用答题模型。表格分别显示检索策略、上下文选择和覆盖变化，案例详情展示新增与丢失的标注。它不会更改生产默认策略。

方法对照的操作顺序：先点“保存已有方法位置”，再选择该版本，点“对照方法单元”。“匹配旧候选原文预算”默认选中，关闭时使用固定 40 项；两者都保留 12,000 字符最终预算和开发题范围。完成后该结果自动成为候选来源，可选择“整段保留”并点击“复用候选对照”，单独比较组装方式。候选来源也可手动选择已有策略。详情显示实际候选数、候选字符预算和引用的方法位置版本。

规范 API：

```text
GET/POST /api/projects/<projectId>/retrieval-suites
GET/POST /api/projects/<projectId>/retrieval-evaluations
GET      /api/projects/<projectId>/retrieval-evaluations/<id>
GET      /api/projects/<projectId>/retrieval-evaluations/<id>/cases/<caseId>?strategy=lexical&budget=12000
POST     /api/projects/<projectId>/retrieval-evaluations/<id>/answers
POST     /api/projects/<projectId>/retrieval-evaluations/<id>/context-comparisons
POST     /api/projects/<projectId>/retrieval-evaluations/<id>/structure-snapshots
POST     /api/projects/<projectId>/retrieval-evaluations/<id>/method-comparisons
```

本机命令示例，从项目根目录执行：

```bash
.venv/bin/python scripts/evaluation/evaluate_retrieval.py \
  --project-config validation-projects/complex-loan/project.config.json \
  --suite validation-projects/complex-loan/evaluations/retrieval-evidence-v2.json

.venv/bin/python scripts/evaluation/evaluate_retrieval.py \
  --project-config validation-projects/complex-loan/project.config.json \
  --batch <已有批次目录> --answer v2-channel-display \
  --context-budget 12000 --model configured-default
```

注册工程改用 `--project-registry <平台目录> --project-id <工程>`。`--prepare-only` 只冻结资料；`--candidates <文件> --strategy <独立名称>` 在相同批次评估其他检索器；`--split development` 可只运行开发题。不同策略的同预算、同集合结果才可比较。

复用已有候选、只比较上下文选择：

```bash
.venv/bin/python scripts/evaluation/evaluate_retrieval.py \
  --project-config validation-projects/complex-loan/project.config.json \
  --batch <已有批次目录> --strategy context-focused \
  --reuse-candidates lexical --context-policy focused \
  --budgets 12000 --split development
```

每次使用未占用的 `--strategy` 编号。API 的上下文对照请求接受 `sourceStrategy`、`contextPolicy`、`budgets` 和 `split`，管理员验证后在本地同步执行并返回独立策略结果。

保存已有索引的方法位置，再与原窗口检索配对比较：

```bash
.venv/bin/python scripts/evaluation/evaluate_retrieval.py \
  --project-config validation-projects/complex-loan/project.config.json \
  --batch <已有批次目录> --freeze-structure

.venv/bin/python scripts/evaluation/evaluate_retrieval.py \
  --project-config validation-projects/complex-loan/project.config.json \
  --batch <已有批次目录> --strategy methods-matched \
  --retrieval-unit methods --structure-version <上一步返回的版本> \
  --compare-to lexical --match-candidate-budget --candidate-limit 500 \
  --context-policy whole --budgets 12000 --split development
```

关闭原文规模匹配时，去掉 `--match-candidate-budget` 并使用 `--candidate-limit 40`。两种预算方式应分别报告，不混成同一策略。API 的方法对照接受 `structureVersionId`、`contextPolicy`、`compareTo`、`matchCandidateBudget`、`candidateLimit`、`budgets` 和 `split`；缺少已保存版本时拒绝，不自动补建。

## 实施进度与后续验收

2026-10-31 用户新增方向已实现为独立 `query/langchain_agent/` 主问答运行时和 `codeintel/hybrid/` 检索包；原三组历史评测保留。显式准备：`python scripts/evaluation/prepare_hybrid_index.py --project-config <配置> --download-models`，再次执行不带下载参数可增量更新。主问答显式启用 hybrid 且索引已存在时才增加 SearchCode，不自动解析或更新结构索引。

2026-10-31 实测尚未通过默认启用检索增强的验收：12 道开发题最终上下文覆盖约 39.6%，3 题改善、2 题退化；所选本地 Cross-Encoder 在 200ms 下全部回退。放宽预算的三题完整精排约 1.07–1.16 秒，未显示质量改善。修复后的真实 Qwen 单题对照两组均读到该题全部 3 个已标注证据组，但仍混淆展示状态与接口实际返回能力。已标注覆盖不等于充分性或正确性；本次为非思考、每版最多 8 次调用、最多 2048 输出 token 的已知回归，不能泛化为全部模型或生产问题。当时默认保留文件工具、混合检索显式启用。2026-11-02 用户确认的程序调度与已有索引复用见指导方向第 13.3 节，不改变这批实验的收益结论。证据及限制见[迁移与混合检索实测记录](../records/2026-10/langchain-hybrid-validation-20261031.md)。

`evaluate_langchain.py` 通过 QueryService 对文件工具与混合检索分别执行同题 Agent 调查，使用 `--answer-mode plain|facts` 指定答题模式、`--workflow-mode basic|tracked` 指定工作流，记录请求模型、思考配置、工具轨迹、累计用量和停止原因。逐轮公开快照由 `--trace-output` 保存，默认结果同名的 `.trace.json`，临时工作区清理前复制；输入工具定义、系统消息、调查状态和输出均保留，不含认证信息或私有思考。`evaluate_hybrid.py` 先逐字核对索引与冻结语料，再仅评开发题；融合候选固定 Top-50、上下文最多 12,000 字符／300 行、每单元最多 100 行。BM25／向量排名融合权重为 0.6／0.4。精排预算 200ms，超时不采用精排结果并记录回退；模型装载与预热不混入稳态精排时间。候选数量和新行数约束不同于旧 Top-40 对照，整套方案的变化不能归因于某一个组件。

可选 facts 答题模式单独验证，尚未通过默认启用验收，默认保留 plain。`scripts/evaluation/evaluate_answer_consistency.py prepare` 从旧运行的完整工具事件重建其实际交付原文，冻结原问题与原文，不读取参考标注或旧答案；`run` 仅调用一次无工具模型，记录原始结构输出、程序最终答案、引用／层次检查及用量，不自动重试或覆盖旧记录。必须指定工程 ID；原运行、题库和冻结输入归属不一致时拒绝。当前此检查不证明任意业务语义、嵌套字段绑定或业务链路完整性；格式通过、字段分段匹配、已标注覆盖都不能代替独立答案复核。固定原文重放与允许继续搜索的完整 Agent 分开报告，不能把本地重渲染旧模型输出算成新增真实复验。实测见[事实分层复验](../records/2026-10/answer-facts-validation-20261031.md)。

轨迹诊断需区分工具行为、证据交付、调用关系和最终结论，不能仅凭读取覆盖或最终标题解释原因。接口 JSON、显式业务原因与页面体验的问法有歧义时，按具体断言复核，不只凭“能／不能直接支持”判错；旧答案和复核记录保留。关系项分别记录端点和连接，当前 3／3 标注组不覆盖完整 HTTP 调用与页面消费。递归 Glob、Grep 大小写、职责导航和搜索状态交接已修复或接入；此前 tracked 真实回归仍有字段断言错误与待办未更新。当时默认 basic，模型未登记的关系缺口不能自动补齐；新的 coordinated 根据目标及已选入口安排补查，仍不能自动发现所有业务缺口。定位证据见[逐轮轨迹复盘](../records/2026-10/agent-trace-review-20261031.md)，修改与验证见[本轮优化记录](../records/2026-10/agent-optimization-20261031.md)。

以下是第一阶段诊断之后的实施顺序。2026-10-30 已实现同候选对照、逐题新增与丢失记录，以及两类上下文选择实验；2026-10-31 已实现显式复用已有方法位置、方法单元、整段保留和候选原文规模匹配。它们尚未接入生产；引用／调用导航、真实题库与模型验收仍待实施。简单排序与空行分段没有总体收益；方法单元仅有开发题上的局部收益，仍存在关键退化，因此保持原顺序默认值。实施证据见[上下文选择对照记录](../records/2026-10/retrieval-context-selection-20261030.md)和[方法单元对照记录](../records/2026-10/retrieval-method-units-20261031.md)。

### 1. 用三道开发题定位不同缺口

第一轮使用修正需求目录后的冻结批次，不扩大演示工程，不以新增题量代替已知问题的修复：

| 开发案例 | 当前观测 | 本轮检查 |
| --- | --- | --- |
| `v2-callback-twice`：重复成功回调 | 候选标注覆盖 100%，上下文 50% | 补偿入口与额度流水为何在组装时丢失 |
| `v2-channel-display`：渠道显示处理中 | 候选覆盖约 66.7%，上下文约 33.3% | 渠道查询实现未召回、中台入账字段未选入，分别处理 |
| `biz-repaid-limit`：已还款但额度未恢复 | 候选与上下文均为 50% | 从还款分配到额度恢复的连接依据为何未找到 |

先复核这三题的必要原文：每个关键判断标明成立条件、行为、结果或关系两端；允许源码、需求和业务补充资料中的等价证明路径。移除非必要的大段标注不能漏掉会改变结论的分支。发现标注问题时发布新题库版本并重评，旧批次保留。自动精确位置检查不等于人工业务复核。

### 2. 先只改上下文选择

先固定原候选位置与顺序、源码快照、问题、40 个候选及 12,000 字符预算，比较原组装和新组装，不同时更换召回器或模型。

代码落点为 `codeintel/retrieval.py` 的 `pack_context` 和 `evaluation/retrieval.py` 的策略记录。保留原组装为默认对照，为实验增加独立策略；分别记录检索策略和组装策略。

本项已比较相关性与文本重叠排序，以及候选内部的段落选择、相邻原文合并。当前已有逐行精确去重，不能把原去重能力重复算作收益。相似度和选择只能使用问题、候选原文及正常元数据，不能读取证据标注或参考答案。当前结果支持保留旧策略，尚不支持以简单去重复或空行分段代替方法、条件与结果的完整性。

此步只选择现有候选，不读取候选之外的资料，不自动生成结构资料。它不能解决候选未召回的问题，也不能保证被选中的固定窗口已经包含完整方法。验收逐题报告候选与上下文的差异，确认重复回调题的关键衔接是否保留，并复算全部开发题，检查新增遗漏。覆盖提高后仍需人工检查材料能否支持结论。

### 3. 再改检索单元与关系展开

方法位置与单元对照已实现；关系展开仍是后续工作。当前按旧候选原文规模匹配时，上下文标注覆盖从 34.7% 到 45.1%，但候选覆盖从 72.2% 到 61.1%，重复回调题仍退化。这仅支持继续检验方法完整性与证据选择，不能认定已修复跨系统调查，也不能替换默认策略。

完成上下文选择对照后，再分别比较以下改动，避免一次混改后无法归因：

- 源码检索单元改为方法、声明或配置段，并保留必要的前置判断和结果；大方法保留范围与继续读取位置。业务资料以语义条目或章节为单位。
- 优先试接现有 `EvidenceTools.search_code`、`read_source`、`find_references`、`follow_call` 和 `follow_integration`，补充工程、仓库范围和资料版本校验。名称匹配的引用与调用保留候选性质，不能冒充类型解析后的精确关系。
- 对候选未找到关键端点的题，按模型已经发现的符号或协议线索继续检索；调用者、被调用者和 HTTP 两端的原文分别读取。结构图只用于导航，不能替代实现证据。

方法边界和关系必须来自已有且与源码一致的结构资料，并与实验原文一起冻结。结构资料的准备或更新由用户显式触发；问答、页面加载和评测不得隐式生成 AST。没有可用结构资料时保留文本能力并明确限制。

自然语言与代码命名不一致仍导致入口遗漏时，再单独比较项目术语扩展或语义召回。每种新增能力分别计入准备、查询、阅读和模型用量。

### 4. 业务资料与题库同步改进

从真实错答记录中登记源码、README 和正常需求仍无法回答的语义缺口；条目记录业务说法、含义、适用范围、例外、依据和核对日期。不能从某一题的参考答案反向编写业务资料，不能虚构项目规则或别名来制造收益。已有资料即使没有独占事实，也可以检验是否减少搜索和阅读成本，不能只按新增事实数判断全部价值。

现有 20 题保留为已知回归。另收集真实使用问题，按业务规则或故障场景分组后划分开发与新保留题；同规则的数字变化或改写不拆到两组。没有真实问题时保留待补状态，不将助手构造题标为生产反馈。新保留题不参与参数选择；已看过的原保留题只能报告回归结果。

### 5. 通过固定证据与正式 Agent 两层验收

先用相同模型分别回答系统选出的原文和人工核对的必要原文，关闭继续搜索。原文缺失对应资料问题；资料存在但未交付对应召回或选择问题；原文已经足够却仍答错，继续核查证据使用、题目条件和评分依据。人工选择原文只用于诊断，不能作为检索器输入，也不能冒充系统的自动能力。

模型接口先完成连接与短请求验证，再进行代码原文实验；记录实际报告模型与失败。完成同模型对照后，再复核第二个模型，不先假定模型能力档位。

检索收益应在开发题上改善、经新保留题复验，并在允许继续搜索的正式 Agent 中减少关键错误或在同等质量下降低成本。2026-11-02 用户确认默认程序调度按需复用已有工具，这是执行策略变更，不表示已经满足收益验收。生产接入位于 `query/langchain_agent/runtime.py` 与 `coordinator.py`，资料版本与会话重用由 `query/service.py` 管理；不把全部检索步骤写成每题固定流程。后续需同模型、同预算比较文件基线、同工具自由调查与程序调度，保留 basic/files/off 对照。

## 历史结构化检索对照（2026-11-13，阶段 0 验收标准）

本节保留旧 LangChain 实验的设置与指标。新的 AST 结构索引对照使用 `scripts/evaluation/evaluate_pi.py run --modes none,ast`。AST 组使用手动生成的可用版本，报告记录每轮实际装配的版本。见[阶段 6](agent-migration-plan.md#阶段-6资料中心与代码结构可视化)。

在改图查询、向量或收敛路由之前，先用固定对照验证“结构化精确检索”相对“Agent 自读 AST md”的真实收益。对照挂在真实 LangChain Agent 上（`scripts/evaluation/evaluate_langchain.py`），两侧资料完全一致、同一模型、同一预算，唯一差异是实验臂多一个绑定冻结 AST `index.db` 的 `SearchSymbols` 工具（FTS5 精确符号检索，返回 fqn 和 file:line 定位线索，结论仍需 Read 核实）。

- 基线臂：`--mode combined`，Agent 读挂载的 `knowledge/ast/` md 自行导航。
- 实验臂：`--mode combined --code-index-db <AST 版本>/index.db`，同题、同资料，另有 SearchSymbols。
- 运行前确认工程 AST `currentVersionId` 与 `--code-index-db` 指向同一版本，md 与索引库才会对应同一份冻结源码。

四个指标：引用有效性（回答中 `file:line` 引用在冻结源码内真实存在且行号在范围内，程序校验，见 `evaluation/code_search_metrics.citation_report`）、token 消耗与模型轮次（usage，配对差值）、`no_results` 声明一致性（回答声明“未找到”与该轮检索观察并列记录，正确性由评审复核）、资料外补充（评审判断）。配对汇总用 `code_search_comparison`，同题配对，缺臂的题报告为 unpaired，不作平均。

题集从现有题库挑选包含明确符号名或 HTTP 路径的开发题（如“`XxxService.method` 怎么处理幂等”），同题两臂各跑一次；题量与题目清单在评测记录中登记，沿用已知回归与开发题划分约定。验收门控：实验臂在精确符号题上 token 下降、引用有效性不劣于基线、且未出现“索引有边有符号但答未找到”的假阴性，才进入下一阶段（图查询）；任一指标退化则先修 fqn/FTS 字段设计，不叠加新能力。此对照是实验能力，不改变 `combined` 生产默认——生产侧是否挂载 SearchSymbols 待对照数据决定。
