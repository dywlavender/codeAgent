# CodeAgent V2 架构设计

状态：设计冻结；`core-v1` 阶段 1 最小主链与 `core-business-v1` 阶段 2 Business Context 工程接入已实现，但只供评测代码显式构造 Direct Runtime 使用；正常在线问答固定为 Pi（2026-11-15），环境变量不再切换。真实模型效果验收仍未完成。确认日期：2026-11-13。

`claudecode` 提交 `9a1c71e7c7bd7400d8d400475f51e47ccbca41f2` 的实际交付是「V2 设计 + V1 运行配置预备实现」，不是纯文档提交。Profile、资料清单、工具预检、执行快照与会话配置已进入该提交，另有 Runtime、API、schema 和轨迹前端配套改动；这些不是 V2 Direct Runtime。当前结论为：架构设计通过，整包提交有条件通过，验证范围与缺口见[迁移计划第 7 节](agent-migration-plan.md#7-本次验证与交付状态)。

## 1. 适用范围与冻结内容

- [当前架构](architecture.md)记录 Pi 在线行为、资料模式、会话和 Trace。旧 `coordinated`、混合检索与契约工具只服务历史运行时或独立诊断。
- 本文规定职责和依赖边界；[能力与运行合同](agent-capability-contract.md)保留资料授权、证据规则及 Direct 设计接口；[迁移计划](agent-migration-plan.md)记录实施与验收。
- V2 首个实现目标为 `core-v1`：只有源码、模型连接及 Read／Glob／Grep 即可调查；需求是可选资料，不需要任何预建索引。
- 新增资料和能力必须在同一调查主链内使用。Pi 已按资料模式提供 Business Context 和结构导航；SearchCode 与契约工具是否进入主链，按独立效果对照决定。
- 设计任务的原始范围仅为文档，但实际提交已包含 V1 预备实现；交付验收必须覆盖这些实现，不能用文档走查或三组旧回归替代。本轮收尾只修订文档和专项测试，不回滚实现或扩大到 core 开发。

架构边界、资料授权和证据边界继续适用。阶段 1 已实现 `core-v1` 的 Profile、RunContext、按配置逐一核验仓库的隔离工作区、FileCapability、DirectAgentRuntime 及 QueryService 接入。完整的运行期能力失效联动和复杂 Evidence 生命周期仍是设计目标，不是 Pi 上线前置。

阶段 1 的实现边界见[迁移计划第 8 节](agent-migration-plan.md#8-阶段-1-实现与验证2026-11-13)，阶段 2 的 Business Context 工程接入见[第 9 节](agent-migration-plan.md#9-阶段-2-business-context-工程实现2026-11-13)。显式注入 `core-v1` 时，`mode=combined` 会映射为 `core`，只提供源码和可选需求；`core-business-v1` 额外提供可选业务文档。两者都不挂载结构或检索索引，也不接受显式 `none/backbone/ast`。

## 2. 唯一调查主链

```text
用户问题 + 工程/范围/资料模式
    ↓
QueryService：校验归属、运行互斥、资料授权、只读工作区
    ↓
构建 RunContext + Pi 工具预检 + 核对会话身份
    ↓
PiAgentRuntime → Pi 负责模型、工具、上下文和会话
    ↓
模型选择已注册 Tool → 执行 → 原文/导航/缺口 → 模型
    └───────────────────────────────────────────┘
    ↓ 模型提交 Answer
引用/证据边界校验 → RuntimeResult
    ↓
QueryService：答案、会话、运行状态与 Trace 持久化
```

Trace 从运行创建开始持续记录，并非回答之后才开始。资料/能力预检失败、工具失败、模型失败和取消也有记录。验证不能暗中补读源码、调用第二个调查 Agent 或将结构线索升级为实现证明。

### 职责分配

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| QueryService | 工程归属、会话与并发运行管理，调用上下文构建和能力装配，保存/转发事件，取消与终态 | 选择检索词、按业务问题分类调度工具、拼装源码答案 |
| RunContext / AgentProfile | 固定本轮实际授权及明确的执行配置 | 重新读取环境变量、全局当前工程、自动扩大范围 |
| Workspace | 将授权资料映射为只读虚拟路径，保存目录/版本映射 | 同步仓库、解析代码、生成/更新索引或下载模型 |
| Capability 注册层 | 预检、工具绑定、阅读指导、状态及版本描述 | 维护全局待办、按新证据自动补查、调用模型 |
| AgentRuntime | 单一模型/工具循环、上下文管理、停止响应、引用收集 | 知识维护、AST 生命周期、工程注册、后台资料准备 |
| Tool | 执行一次受控操作，返回资料、原文、导航或明确缺口 | 建立第二套调查循环、跨授权搜索、生成最终业务答案 |
| 证据检查 | 检查已交付内容、版本、行号和声明的证据类别 | 任意业务语义证明、保证调用链完整、自动修正业务结论 |
| Trace / Session | 审计公开交互，按身份隔离会话，管理原文新鲜度 | 将历史回答或摘要当作新一轮源码证据 |

V1 `coordinated` 的程序调度保留为历史对照；预留名称 `legacy-v1` 尚未开放。Pi 和 Direct 都不经由 Coordinator 选择工具或安排补读。

### Direct 的目标、证据和收尾

开启 Answer Closure 后，模型可在同一调查循环中登记原问题要求、必要关系和带原文的结论。Question Contract v3 留存原问题和追加式目标，模型负责解释问题及判断关系必要性。Evidence Ledger 将结论和连接引用关联到本轮实际交付的授权 Read 原文。Answer Closure 渲染有依据的结论和未核实项，完整覆盖原问题仍需语义复核。

模型选择何时继续定位、读取或提交答案，`RecordFindings` 只维护当前运行状态。正常提交直接结束。同一结论混用有效和无效引用时，原循环可收回一次提交，让模型用已读原文拆分结论或明确缺口。现有有界收尾可用相同字段提交部分结论。该路径在 Direct Profile 中显式启用，运行配置和记录进入现有 Trace。具体检查及启用方式见[能力与运行合同](agent-capability-contract.md#81-direct-答案覆盖实验)。

## 3. 资料与工具分层

| 类别 | 对象 | 如何交付 | 证明边界 |
| --- | --- | --- | --- |
| 原始资料 | Source、仓库 README、Requirements | 受控文件读取 | 源码支持已核对实现；README 是项目背景；需求只说明目标 |
| 业务认知 | Business Context | 受控业务资料目录和阅读指导 | 项目术语、业务约定、系统协作、数据生命周期；不证明已经实现 |
| 代码结构 | AST、Code Map、Contract Index | 结构导航、符号/关系工具，必要时另读源码 | 已分析的候选关系；不是完整执行链或业务规则证明 |
| 检索实现 | Hybrid Search | 可选 SearchCode，受资料授权约束 | 排名算法不是知识类别；命中和高分不证明业务结论 |

Code Map 在 V2 是结构导航的一种视图，不要求永远维持独立知识体系。迁移期间仍保留 `code_map` 来源标识、现有文件目录与版本，避免抹掉历史来源；本阶段不合并 Code Map 与 AST。

工具名不决定证据性质。Read 读取 AST Markdown 仍是结构导航；SearchCode 若交付经过当前文件核对的完整原文片段，可以单独产生源码证据，排名/结构关系本身仍是导航。业务资料中引用的类名、接口或流程只提供调查线索。

Pi 和 Direct 都复用各自的文件工具读取授权业务目录。Direct 设计中的 `AstCapability` 与 `FindCodeRelation` 尚未开放；线上 Pi 可以读取授权的结构资料和查询固定版本的符号及关系。

## 4. 统一上下文与能力装配

复用 `platform/projects.py` 的 `ProjectContext`，不再建立第二套工程配置/管理对象。`RunContext` 包含工程、Profile、规范化范围、工作区、资料清单、能力决策及可复用的运行时会话 ID；它是本轮授权快照，不是供工具读取任意工程目录的通行证。

在线 Pi 依[当前架构](architecture.md#workspace)准备资料和工作区。以下是冻结的 Direct 能力装配顺序：

1. 由请求关联的 ProjectContext 校验会话归属及系统/仓库 ID，将范围展开成具体仓库集合。先授权，再探测可选资料。
2. 解析 Profile。`disabled` 的能力不得探测索引、导入其可选依赖、挂载资料、注入指导或恢复历史工具结果。
3. 根据 Profile 和范围形成 MaterialSource，固定目录、所属仓库及版本；准备最小只读 Workspace。必需源码仓库缺失时失败，不能静默缩小范围。
4. 创建仅用于预检的 RunContext；对非 disabled 能力调用 `status`，required 不可用则记录配置失败，optional 不可用则排除该能力。
5. 对 ready 能力绑定工具和指导，检查名字冲突与来源范围，形成最终能力快照。会话尚未确定时，工具只能绑定资料授权，不能绑定旧会话状态。
6. 先解析冻结的 RuntimeSettings/RunOptions，用工程、范围、完整 Profile、资料、工具定义与这两份设置计算会话身份；同一设置依次传入工厂和 ask，核对后设置 `session_id`。只有冻结完成的上下文允许进入模型循环。

后续接入业务/结构能力时，其预检失败必须同步排除 File 正文授权，运行中也不能只停用关系工具却允许 Read 继续访问旧 AST。完整 RunAccess、AvailabilityChange、重绑定及跨来源证据失效为对应扩展阶段的合同；阶段 1 只有 File，先落实简单注册、授权检查与明确失败，不要求一次实现这些远期机制。

具体两阶段类型和接口见[合同第 5 节](agent-capability-contract.md#5-装配协议与-bootstrap-顺序)。工具不得从全局环境、相邻工作区或其他 Profile 的缓存再次发现能力。

## 5. Profile 与运行期行为

| Profile | Runtime | 资料与能力 | 当前状态 |
| --- | --- | --- | --- |
| `pi-v1` | `PiAgentRuntime` | 按 `combined`／`none`／`backbone`／`ast` 授权资料，只读文件工具和 `codemode` | 唯一在线 Runtime |
| `core-v1` | `langchain-direct` | 源码 required、需求 optional、File required，其余 disabled | 已实现基础主链（阶段 1）；显式 Profile，默认 Pi |
| `legacy-v1` | `langchain-legacy` | 保留当前 combined/coordinated 及已准备索引的使用语义 | 需未来兼容适配；当前仍用现有入口 |
| `core-business-v1` | 同一 `langchain-direct` | core 加可选 Business Context 文件与阅读指导 | 阶段 2 工程接入已实现；效果未验收 |
| `core-ast-v1` | 同一 `langchain-direct` | core 加 AST 结构导航 | 预留，未开放 |
| `core-business-ast-v1` | 同一 `langchain-direct` | core 加两种能力 | 预留，未开放 |

阶段 1 新增 core 正式注册和保留旧入口所需的最薄 Legacy 适配；阶段 2 只新增 `core-business-v1` 与一个无工具的 `BusinessContextCapability`，复用同一 Direct 模型/工具循环。其余组合 Profile 仍为预留。不同组合不能对应不同 DirectRuntime 子类或不同调查算法。

- `required` 是必须具备的能力/资料，不要求模型每题必须调用或读取。
- `optional` 是授权尝试使用现有可用资料，不允许准备、下载或补建。
- `disabled` 是禁止使用，不是「没有找到」；即使本机有索引也不能启用。
- 预检/执行期不可用不能自动切换 Profile 或 Legacy。索引过期时，在原 Profile 已允许的 File 能力内继续调查；required 能力失效则明确失败。
- 新索引在运行途中准备完成也不能临时启用，必须下一轮重新构建上下文。

Direct 的模型自主决定下一次 Read／Glob／Grep 和何时回答。它不继承 V1 的固定补读、Investigation 待办或 Coordinator。Direct 评测可通过公开参数显式限制模型调用和总时长；Pi 使用 `BUSINESS_CODE_PI_TIMEOUT`，不读取 Direct 的调查预算。

上下文不足时可以丢弃/摘要历史工具正文，但必须标明原文未在当前上下文中，不能伪装为仍可引用。证据不足可以回答「尚未确认」，不是强制打开其他能力的理由。

Direct 的可选 Context Manager 在模型请求前检查 token 预算，保留原问题、短调查摘要和最近的工具批次，并用现有 SessionStore 继承记录。模型可经 ReadLedger 恢复压缩前的原文。运行协议见[能力与运行合同](agent-capability-contract.md#82-direct-上下文管理实验)。

## 6. 逻辑目标结构与依赖规则

以下是目标职责位置。阶段 1 已创建 `context.py`、`capabilities.py`、`tools/files.py`、`agent/direct.py` 和 `agent/session.py`；其余是后续能力的目标，不要求一次建齐。

```text
business_code_agent/
├── platform/projects.py        ProjectContext 与工程注册
├── query/
│   ├── service.py              会话与运行管理
│   ├── runtime.py              公共 Runtime 协议和既有 RuntimeResult
│   ├── context.py              RunContext 与 QueryScope
│   ├── profile.py              AgentProfile 与解析
│   ├── materials.py            MaterialSource 与授权清单
│   ├── capabilities.py         Capability 协议、注册和绑定
│   ├── workspace.py            工作区与资料授权
│   ├── agent/pi/                在线 Pi RPC、会话和只读扩展
│   ├── agent/direct.py         唯一 Direct 模型/工具循环
│   ├── agent/session.py        会话身份与原文新鲜度
│   ├── tools/files.py          无索引文件能力
│   ├── langchain_agent/        原 V1，迁移期保留
│   └── traces.py               工程运行归档
├── knowledge/                 业务认知管理及其公开只读接口
├── codeintel/                 AST/代码地图/检索/契约及其公开只读接口
├── requirement/               需求管理
├── evaluation/                独立评测，消费公共 Runtime
├── api/                       请求处理与依赖装配
└── storage/                   数据持久化
```

依赖规则：

- `query/service.py`、`agent/direct.py` 和通用注册层依赖协议，不引用 knowledge、AST 或混合检索内部模块。领域适配器经 API/CLI 组合根注入注册层；后续可放在既有 `query/tools/` 下，但只能调用对应领域的公开只读接口。
- 核心文件工具不应为后缀/忽略目录常量依赖 `codeintel/retrieval.py`；下一阶段在文件工具范围内收敛这类通用策略，不连带迁移检索算法。
- `knowledge/` 不调用问答 Agent 主循环。现有知识维护中的模型能力不因此删除，但不能复用它建立第二套问答调查。
- `codeintel/` 不生成最终业务答案、不依赖 LangChain；解析/生成与只读查询分开，生成仍由显式管理操作触发。
- `evaluation/ → query/runtime` 是单向的公共接口消费。`query/` 不依赖 evaluation，`platform/` 不为问答导入 evaluation。AST 生命周期由 CodeIntel 管理，评测冻结并引用版本。
- `api/` 与 CLI 是组合根，只构造/注入服务与提供传输；领域逻辑不得搬到路由。
- ProjectContext 继续提供工程存储边界，底层 storage/shared 不反向依赖领域。已有包边界规则继续生效，V2 的更细隔离需要未来新增测试，不声称现有测试全部覆盖。

这些规则限制的是依赖方向与公共行为，不要求为每个能力制造独立管理层或插件框架。

## 7. 零索引完整执行示例

输入为工程配置中已有的三个本地仓库及其系统归属：

```text
repos/
├── channel/
├── middle/
└── decision/
```

没有 AST、Code Map、Hybrid Index、Contract Index 或 Business Context。

1. **工程准备**：复用 ProjectContext 和工程配置，核对三个仓库真实目录；配置系统职责仅作导航。建立只读映射，不运行 Git 同步、解析器或索引准备器；无仓库内容时明确失败。
2. **开始运行**：QueryService 创建运行记录、Trace 与 `core-v1`，规范化 QueryScope 为三个仓库，生成三个 source MaterialSource，记录需求 missing/ready 和其他资料 disabled。
3. **工具装配**：FileCapability ready，只绑定 Read／Glob／Grep。禁用能力不执行 status，更不扫描机器上已有索引。基于最终快照确定是否复用 session。
4. **自主调查**：模型用 Glob 看目录、Grep 定位业务词/符号、Read 读取正文；发现客户端调用或事件名后，自行在其他授权仓库继续搜索。不需要预建符号库，不保证所有动态连接都能找到。
5. **形成答案**：区分已确认代码行为、读取到的配置/需求，以及没有找到连接证据的部分。源码引用来自本轮交付的原文及行号；README 说明不升级为实现证明。
6. **校验与记录**：Runtime 记录来源校验与 `semanticProof=false`，返回既有 RuntimeResult。QueryService 保存 Profile、实际资料、工具、引用、答案、会话身份和完整 Trace；完成不表示所有关系均已查明。
7. **追问**：工程/范围/Profile/资料或能力版本变化重建模型会话；相同身份下仍校验旧原文新鲜度。历史轮次不被新资料补写。

增加业务知识只增加授权资料与阅读指导；增加 AST 只增加结构查询能力。上述步骤与 Runtime 循环均不改变。五个接口级场景及反例见[迁移计划第 5 节](agent-migration-plan.md#5-五个接口级走查)。

## 8. 交付与后续闸门

设计冻结通过条件为 A01—A12 能由接口和场景解释清楚；该设计已通过。阶段 1 已补配置/预检/会话/失败持久化及 core 闭环测试，逐一缺失仓库校验已在 `prepare_core` 实现。还没有完成真实模型答案验收：2026-11-13 对 `validation-projects/complex-loan` 发起真实请求时供应商返回 403（免费额度耗尽），core 已正确保存失败 Run、事件和 Trace。不能据此宣称零索引 Agent 的答案质量、业务认知收益或 AST 收益已验证。

Core 和 Business Context 在 Direct 实验中已接入。阶段 2 后真实模型小样本、证据与收尾的验证见[迁移计划](agent-migration-plan.md)。线上问答已切换到 Pi，质量对照单独进行。

## 9. Pi Runtime 统一主链（2026-11-15）

在线问答使用 `PiAgentRuntime`，正常 API 入口固定进入 Pi；旧 Profile／Runtime 环境变量不能再覆盖。Pi 负责模型调用、工具选择与调度、上下文压缩和会话；CodeAgent 继续负责工程/资料授权、RunContext、Trace、取消、持久化和业务问答增强。`QueryService._query_pi()` 统一处理 `combined`／`none`／`backbone`／`ast`，失败不自动回退旧链。旧 Runtime 仅允许评测代码显式构造并注入。

Pi 只开放 `read`、`grep`、`find`、`ls`、`codemode`。工作区仍通过软链接暴露授权仓库；只读扩展从授权真实根目录搜索并通过 `CODEAGENT_PI_MOUNTS` 映射回工作区，不使用全局 `--follow`，仓库内部软链接不会扩大授权范围；执行前仍检查入口路径。工作区 cwd 不是 OS 安全边界，扩展也不替代进程级隔离。Pi 会话身份包含资料版本，业务文档变化或源码元数据变化时重建。最终答案引用按本轮交付的 `read` 原文核验路径与行区间，只证明原文交付，`semanticProof=false`。实现与真实运行证据见[迁移计划第 10 节](agent-migration-plan.md#10-pi-runtime-统一问答主链2026-11-15已实现并默认启用)。

证据核验和目标覆盖只用于观测模型声明。停止收敛、原问题完整性与业务补充知识增益须以冻结题库的真实答案、源码和 Trace 对照验收。尚无证据表明需要把 Direct 的 Context Manager、Question Contract、Evidence Ledger 或 Answer Closure 移植到 Pi；旧链删除另行决定。
