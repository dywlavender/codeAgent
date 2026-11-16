# 项目协作指引

## Pi Runtime 统一问答主链（2026-11-15）

- 在线问答默认使用 `PiAgentRuntime`，正常 API 入口固定进入 Pi；`BUSINESS_CODE_AGENT_PROFILE`／`BUSINESS_CODE_AGENT_RUNTIME` 不能再把在线问答切回 LangChain/Direct/Claude。旧 Runtime 只允许评测代码显式构造并注入。`QueryService._query_pi()` 统一处理 `combined`／`none`／`backbone`／`ast`。Pi 负责模型、工具决策与调度、上下文压缩、会话和 Agent Loop；CodeAgent 只负责授权、RunContext、Trace、取消、持久化和业务问答增强。
- Pi 基础工具为 `read`、`grep`、`find`、`ls`、`codemode`，不开放 `bash`／`edit`／`write`。工作区仓库是软链接，Pi 自带搜索不跟随链接，因此加载只读扩展 `query/agent/pi/extension/codeagent_search.js`；扩展以授权真实根目录为搜索起点，不使用全局 `--follow`，结果路径映射回工作区，仓库内部软链接不会扩大授权范围。工作区 cwd 不是 OS 安全边界，不能把扩展当作完整沙箱。
- AST 由 `codeintel/ast/` 管理，用户手动生成后才能用于当前源码导航。Pi 在可用版本上按本轮仓库范围注册 `find_code_symbol`、`get_code_symbol`、`find_code_relations`。启动时确认 AST 扩展已加载，失败时记录为 `unavailable` 并使用文件工具继续。用 `read` 核实具体实现。全工程 Code Map 和 AST Markdown 只挂载到全仓问答，历史 AST 页面继续可读。见[阶段 3](docs/spec/agent-migration-plan.md#阶段-3ast-与结构导航)。
- Pi 的系统代码导航取自工程配置的应用位置和工程数据目录中的可选 System Guide。显式系统选择限定导航，直接选择仓库时加入其关联应用。共仓的源码权限仍以仓库为单位。只复制本轮文档，文档内容或应用映射变化时更换 Pi 会话。导航只用于定位，见[阶段 4](docs/spec/agent-migration-plan.md#阶段-4system-guide)。
- Business Context 通过工程知识页维护 Markdown。建议使用默认 Pi 模型进行一次无工具的定向检查，管理员确认后走原文保存接口。下一轮问答按资料模式读取发布的原文，结构化视图仍显式导入。见[阶段 5](docs/spec/agent-migration-plan.md#阶段-5business-context-维护)。
- Pi 会话文件限定在工程数据目录 `pi-sessions/`；工程、scope、Profile、资料模式/版本或 Pi 配置变化时重建。业务文档和需求使用内容指纹，源码仓库使用文件元数据修订号，资料变化会隔离旧会话；旧 LangChain 会话不直接复用。Pi 失败不回退 LangChain。配置见 `.env.example` 的 `BUSINESS_CODE_PI_*`。
- 在线模型选择来自 Pi 自身的 `get_available_models`（`GET /api/pi/models`），只返回安全展示字段；前端按工程保存选择，请求携带 `model`，留空使用 `BUSINESS_CODE_PI_MODEL`。2026-11-16 起问答默认模型为 `deepseek/gpt-6-luna`；换模型后必须新开评测批次，不能与 glm-5.3-flash 等旧批次混跑或续跑。模型进入会话身份，切换模型会重建 Pi 会话。
- Pi 最终答案的引用会按本轮实际交付的 `read` 原文核验路径与行区间，记录在 usage/Trace；`semanticProof=false`，核验通过不等于语义正确。空范围展开后没有仓库时，必须在调用模型前拒绝，不能回退为全部仓库。
- Pi 回答末尾的“目标覆盖”小节由模型逐条声明问题关系及其证据；`usage.investigation.goalCoverage` 只把模型标为 `[已核实]` 且该条所有带行号引用都被本轮 `read` 确认（至少一条）的目标计为 verified，缺少交付证据的声明降为 unverified；全部 verified 才把 `complete` 记为 true，未声明时为 null。该标记不是语义证明，也不能替代人工 checks；停止判断以未完成目标为依据，不以关键文件数量或工具次数阈值为准。
- Pi 真实问答评测使用 `scripts/evaluation/evaluate_pi.py`，直接调用生产 `QueryService` 与 `PiAgentRuntime`；题库 checks 只留在评测侧，答案、Trace、模型/资料版本和调查计数写入独立批次。搜索与未读候选只是代理指标，不是答案正确性评分。引用按独立源码引用去重（同一 path+line 只计一次），Markdown 链接只按目标或可匹配标签生成一条记录；grep 上下文行不得作为候选文件。逐项复核用 `review` 命令生成 `checks.md`，结论固定为 pass/partial/fail，并附答案原句与工程相对证据。该命令只校验复核结构、判定词、答案原句是否出现在 `answer.md`，以及证据是否为工程相对路径；它不打开证据文件或 Trace，不核对文件/行是否存在或内容是否支持判定，语义正确性仍需人工阅读源码确认。Pi 最终答案在输出前把临时绝对路径改为工程相对路径；Pi Trace 只保存模型调用摘要，不能标记为完整请求快照。
- 优先完成同模型、同题库、同源码的 Pi 质量基线，再按 Trace 区分未找到证据、已读但漏答与证据充足后的继续调查，用 none／backbone 验证业务知识增益。跨系统调查限同一工程授权范围。工程测试和单次实跑不能替代答案质量验收；Direct 实验不作为 Pi 的前置，旧 Runtime 删除另行确认。实现见[Pi Runtime 记录](docs/records/2026-11/pi-runtime-unification-20261115.md)。

## Direct 评测：上下文管理（2026-11-15）

- 以下机制只在显式 Direct 评测中使用。Token 预算包含系统提示、工具定义、请求内容和输出预留，沿用 `BUSINESS_CODE_AGENT_CONTEXT_CHARS` 作为字符上限。
- `BUSINESS_CODE_AGENT_CONTEXT_TOKENS` 显式启用同一循环内的上下文压缩。保留原问题和最近完整工具批次，优先收缩旧搜索的大结果。摘要调用计入调查耗时、模型调用和 token。预算紧张时可直接收缩上下文。
- 调查摘要保留结论线索、未确认问题与已读路径。ReadLedger 在本轮恢复原文；最终引用检查依据交给答题模型的原文。跨轮复用摘要前核对会话范围和源文件版本。工具范围与结束决定仍由现有 Profile 和 Direct 循环负责。

## Direct 评测：调查证据与收尾（2026-11-15）

- Question Contract v3 冻结。Direct 使用它保存原问题和模型登记的目标，逐字引文用于关联，分句与词面规则不参与收尾判定。
- Evidence Ledger 关联用户目标、模型结论、实际交付的 Read 原文及必要关系。必要连接有单独的源码依据，两端文件的存在不足以确认连接。ReadLedger 继续负责已读区间及原文恢复。
- Answer Closure 在同一 Direct 循环内核对资料类型、引用区间和已登记关系。提交若混用有效与无效引用，可向原模型退回一次，让它拆出有依据的结论和缺口。`BUSINESS_CODE_AGENT_ANSWER_CLOSURE_ENABLED=true` 显式启用，Answer Checkpoint v2 保持默认关闭。
- 执行终态、已登记目标覆盖和原问题完整性分别记录。答题质量以同题、同冻结资料、同模型的真实对照为依据。验证与后续边界见[迁移计划](docs/spec/agent-migration-plan.md)。

## CodeAgent V2 架构与接口冻结（2026-11-13）

`claudecode` 的 `9a1c71e` 实际包含「V2 设计 + V1 运行配置预备实现」：Profile、Manifest、工具预检、执行/会话配置及 Runtime、API、schema、轨迹前端配套改动已提交，不再按未提交或纯文档描述。架构设计通过，整包提交有条件通过。阶段 1 已实现 `core-v1` 最小主链（Profile、RunContext、隔离 core 工作区、FileCapability、DirectAgentRuntime、QueryService 接入）；阶段 2 已实现 `core-business-v1` 的 Business Context 工程接入；在线问答默认已于 2026-11-15 切换为 Pi，完整 V2 其余能力未实现，真实模型答案验收与业务效果对照因供应商配额未完成。

后续新 Agent 功能必须先读 [V2 总体架构](docs/spec/agent-architecture-v2.md)、[能力与运行合同](docs/spec/agent-capability-contract.md) 和 [迁移计划](docs/spec/agent-migration-plan.md)，遵守以下边界：

- 唯一调查主链为 QueryService → RunContext → AgentRuntime → 模型自主选择工具 → Answer → 证据检查与持久化；Trace 从运行开始持续记录。Business Context、AST、检索与契约不能各自建立全局调查编排。
- RunContext 复用现有 ProjectContext，固定工程、Profile、范围、工作区、资料和实际能力。工具只能使用授权快照，不再自行读取环境变量或探测相邻资料。
- 首个实现目标 `core-v1` 只使用源码、可选需求和 Read/Glob/Grep；业务资料、AST、Code Map、Hybrid 与 Contract 均禁用，即使机器上已有产物也不探测/使用。Core 仅在评测代码显式构造 Direct Runtime 时使用，在线入口不再读取 `BUSINESS_CODE_AGENT_PROFILE`；Core 的 `mode=combined` 映射为 `core`，显式 `none/backbone/ast` 返回配置错误。`legacy-v1` 是未来对现有 coordinated 的显式适配，不是当前已开放名称。
- required/optional/disabled 是授权策略，ready/missing/stale/unavailable 是实际状态；optional 缺失/过期只收缩原 Profile 内可用能力，required 不可用明确失败，不自动换 Profile 或生成索引。
- 能力扩展只增加提供者、工具/阅读指导及注册配置，不修改 Direct 核心循环。源码、需求/业务约定和结构导航保留不同证明能力，引用校验不是语义证明。
- 工程、范围、Profile、可用资料/工具及其版本变化时重建模型会话，历史运行不补写新资料；同名仓库或版本不能跨工程复用。
- Query 核心依赖公共工具合同，不依赖知识管理或 AST 内部实现；知识管理不调用问答主循环，CodeIntel 不生成最终业务答案，Evaluation 只消费公共 Runtime，API/CLI 只做装配与传输。
- `core-v1` 阶段 1 已实现 AgentProfile、RunContext、MaterialSource、简单 Capability 注册、FileTools、DirectAgentRuntime 和 QueryService core 分支；测试位于 `tests/test_core_*.py`、`tests/test_direct_runtime.py`。完整动态失效、结构版本联动、复杂 Evidence 生命周期、多级依赖/重绑定及 AST/Contract 专属授权仍随能力延后，不成为 core 前置，也不新增抽象层。
- `core-business-v1` 阶段 2 已实现可选 Business Context：资料由项目业务目录只读挂载，复用 Read/Glob/Grep；无专用业务搜索工具，无新调查循环。业务文档内容指纹变化会重建会话；业务资料不能当作源码已实现，也不能扩大本轮仓库授权。AST/Code Map/Hybrid/Contract 仍 disabled；真实模型 Core 与 Core+Business 效果对照尚未完成，工程测试通过不等于业务收益已验证。
- 预期仓库必须来自配置和规范化 scope，由 `prepare_core` 逐一核验；任一必需仓库缺失/不可读须在模型前失败，错误与 execution/Trace 明确仓库 ID，不能以 File 工具已注册或部分仓库可读代替完整性检查。core 不构造 AstMaterial，不探测业务、Code Map、AST、Hybrid 或 Contract。
- Core 已获得真实模型运行和小样本对照。Core 仍为显式 Profile，不参与默认链路（默认已是 Pi）；其生产答案质量验收尚未通过，不因默认切换而视为已验证。模拟模型、API 回归及供应商失败记录只用于验证工程行为。
- Direct 通过验收前禁止删除旧 LangChainRuntime、Coordinator 或 Investigation；Direct 不继承其调查逻辑。在线入口已封存旧 Runtime 选择，旧链代码删除另行确认。

下文 `combined`、`coordinated`、自动补查、既有 Profile/开关与资料模式约定继续服务 V1 及历史对照，不作为 V2 core 的隐含授权。资料来源边界、只读、手动生成、工程隔离、无限制生产调查与真实效果验证原则继续适用；V2 接口和迁移以以上三份文档为准。现有 `profile.py/materials.py/preparation.py` 准备工作不等于 V2 已完成。

## V1 知识体系重构与检索诊断（2026-11-02）

2026-11-02 用户确认资料组合使用：主问答默认 `mode=combined`，基础资料、业务补充知识、已有自动代码地图与可用的手动 AST 版本同时可检索，按问题需要读取；页面取消互斥资料选择。AST 未生成、待更新或暂不可用时继续使用其余资料，不隐式生成。AST 与自动代码地图保留独立来源，结构资料只用于导航。旧 none/backbone/ast 分组保留给历史兼容和显式对照，不再决定日常问答默认资料。工程、范围或 AST 版本变化仍重新建立模型会话，历史运行保留原模式及版本。

2026-11-02 用户取消主问答调查预算：生产 LangChain 调查不再限制模型调用次数、总时长、自动补查总数、入口总数或调查任务总数，LangGraph 不再设有限循环步数；也不因其它系统前端连续两次无命中强制收尾。保留手动停止、上下文容量、单次工具原文上限和单次网络请求超时。旧 `BUSINESS_CODE_AGENT_MAX_CALLS`／`BUSINESS_CODE_AGENT_TIMEOUT` 不再控制生产调查；有界评测通过构造参数 `max_calls`／`max_seconds` 显式指定，并保留在轨迹设置中。

2026-11-02 用户确认检索调度与完整轨迹查看：主问答默认 `WORKFLOW=coordinated`，由程序按问题目标与新到达的证据安排精确定位、混合检索、契约核对和缺口补读；模型仍可追加调查与解释。`RETRIEVAL=auto`、`CONTRACTS=auto` 仅复用已准备的当前工程索引，未准备时使用文件工具，不生成 AST 或下载模型；显式 files/off 继续有效，basic/tracked 保留作对照。默认答案仍为 plain，不能把调度实现或本地模拟通过当作答题收益已验证。此前关于 basic 默认及契约工具关闭的约定由本段替代，facts/contracts 答题收尾仍为实验能力。

每次运行将公开模型请求／响应、工具定义、完整工具结果、程序调度原因、调查状态、错误和停止原因保存到工程 `query-traces/<runId>.json`，与工作区生命周期分开；工作区 `.langchain-traces/` 仍保留运行时快照。问答页每轮“查看调用轨迹”可回看、按类型筛选及导出，运行中、失败和取消均保留记录。接口按工程与运行编号读取，历史资料缺失时明确说明，不补造旧轨迹。不记录认证信息和模型私有思考。

2026-10-31 用户调整：生产主问答迁移为项目自有 LangChain 1.2 Agent，读取 `.env` 模型连接，管理上下文并调用只读 Read／Glob／Grep。已显式准备的工程检索索引可增加 SearchCode：函数／类分块、BM25 权重不低于向量的并发混合召回、Top-50 Cross-Encoder、带废弃／截断标记的上下文及 Git diff 增量更新。50–200ms 是待实测的精排目标，不是保证；超预算需记录并保留混合排序结果。结构和向量索引仍必须显式准备，不在启动、页面或问答中隐式生成 AST 或重建索引。现行运行时约定见架构文档，原 Claude CLI 保留给历史评测和显式兼容运行。

主问答默认 `ANSWER_MODE=plain`。可选 `facts` 模式保留最终一次无工具模型调用整理分层事实，再由程序核对实际交付的原文、行号、字段分段和模型声明的层次。未观察到不等于不存在，接口已返回不等于页面已使用。两次真实固定证据复验暴露了对象归属、透传出处遗漏和范围误分类，尚未通过默认启用验收。引用和字段分段检查不是任意业务语义或跨系统连接的证明，不能把有引用或格式通过当成答案正确；后续需独立固定证据及完整 Agent 验证。

工具已修复递归 Glob 与大小写检索，工程职责导航复用配置并按本轮仓库过滤。`WORKFLOW=basic` 提供文件工具与搜索状态交接，`tracked` 和默认的 `coordinated` 均执行 Investigation 中明确的 next_read。关键待办无具体补查时，按已有证据和缺口进行一次无工具答题。本轮同工具同参数的重复定位跳过并记录，纯重复且没有程序补查时结束调查。此前真实回归虽补读了客户端与渠道 Controller，仍出现响应字段断言错误和待办未更新。

2026-10-31 用户确认契约索引方案：先实现显式准备的 Spring HTTP／RestClient、返回对象与字段透传索引，再接入关系查询、由未解析连接生成的补查和收尾事实约束。实现落在 `codeintel/contracts/`，先复用工程内 SQLite；源码、需求、业务语义与导航仍保留来源区别。结构关系与字段事实分开验证，图存在不等于正确。现行依据补充为指导方向第 14 节；功能与收益未验证时不默认启用。

契约实验继续增加返回实体成员与 setter 写入点的绑定，保留局部 if 条件、try/catch 与原文。状态／失败／区分／改动类问题核对字段后，由程序补查这些写入点；单纯字段查询不自动展开。局部分支不是完整状态机或执行证明，条件解释仍需复核。收尾允许引用分支编号的业务解读，改动采用有前提的选项，不自由承诺单点收益；已有字段先复用。原文按文件和行号合并，缺失行不补齐；索引升级仍需显式 `--reparse`。

异常解释只确认 catch 中明确写入的成员，不把调用前／try 内的值当成异常后的保证。消费者未登记及其它系统前端无命中只记录为调查缺口，不按次数强制收尾；不据此断言页面不存在，也不删减正常资料。真实调用与修正后的本地复放分开报告；contracts 答题收尾仍为实验能力，coordinated 可按需复用已准备的契约工具。

2026-10-31 用户确认展示调用链图：知识图谱页增加独立的“调用链”视图，复用当前工程已准备的契约索引，显示接口、服务方法、HTTP 客户端和远端接口；节点／连线可查看版本化源码依据。原有业务关系视图保留。页面与 API 只读，不生成 AST、不调用模型、不将结构图浏览作为问答收益；当前仅投影已索引的返回依赖调用，不声称完整执行链。

涉及项目认知、业务补充知识、自动代码地图、知识主干、词云／知识图谱、入口导航、问答调查或相关页面的设计和修改前，必须先阅读 [知识主干与知识图谱：项目整改指导方向](docs/spec/knowledge-backbone-direction.md)，以第 11 节的资料边界、第 12 节的检索诊断、第 13 节的自有 Agent 与混合检索及第 14 节（2026-10-31）的契约与返回字段约定为依据。此前关于“大而全知识主干”的章节保留为历史背景，不得作为新功能的默认设计。

- 资料分为四类：源码是当前实现依据；需求原文是被提出的目标；业务补充知识只记录源码难以推断的项目特有语义、边界、术语、跨系统关系和例外；自动代码地图只负责定位文件、符号和结构。
- 业务补充知识的目录和写法遵循 [业务补充知识使用指南](docs/spec/business-baseline-guide.md)；历史 `knowledge/baseline` 仅作为兼容读取来源。
- 不再把完整业务流程、调用链、类名、字段、URL 和可由源码确认的细节复制成“大知识主干”。业务补充知识应短、可核查、标明适用范围和依据。
- 每次问答按需读取资料；项目索引和自动代码地图是导航，不是答案证据；源码确认当前实现，资料冲突时分别说明。
- 知识图谱先作为管理和可视化能力保留，只有业务补充知识证明了语义收益后再扩展复杂图谱、RAG 或固定检索工作流。
- 区分目标、已实现能力和已验证收益；不得把独立图谱浏览、目录存在或知识文件被读取视为增益证明。
- AST 是独立的结构资料，不等同业务补充知识；只能由用户手动生成并按版本引用。启动项目、进入页面、选择 AST 模式或启动实验不得隐式生成 AST。

旧资料目录可通过显式迁移进入 `knowledge/business-context/`；新注册工程登记只创建边界和空目录，不自动复制配置中的资料。

若用户明确调整方向，同步更新指导文档和本文件，使后续工作读取到一致的约定。

### 主干收益诊断

检索改造先使用[检索诊断约定](docs/spec/retrieval-evaluation.md)分别测候选召回、最终上下文和固定证据答题。已标注证据覆盖不等于材料充分或答案正确；未标注内容不能自动判为无关。标注来源、开发题／保留题、模型与预算必须明确记录。各模型内先做同题配对，再比较模型间增益，不以换弱模型或削减对照组正常资料制造优势。当前检索基准是独立实验能力，不能把词项检索结果当作生产 Agent 的完整调查结果。

按指导方向第 9 节的诊断约定区分无主干、仅总览和完整主干，以及代码细节和业务调查题（证据记录见[诊断验证说明](docs/records/2026-09/knowledge-backbone-diagnosis-20260908.md)）。评分标准与源码冲突时修订标准并独立重评，不给符合源码的答案扣分，不覆盖原批次；不得删减对照组正常资料以制造增益。

AST 对照按指导方向第 10 节执行，方案与证据见 [AST对照方案](docs/records/2026-09/ast-comparison-20260908.md)：结构图由冻结源码机械生成；独立AST与总览＋AST分开比较，不能将结构目录或读取次数当作业务图谱收益。

## 目录与分层

根目录只放入口和元数据。主启动统一使用 `start.sh`，Windows 在 Git Bash 中运行，`start-mac.sh` 兼容原 macOS 命令。实现位于 `scripts/launcher/`，工程配置按 UTF-8 读取，支持带 BOM 的文件。旧离线入口供对应版本的离线包使用。打包脚本位于 `scripts/offline/`，评测脚本位于 `scripts/evaluation/`。新增脚本放到 `scripts/` 下对应子目录。

后端 `business_code_agent/` 的分包、依赖方向和 import 规则见[当前架构](docs/spec/architecture.md)的“包结构与依赖方向”，改动前先读该节，不要新增平铺模块。

前端 `frontend/src/`：`app/` 放应用壳与跨页面状态（工程、管理员、路由），`features/<功能>/` 放页面及其自有状态，`ui/` 是唯一设计令牌来源（`tokens.css` 是语义变量来源，`tokens.js` 只做 Ant Design 的 JS 侧映射），`lib/` 放无 UI 的工具。全局状态只保留当前页面、当前工程、工作区快照和会话历史。

文档分 `docs/spec/`（现行约定，改动功能前读这里）与 `docs/records/<年-月>/`（历史记录，只用于追溯结论来源）。结论变化时修订 `spec/` 中对应文档，不要新增“新版说明”与旧版并存；`spec/` 不把 `records/` 当作规范性依据，只在需要证据时指向记录。索引见 [文档索引](docs/README.md)。

测试放在 `tests/`，夹具放 `tests/fixtures/`。演示工程以 `examples/` 为唯一来源，测试需要时从那里复制，不在 `tests/fixtures/` 维护第二份同名源码。

## 待完成的结构调整

以下方向已确认但尚未执行，不要当作现状：拆分 `evaluation/service.py`（现约 1300 行，含批次编排、题库、人工复核与状态持久化）；把运行时数据落到[当前架构](docs/spec/architecture.md) 已描述的 `.data/projects/<project-id>/` 布局，并把 `project.config.json` 的 `baselineRoot` 默认值改为 `businessContextRoot`；合并 `examples/` 与 `tests/fixtures/` 中重复的演示源码。

## 多工程边界

- 平台可以登记并切换多个独立工程；工程配置、数据库、工作区、知识维护、题库、AST 和评测批次必须通过 `ProjectContext` 归属到当前工程。
- 规范请求使用 `/api/projects/<projectId>/...`；兼容请求必须明确携带工程上下文，不能用可变的全局当前工程覆盖另一个标签页。
- 工程登记只创建边界和数据目录，不自动同步源码、索引、导入主干或生成 AST；已有历史数据迁移必须显式指定目录或执行迁移步骤。
- 不同工程的会话、资料版本和评测输入不得互相续用；验证 A/B 隔离时要覆盖相同仓库 ID、相同案例 ID 和跨工程会话/版本引用。
