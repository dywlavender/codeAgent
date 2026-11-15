# CodeAgent V2 迁移与验收计划

状态：V2 设计冻结，`core-v1` 和 `core-business-v1` 已显式开放，生产默认使用 Legacy。Direct 的 Evidence Ledger 与 Answer Closure 作为独立实验接入，真实答案质量仍按对应对照验收。

依据见[V2 总体架构](agent-architecture-v2.md)和[能力与运行合同](agent-capability-contract.md)。第 1–7 节保留设计冻结时的基线及验收记录，实现进度见第 8 节起。

## 1. 当前基线与保护范围

核对基线为 `claudecode` 提交 `9a1c71e7c7bd7400d8d400475f51e47ccbca41f2`（`feat: resolve runs through agent profiles and material manifests`）。其 24 个文件变更同时包含 V2 设计与 V1 运行配置预备实现，以及 Runtime、API、schema、轨迹前端配套修改。原设计任务开始时基于 `2531add` 观察到的未提交状态已不代表当前交付，不能据此称整包仅文档。

关键现状：

- [query/service.py](../../business_code_agent/query/service.py) 已调用 `prepare`、`ask_prepared`，保存 `execution_json/session_config_json`，会话比较包含工具与资料；构造函数仍接收多个路径，尚无 RunContext。
- [query/profile.py](../../business_code_agent/query/profile.py)、[query/materials.py](../../business_code_agent/query/materials.py) 和 [langchain_agent/preparation.py](../../business_code_agent/query/langchain_agent/preparation.py) 已进入该提交，实现 V1 AgentProfile 解析、MaterialManifest 和工具预检；不是未跟踪文件，也不等于 V2 合同已实现。
- Manifest 目前按实际 `repos/` 子目录枚举，授权三仓库但只存在两个时不会逐一列出缺失仓库；File 工具已注册也不代表预期源码齐全。这是阶段 1 必修缺口，不作为已通过的授权完整性能力。
- 当前 `standard-v1` 的 `agent_direct` 分支明确抛出未开放错误；其资料默认包含多类资料、搜索 optional，不能直接改名并声称等价 `core-v1`。
- 默认仍是当前 coordinated 及 combined。V2 的 `core-v1/legacy-v1` 是未来注册名称，本次不修改环境变量解析或默认行为。
- [platform/projects.py](../../business_code_agent/platform/projects.py) 已有 ProjectContext；`query/` 目前没有类型化 QueryScope，scope 由服务校验并展开。
- query 已不直接依赖 evaluation；AST 只读投影位于 [platform/ast_material.py](../../business_code_agent/platform/ast_material.py)，完整 AstService 由 API 注入，生成仍在 evaluation。不能误写成当前存在 `platform → evaluation → query` 的直接 import 环。
- 当前 AstService 构造包含目录创建与中断状态修复，AstMaterial 也可能创建目录、写新鲜度缓存。因此 V2 的「纯只读能力预检」不能直接无差别调用这些生命周期构造；core 应完全绕开，后续 AST 阶段提供真正只读的固定版本 reader。

| 成熟度 | 当前内容 |
| --- | --- |
| 已实现并提交（V1 预备） | resolve_profile、MaterialManifest、prepare_tools、execution_snapshot、execution_json/session_config_json 及配套接口/展示；仍走旧 Runtime |
| 已设计（V2） | 唯一 Direct 主链、RunContext、Profile/Capability 合同、证据边界、隔离及迁移顺序；五场景为接口走查 |
| 待实现与验收 | core-v1、DirectRuntime、V2 授权/会话、逐一缺仓库失败检查；之后才接入业务/AST/Hybrid/Contract |

本轮收尾修正文档并补充上述 V1 机制专项测试，不回滚已提交实现，不重写 LangChainRuntime、拆分 QueryService、删除 Coordinator、再次修改 schema/前端/配置、合并结构资料或改变业务格式。不以原设计任务「只改文档」的范围掩盖实际整包变更，也不提前启动 core 开发。

## 2. 现有代码到目标职责的映射

| 当前位置 | 后续处理与目标 | 阶段 | 保留/退出条件 |
| --- | --- | --- | --- |
| `query/service.py` | 调用上下文构建/能力注册和 RuntimeFactory，收敛路径配置输入；保留会话、互斥、取消、事件及终态管理 | 1 | 先增加 V2 接入边界，不复制第二套 QueryService；V1 分支继续工作 |
| `query/runtime.py` | 演进为 context 型公共 Runtime 协议；沿用 RuntimeResult、错误与结果归一化 | 1 | 旧 ask/ask_prepared 由单一 LegacyAdapter 转换，不逼旧 Runtime 同步重写 |
| `query/profile.py` | 复用现有模块，冻结 required/optional/disabled 与资料策略，增加 core/legacy 注册 | 1 | 旧 profile ID 和开关仍可显式进入兼容入口；不改写历史 ID |
| `query/materials.py` | 演进 MaterialSource/Manifest，明确授权、状态、版本、排除根 | 1 | 旧 available/directory 等字段在边界转换；禁止同时维护两套新的权威类型 |
| 目标 `query/context.py` | 新增 RunContext/QueryScope，复用 ProjectContext | 1 | 不复制工程注册、配置加载或管理数据库 |
| 目标 `query/capabilities.py` | 提供者协议、能力注册、预检与绑定；只实现 FileCapability | 1 | 不依赖具体结构索引，不包装 Coordinator |
| `query/langchain_agent/runtime.py` | 作为 V1 原实现保留；目标 `query/agent/direct.py` 独立实现最小模型循环 | 1 | Direct 功能与对照验收前禁止删除旧链；不能把旧 runtime 整体搬进 direct |
| `query/langchain_agent/coordinator.py` | 保留为 Legacy 调查策略 | 1 | 不被 core 导入；是否退出由未来独立对照和确认决定 |
| `query/langchain_agent/investigation.py` | 保留 Legacy 待办、补读与事项状态 | 1/后续 | 不成为 Direct 必需协议；后续按收益处理，不预先删除 |
| `query/langchain_agent/preparation.py` | V1 工具预检保留；可复用状态语义，V2 注册层不调用它加载所有后端 | 1 | core 禁止实例化 ContractIndex/SearchSnapshot 或探测其依赖 |
| `query/workspace.py` | 增加按 RunContext 授权准备的路径，隔离活跃 workspace；旧 mode 挂载仅供 Legacy | 1、2 | core 不先建 combined 再靠提示隐藏资料；不生成 AST/地图 |
| `query/langchain_agent/files.py` | 抽取必要的无索引读取/Glob/Grep 到 `query/tools/files.py`，工具依赖通用合同 | 1 | 保留 V1 行为和相关回归；不把 codeintel.retrieval 常量依赖带进 core |
| `query/langchain_agent/context.py` | 会话持久化与原文过期逻辑按需要复用到 `query/agent/session.py` | 1 | 不携带协调器状态；先审查现有会话格式，不默认混用历史消息 |
| `query/langchain_agent/evidence.py`、`verification.py`、`answering.py` | V1 facts/plain 逻辑保留；V2 只复用框架无关的引用/原文验证职责 | 1/后续 | Direct 不默认引入 facts 收尾或额外模型整理；语义证明仍为 false |
| `query/langchain_agent/contracts.py`、`contract_answer.py`、`business_answer.py` | Legacy 保留；后续 ContractCapability 仅适配公开只读结果 | 后续 | 不把自动补查/规则收尾塞入通用 Capability |
| `query/navigation.py` | 按配置提供工程职责与授权仓库导航 | 1 | 不要求索引，不把配置职责当源码连接证明 |
| `query/traces.py` | 复用工程级 RunTraceArchive；增加上下文/Profile/证据元数据 | 1 | 历史缺失不补造，工作区清理不影响归档 |
| `query/claude_runtime.py` | 保留显式 CLI 兼容与历史评测 | 1/后续 | 不映射成 core 或伪称 coordinated；删除需单独决定 |
| `platform/projects.py` | 继续唯一工程身份与目录归属 | 1 | 不添加第二套工程管理模型 |
| `platform/ast_material.py` | 当前解耦投影保留；后续将 AST 状态/版本 reader 收敛到 CodeIntel 公共接口 | 3 | core 不构造 AST 投影；迁移后 platform 不承载 AST 生命周期 |
| `knowledge/baseline.py` | 后续在公开能力中统一为 Business Context 语义，复用已有 Markdown/元数据 | 2 | 不强制重命名存储表/重写内容；legacy baseline 兼容读取保留 |
| `requirement/` | 继续管理需求原文，提供授权文件来源 | 1、2 | core 只消费原文，不引入管理服务或评分资料 |
| `codeintel/map.py` | 保留为结构导航视图及原有生成入口 | 3 | 不合并历史来源；core 禁止读取生成地图 |
| `codeintel/analysis.py` | 复用 JavaIndexer.ingest/resolve_edges 等显式解析能力 | 3 | 查询工具不调用 ingest/resolve_edges；未支持关系不能猜测 |
| `evaluation/ast_service.py`、`evaluation/ast_docs.py` | 将 AST 生成、状态、版本生命周期及渲染职责调整到 CodeIntel | 3 | 同步拆出 resolve_project_sources 等生成依赖，避免 CodeIntel 反向依赖 evaluation.runner |
| `codeintel/hybrid/` | 保留显式准备及可选 SearchCode，后续 CodeSearchCapability 调公开只读入口 | 后续 | 零索引 core 不导入 numpy/ONNX/tokenizers；问答不下载模型 |
| `codeintel/contracts/` | 保留索引准备与只读关系/字段查询；后续 ContractCapability 适配 | 后续 | 不在领域层生成业务答案，未解析/过期关系保留缺口 |
| `evaluation/harness.py`、`runner.py`、`service.py` | 新对照只调用公共 Runtime/上下文构建边界，旧冻结批次入口保留 | 1/后续 | 评测不是 Query 依赖；不得改写旧答案、Profile、版本或评分 |
| `api/server.py`、`cli.py` | 装配 ProjectContext、提供者、RuntimeFactory 和传输参数 | 1、3 | 阶段 1 仅向 QueryService 注入 ProjectContext，不改路由/协议，不在路由中实现能力选择或循环 |
| `storage/schema.py`、前端问答页 | 已提交 V1 快照存储/展示配套；阶段 1 优先复用执行快照/usage 扩展元数据 | 后续按需 | 本轮收尾不再迁移数据库或改页面；不把 UI 重做作为 core 前置 |

目标目录不存在不是问题；接口实现与依赖边界优先于目录搬家。退出旧入口前必须检查真实调用方，不保留无用 re-export，也不因追求「新目录整齐」批量移动代码。

## 3. 实施阶段与闸门

### 阶段 0：本次设计冻结

三份 V2 文档、现行规范同步、第 5 节走查与第 6 节设计验收已完成。原三组既有测试共 15 项通过仅是基础回归；实际提交另含 V1 预备实现，因此还须补第 7 节专项测试与真实范围说明。阶段 0 不要求零索引 Agent 已实现，整包结论不再写成「7/7 全量验收通过」。

### 阶段 1：core-v1 基础主链（已实现）

最小实现只包含 **AgentProfile、RunContext、MaterialSource、简单 Capability 注册、FileTools、AgentRuntime**；直接复用既有工程、RuntimeResult、会话及 Trace 基础设施。完整合同保留作后续边界，不要求把所有类型/Protocol 一次落地，更不新增抽象层。本节验收结果见第 8 节。

顺序固定为：

1. 演进已有 Profile/资料类型，新增复用 ProjectContext 的 RunContext；按工程配置展开预期仓库，逐一核验后生成授权快照。
2. 增加不触发 AST 投影/索引探测的 core 工作区路径；落实 ready 根、嵌套排除、链接检查和活跃运行隔离。
3. 用简单注册绑定唯一一套 Read／Glob／Grep；不实现业务、AST、Hybrid 或 Contract 提供者或空壳。
4. 实现一个 DirectRuntime 和必要的会话恢复/重建，用配置模型自主选择文件工具；不继承旧 Runtime 的调查逻辑，不搬入 Coordinator、Investigation 或业务收尾。
5. 接入现有 QueryService，保留 RuntimeResult、执行快照、原文引用、完整事件、停止和终态。旧入口通过最薄边界适配保留，不要求先统一 Legacy 全部能力合同。
6. 完成基础源码问答、跨仓库文件搜索、会话、授权、Trace、停止回归，再按用户允许的范围验证真实模型。

**缺失仓库为阶段 1 必修、必验**：预期集合必须来自配置和规范化 scope，不能来自 `repos/` 枚举。`prepare_core` 现在先核验所有选中仓库，任一缺失或不可读即失败，错误列出仓库 ID；模型调用为零，失败 Run、execution 和 Trace 保留。显式仅选存在的 channel/decision 可以运行；未登记目录不加入。测试见 `test_core_context.py`、`test_core_workspace.py`、`test_core_query_service.py`。

其他必验项：无可选解析/检索依赖也可导入 core；禁用能力探测函数「调用即失败」仍可问答；模型可自主跨授权仓库搜索；Profile/范围/实际工具和资料变化重建会话；取消与失败保留事件。固定结果测试提供者验证注册不改 Direct 循环，不扩展真实业务/结构能力。

**已实现（2026-11-13）**：`query/context.py`、`profile.CORE`、`materials.for_core`、`workspace.prepare_core`、`query/tools/files.py`、`query/capabilities.py`、`query/agent/direct.py`、`query/agent/session.py` 及 QueryService core 分支。当时默认仍为 Legacy，`BUSINESS_CODE_AGENT_PROFILE=core-v1` 显式启用；2026-11-15 起在线默认已切换为 Pi（见第 10 节）。网页默认 `mode=combined` 映射为 `core`，显式旧模式拒绝。

**延后项**：完整运行期能力失效联动、全部结构版本联动、复杂 Evidence 生命周期、多级依赖/重绑定、AST/Contract 专属授权、高级检索动态状态管理，随阶段 2/3/后续对应能力实施。core 的路径授权、required 明确失败、基本原文引用和会话边界未因延后这些机制而省略。

阶段 1 不自动切换生产默认，也不删除旧链。切换需单独确认，不能靠重命名 standard-v1 绕过新授权合同。真实模型答案验收因供应商配额未完成，见第 8 节。

### 阶段 2：Business Context

只增加资料授权/版本、阅读指导和业务能力提供者，复用原内容格式和 File 工具。验证术语消歧、提款/额度语义、系统职责与源码冲突的区分；同模型配对 core 与业务组合，正常源码/README/需求不得删减。实现可用与业务语义收益分别验收。

### 阶段 3：AST 与结构导航

在阶段 2.5 的 Direct 答案覆盖验证之后安排。将 AST 生命周期从 evaluation 调整到 CodeIntel，保留已有版本目录、显式生成和历史引用。提供只读的状态/版本 reader 及 AstCapability，复用已有解析结果和 Code Map 导航视图。

解析器能力范围如实暴露：调用/继承/实现关系能返回哪些由真实数据决定；消息消费者和任务未被索引时，工具交付缺口，由模型用现有文件工具继续。符号库存在不等于已实现可编程查询，更不表示完整业务链已证明。

### 后续：Hybrid / Contract 与旧实现收敛

分别增加提供者及公开只读适配，不改变 Direct 循环。与同模型、同题、相同正常资料条件的 core/业务/AST 组合分开对照。记录索引建设、维护和查询成本，不能把 V1 补查策略包装成检索收益。

仅在 Direct 通过功能验收、完整对照有可解释结果、回退路径可用且用户确认后，才讨论废弃 Coordinator/旧 Runtime。评测结果不能自动触发代码删除。

## 4. 兼容、会话与回退

### Profile 名称映射

| 当前名称/入口 | V2 处理 | 禁止行为 |
| --- | --- | --- |
| `standard-v1` | 当前是未开放占位；未来 core 独立定义并拒绝占位配置误用 | 不把其宽资料/optional 搜索自动继承为 core |
| `coordinated-legacy-v1` | 作为既有标准 V1 入口保留；`legacy-v1` 显式适配相同 combined/coordinated/plain 配置 | 不重写旧运行的 profileId |
| basic/tracked、facts/contracts、旧 mode | 保留 V1 显式兼容入口与实际设置 | 不把不同对照压成同一个 legacy-v1 标签 |
| `claude-compat-v1` / Claude CLI | 原显式兼容 | 不冒称 LangChain Direct |
| `core-v1` | 新零索引授权定义，阶段 1 才注册 | 不自动启用机器上的已有资料 |

### 回退步骤

1. 当前阶段没有运行时切换，回退不需要改生产配置。文档修订只按明确文件差异撤回，不恢复或清理用户已有生产改动。
2. 阶段 1 以后若 Direct 异常，先保存失败/取消 run、Trace 和停止原因；不在同一 run 内悄悄调用 Legacy。
3. 用户/管理员显式选择 `legacy-v1`（实现后）或仍可用的原 V1 入口，创建新运行。原始失败记录保留。
4. Profile/Runtime 改变使模型会话身份失效。可保留前端会话容器供查看，但不把 core/legacy 的旧模型消息和工具结果交叉恢复。
5. 复用原数据库和历史读取接口，不恢复旧数据库快照、不删除新 run、不改旧 AST 版本，不全局改写历史的 mode/Profile。

阶段 1 应优先使用已有 `execution_json`、`session_config_json`、`usage_json` 与工程 Trace 表达新元数据。若实现时证明必须迁移 schema，另行提出最小迁移与回退方案，不把本次设计当作数据库修改授权。

## 5. 五个接口级走查

以下为设计演算，示例仓库和业务词不代表已完成真实业务调查；没有执行模型、生成 AST 或创建业务资料。每个案例走同一 `Registry.prepare → RuntimeFactory.create → AgentRuntime.ask → RuntimeResult` 路径。

### 场景 1：只有源码，无任何预建索引

- **输入**：ProjectContext P，channel/middle/decision 三仓库，`core-v1`，无业务资料或索引。
- **装配**：QueryScope 展开三个仓库；三条 source ready；requirements missing；其余 disabled；FileCapability.status=ready，tools=(Read, Glob, Grep)。禁用提供者 status 调用次数为零。
- **接口路径**：模型 `Glob(pattern="repos/*", kind="directories")` → `Grep(pattern="withdraw", case_sensitive=False)` → `Read(path=命中路径, offset=命中行)`。读到跨仓库标识后由模型自行再 Grep/Read。
- **证据**：Glob/Grep 是定位；Read 的已交付源码行产生 source Evidence。答案说明确认实现和未找到的远端连接，不能因未命中断言不存在。
- **会话/记录**：保存 core 定义、三个根、工具 schema、引用与 Trace；同 scope/Profile 可复用经新鲜度核对的会话。
- **反例**：准备工作若调用 AstMaterial、SearchSnapshot 或 ContractIndex，即使没有产生索引也违反 core 零探测边界。
- **走查结论**：无索引前置，接口可连通；待阶段 1 真实实现与测试。

### 场景 2：加入 Business Context，理解提款/额度/系统职责

- **输入**：使用预留业务组合的测试定义，业务资料/能力 optional 且 ready；新增业务文档版本 B1。
- **装配**：新增 business_context MaterialSource；BusinessContextCapability 返回目录与「业务约定不代表实现」指导，tools=()；FileCapability 仍只注册一套三个工具。
- **接口路径**：模型 `Grep(pattern="提款|额度", path="knowledge/business-context")` → Read 对应语义原文 → 根据术语/职责线索选择源码 Grep/Read，而非程序自动安排固定流程。
- **证据**：B1 关于可用额度与审批额度的说法记 business_context；代码是否使用这些口径必须另有 source Evidence。系统职责配置只是导航。
- **会话/记录**：从 core 切换组合后重新建立模型会话；不能沿用 core 的原始 session，也不能修改以前 core 轮次的资料记录。
- **失效分支**：业务能力预检/绑定失败时，B1 的 root 置空并加入排除根，整批 File 工具重新绑定；运行中 B1 失效则通过 AvailabilityChange 进入共享 RunAccess，Read 也不能继续取得该正文。
- **反例**：业务文档描述提款完成流程，不足以断言某个服务已持久化/通知成功。
- **走查结论**：只改变资料与指导，Direct 循环不改；业务收益尚未验证。

### 场景 3：加入 AST，从 Service 找实现、消费者或任务

- **输入**：AST 组合测试定义，授权源码不变，手动已生成版本 A1 与当前源码匹配。
- **装配**：ast MaterialSource ready/version=A1；AstCapability.status 返回 ready/A1；tools 增加 FindCodeRelation，File 工具保留。
- **接口路径**：模型 `FindCodeRelation(query="某Service全限定名", relation="implementations")`，消歧后可查询 calls；工具返回支持的符号/边、版本和源码位置；模型主动 Read 两端及连接源码。
- **能力缺口**：若 A1 没有消息消费者/任务关系，返回 unsupported/unresolved；模型自行 Grep 事件名、监听注解或任务入口并 Read。没有注册的仓库必须记为范围缺口，不能出界。
- **证据**：结构边是 navigation；只有实际交付的连接原文支持有限技术关系，不保证运行顺序、完整状态机或业务成功。
- **会话/记录**：记录 A1；A2 在运行中出现不切换，下一轮重新预检并重建会话。
- **走查结论**：新增一个提供者/工具即可；关系未支持不会要求修改核心循环或隐式解析。

### 场景 4：同一查询使用业务知识和 AST

- **输入**：业务+AST 组合测试定义，B1/A1 同属 P，且均在授权范围。
- **装配**：BusinessContextCapability 增指导、AstCapability 增结构工具；注册层合并且拒绝重复工具名，仍使用同一个 DirectRuntime。
- **接口路径**：Read B1 的业务术语 → FindCodeRelation 查询 A1 符号/关系 → Read channel/middle 的源码连接；所有步骤由模型选择。
- **证据与答案**：`E-business` 说明「业务约定如何定义额度」；关系 data/A1 说明「索引发现的候选调用」；`E-source-1/2` 说明「本次核对的参数/条件」。答案逐层描述，三者冲突分别列出，不把一张图当业务流程已完成。
- **校验**：验证 Evidence 属于 P、B1/A1/当前源码、引用行确已交付；仍记录 semanticProof=false，不能据来源齐全自动通过任意业务断言。
- **会话/记录**：指纹包含 B1/A1 和实际工具，任意版本变化不复用旧模型会话；历史轮次不补用新版本。
- **走查结论**：多来源通过同一证据合同汇合，不存在业务调查链与 AST 调查链各自运行再拼答案。

### 场景 5：索引未准备或过期

| 条件 | 预检/执行结果 | 后续行为 |
| --- | --- | --- |
| core，磁盘恰有 AST/Hybrid/Contract | disabled，不探测、不注册 | 仍只有 File；已有产物不改变指纹 |
| AST optional，A1 缺失 | missing，无结构工具/正文 | 同一 Profile 的 File 正常调查，Trace 写缺口 |
| AST optional，A1 过期 | stale，排除旧结构源 | File 读当前源码；不生成 A2、不允许 Read 绕过旧挂载 |
| AST required，A1 缺失/过期 | 配置失败，零模型调用 | 告知需显式准备或显式选别的 Profile；不偷偷降级 |
| optional A1 调用中变 stale | ToolResult.status=unavailable，availability_changes 指定 ast/A1 对应资料、state=stale | RunAccess 统一撤销结构访问；File 不能绕过读旧 AST；不换 Profile/版本 |
| required A1 调用中失效 | Runtime 失败，保留事件/已得结果 | 不把不完整调查标 completed，不自动回退 Legacy |
| Hybrid optional 依赖缺失 | unavailable，无 SearchCode | File 正常工作，不下载 ONNX 模型 |

- **会话**：optional 从 ready 到 stale/missing 或下一轮恢复为 ready，实际资料/工具集合改变，重新建立模型会话。之前的结构原文和业务摘要不能绕过新授权恢复。
- **权限**：缺索引不表示可以读取未授权仓库、换成其他工程索引或打开 disabled 的业务目录。File 的 ToolAccess 检查不仅看 AST 文件版本，还核对其对应源码新鲜度；旧 Evidence 失效后不再进入有效引用，原始 Trace 保留。
- **单次错误对照**：某个源码文件不存在时，unavailable 且 availability_changes 为空，模型可继续搜索；整个必需 source 根失效才产生材料级变化并使运行失败。
- **走查结论**：降级是原 Profile 内可用能力的收缩，不是换 Profile；required 与 optional 的结果明确不同。

## 6. 设计验收矩阵

「通过」在本阶段仅指接口闭合、设计可实施，不代表实现或收益通过。

| 编号 | 内容 | 文档检查点/走查证据 | 判定边界 |
| --- | --- | --- | --- |
| A01 | 总体架构 | 总体架构 §2、§3、§6 | Query、Agent、资料、工具、索引、Evaluation 职责与依赖明确 |
| **A02** | 零索引独立性 | 合同 §2、§5、§6；场景 1/5 | core 不探测禁用索引，不要求任何预建代码资料 |
| **A03** | Business Context 扩展 | 合同 §3、§7.3；场景 2 | 增加授权与指导，允许 tools=()，不改循环 |
| **A04** | AST 扩展 | 合同 §7.3；场景 3 | 版本化结构工具，未支持关系明确缺口，不改循环 |
| A05 | 统一工程上下文 | 合同 §2/3/9 | 复用 ProjectContext，无第二套工程管理 |
| **A06** | 执行配置 | 合同 §2/4；场景 5 | required/optional/disabled 与实际状态分离 |
| **A07** | 会话隔离 | 合同 §6/9；场景 2/4/5 | 工程归属拒绝，资料/范围/Profile/工具变化重建模型会话 |
| A08 | 证据边界 | 合同 §7/8；场景 4 | 业务、需求、结构与源码分开，格式通过不是语义证明 |
| A09 | 旧代码迁移 | 本文 §1/2/3 | 已有预备改动和主要职责均有映射，不只列目标目录 |
| A10 | 向后兼容 | 本文 §4 | 保留旧运行链和历史，显式新运行回退，不自动覆盖 |
| **A11** | 扩展独立性 | 合同 §3/5/7.3/10；场景 2—4 | 提供者/注册/Profile 变化即可，不添加核心能力分支 |
| A12 | 文档一致性 | AGENTS、当前架构、指导方向和文档索引 | V1 默认与 V2 目标分别适用，不把未来规范写成现状 |

## 7. 本次验证与交付状态

**第一步架构设计：通过。第一步整包提交：有条件通过，不是全量验收通过。** 设计走查、V1 预备功能回归和未来 core 验收分别记录；不再以文档交付项全部完成代表整包通过。

### 本地回归命令

2026-11-13 使用项目 `.venv/bin/python`（Python 3.14.3）。原三组回归保留，另外补运行配置专项及既有 coordinated 回归：

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_query_profiles.py'
.venv/bin/python -m unittest discover -s tests -p 'test_query_runtime_service.py'
.venv/bin/python -m unittest discover -s tests -p 'test_retrieval_coordinator.py'
.venv/bin/python -m unittest discover -s tests -p 'test_package_boundaries.py'
.venv/bin/python -m unittest discover -s tests -p 'test_import_paths.py'
```

| 检查 | 本轮本地结果与证明范围 |
| --- | --- |
| [test_query_profiles.py](../../tests/test_query_profiles.py) | 17 项通过，其中 16 项配置/清单/预检/快照验证，1 项仅复现缺失仓库被省略的现状，不代表需求通过 |
| [test_query_runtime_service.py](../../tests/test_query_runtime_service.py) | 15 项通过：原 8 项 + 新增 7 项会话、快照和真实预检接入测试；模型使用桩 |
| [test_retrieval_coordinator.py](../../tests/test_retrieval_coordinator.py) | 16 项通过；验证 V1 自动补读、契约调查、去重等既有行为，不代表 Direct |
| test_package_boundaries.py / test_import_paths.py | 5 + 2 项通过；仅现有包边界与导入路径 |

合计 **55 项本地测试通过**，其中新增 24 项包含 1 项已知缺口的现状刻画；不能表述为 24 项新需求全部达标。原记录的 15 项旧回归不足以验收提前提交的新机制。没有新增远端 CI 结果，也不声称用户已独立复跑。

### 六项专项要求的证据与限制

| 要求 | 本轮证据 | 边界 |
| --- | --- | --- |
| 无 Hybrid Index，optional 不阻断文件问答 | 配置专项调用真实 Read/Glob/Grep；服务专项使用真实 LangChainRuntime + 固定响应模型完成文件问答 | 无网络模型；未证明真实问题答题收益 |
| 已有 Contract Index 但 Profile 禁用 | 在索引路径放置哨兵文件，将文件探测/ContractIndex 构造替换为调用即失败；仅注册文件工具 | 验证 disabled 分支完全不碰该产物，不声称检验了真实索引内容 |
| 多仓库缺一个，明确缺哪个 | 阶段 1 已由 `prepare_core` 逐一核验，`test_core_workspace.py`/`test_core_query_service.py` 覆盖缺失与不可读；模型未调用，Run/execution/Trace 记录仓库 ID | 阶段 0 的现状刻画保留在 `test_query_profiles.py`，不再代表当前行为 |
| 同会话切换 Profile 或工具集合不复用模型会话 | 服务专项核对 session_id、session_config_json，覆盖 Profile、工具名/策略/状态/版本，以及模型设置和资料集合变化；相同身份仍复用 | 工具变化用合成状态隔离测试服务比较；不证明 V2 完整 schema/指纹已实现 |
| required 不可用保存 failed 与 execution | 真实 prepare_tools 返回 required/unavailable，require_tools 阻止模型；检查 DB、回调、磁盘 Trace、错误及无助手消息 | 覆盖返回预检状态后的失败；V1 的 prepare 自身抛出异常仍早于 run 创建，不在本项通过范围，阶段 1 应按 V2 顺序处理 |
| 旧 coordinated 保持行为 | 服务专项保留 combined/coordinated/plain、Investigation 与 workflow 来源自动 Read，另跑原 coordinated 16 项 | 局部兼容回归，不是所有 V1 功能全量验收 |

### 收尾结论与后续边界

- 实际提交范围和已实现/已设计/待实现状态已修正，三份 V2 文档、当前架构、AGENTS、指导方向和索引保持一致；不回滚已提交 V1 预备实现。
- A01—A12 与五场景按设计范围通过。完整合同保留，但核心阶段只落实最小类型、注册和文件 Runtime；运行期跨能力失效、结构联动和复杂 Evidence 生命周期后置，不增加抽象层。
- 阶段 0 收尾时缺失仓库核验尚未修复；阶段 1 已修复并由第 8 节专项测试覆盖。`core-v1` 与 DirectRuntime 已实现，V2 Legacy 适配仍未实现。
- 阶段 0 收尾时仅变更规范文档与测试；阶段 1 的实现、测试和真实模型尝试见第 8 节。
- 阶段 0 的 55 项本地回归、7 份文档链接检查及 `git diff --check` 通过；没有据本地回归将整包提升为无条件通过。

阶段 0 收尾后已进入阶段 1，范围仅为源码问答、跨仓库搜索、会话、授权、Trace 与停止；Business Context、AST、Hybrid/Contract 未同时接入。

## 8. 阶段 1 实现与验证（2026-11-13）

### 实现范围

| 提交 | 内容 |
| --- | --- |
| `8de687c` | `core-v1` Profile、RunContext、`MaterialManifest.for_core` 与配置仓库清单；缺失仓库保留为独立条目 |
| `231bbf0` | `WorkspaceManager.prepare_core` 每轮隔离工作区、`query/tools/files.py`、`query/capabilities.py` |
| `f96f23b` | `DirectAgentRuntime`、项目级 Direct 会话、取消与引用核对；不导入 Coordinator/Investigation/CodeIntel |
| `863f765` | QueryService core 分支：先创建 Run/Trace，再准备资料；失败/取消/成功持久化；API 组合根注入 ProjectContext |
| 本轮修复 | Direct 每次模型请求写入 `RunTraceArchive.calls`（请求消息、工具定义、响应、耗时）；失败/取消保留；引用改为按已交付行区间核验 |

`core-v1` 使用源码 required、需求 optional、File required；Business Context、Code Map、AST、Hybrid、Contract 均 disabled，且 core 路径不构造 `AstMaterial`、不探测索引。Legacy 查询路径保持原有行为。

### 验收矩阵

| 编号 | 本地证据 | 结论 |
| --- | --- | --- |
| C01 零预建索引 | core 服务测试替换 `AstMaterial`/`WorkspaceManager.ensure` 为调用即失败仍成功；工作区无 knowledge/AST/索引目录；Direct 导入子进程不含 CodeIntel | 通过（单元/集成） |
| C02 多仓库问答 | Direct 模拟模型跨 channel/decision 执行 Glob/Grep/Read | 通过（模拟模型） |
| C03 缺失仓库 | `prepare_core` 拒绝 middle，模型零调用；失败 Run/execution/Trace 含仓库 ID | 通过 |
| C04 独立调查 | Direct 导入子进程不含 `query.langchain_agent`/Coordinator/Investigation | 通过 |
| C05 工具扩展 | 附加只读 Ping 提供者无需修改 Direct 循环 | 通过 |
| C06 会话隔离 | core scope/profile 变化及 Legacy 工具/设置变化均重建会话 | 通过 |
| C07 资料隔离 | core 工作区无业务/AST/Code Map；Read 拒绝未授权路径 | 通过 |
| C08 原文依据 | Read 返回真实路径/行号；引用单行与区间按已交付范围核验，仅文件名的引用只标记 `fileRead`；`semanticProof=false` | 通过（格式与覆盖核对，不等于语义正确） |
| C09 运行记录 | core 成功、缺仓库失败、取消均有 Run/Event/Trace；`trace.calls` 保留每次模型请求消息、工具定义、响应与耗时 | 通过 |
| C10 向后兼容 | Legacy 15 项、coordinated 16 项、问答 API/Trace/工作区 31 项本地回归通过 | 通过（本地） |

### 本地测试

阶段 1 新增 5 个测试模块，共 39 项：

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_core_context.py'
.venv/bin/python -m unittest discover -s tests -p 'test_core_workspace.py'
.venv/bin/python -m unittest discover -s tests -p 'test_core_file_tools.py'
.venv/bin/python -m unittest discover -s tests -p 'test_direct_runtime.py'
.venv/bin/python -m unittest discover -s tests -p 'test_core_query_service.py'
```

连同既有 55 项配置/会话/协调器/包边界回归，共 94 项通过；另跑 query API/进度/资料、Trace、工作区、工程上下文与多应用流程 31 项通过。前端 `npm test` 18 项通过，其中包含三次模型调用在轨迹时间线上的模型步骤断言。失败路径产生的 `WorkspaceError`/`RuntimeError` 日志为预期。测试使用临时目录和模拟模型，未生成工程索引。

### 2026-11-13 验收修复

- **模型调用轨迹**：原 Direct 只发送 `model` 摘要事件，`RunTraceArchive.calls` 为空，轨迹页显示 0 次模型请求。现 `RunTraceArchive.record_call` 按 `attempt` upsert；Direct 在每次模型调用前写入请求消息与 OpenAI 工具定义，调用后补响应、用量和耗时。请求在调用前即落盘，第二次调用抛错时第一次记录保留，当前调用标记 `failed`；取消时标记 `cancelled`。`public_message` 与 Legacy `TraceRecorder` 共用，不新增第三种轨迹格式。`QueryService` 只对 Direct Runtime 注入该回调。
- **引用核验**：`verify_answer` 不再把无行号引用视为已确认，只记录 `fileRead=true`；单行引用核对该行已交付；`文件:起-止` 必须被已读区间完整覆盖，分段相邻读取可合并覆盖；`unconfirmed` 保留引用区间文本。仍声明 `semanticProof=false`。
- **新增回归**：三次模型调用的请求/工具/响应/耗时、第二次调用失败的失败记录、工具触发取消后的完成调用保留、进行中模型调用的取消记录、引用单行/跨读区间/越界/仅文件名，以及前端轨迹时间线三条模型步骤。
- **未完成**：真实模型额度恢复前的五类问答仍未执行；浏览器人工验收未执行。以上不能由模拟模型测试替代。

### 真实模型尝试与未完成项

对 `validation-projects/complex-loan` 使用现有 `.env` 发起真实 core 请求。第一次在宿主机 HTTP 客户端初始化阶段被 `NO_PROXY=[::1]` 解析错误阻塞；仅为该验证进程清理代理变量后，供应商返回 **403 insufficient_quota（免费额度耗尽）**，未产生模型答案。core 已按失败路径保存 Run、事件和 Trace；未修改生产环境或模型配置。

因此：

- 真实模型的五类问题（代码定位、方法逻辑、跨工程调用、配置/数据访问、连续追问）**尚未完成**，不能宣称答案质量或业务收益。
- 未启动浏览器页面做 core 人工验收；API/服务层回归通过不等于前端实机通过。
- 生产默认仍为 Legacy；切换需在配额可用后完成真实模型验收并单独确认。

真实模型 core 验收仍待配额；随后只进入阶段 2 Business Context 工程实现（见第 9 节），不同时接入 AST/Hybrid/Contract，也不回退本次实现。

## 9. 阶段 2 Business Context 工程实现（2026-11-13）

### 实现范围

| 位置 | 内容 |
| --- | --- |
| `query/profile.py` | 新增显式 `core-business-v1`：source required、requirements/business_context optional、File required，其余能力 disabled |
| `query/materials.py` | `for_core` 可选生成 `business_context` 条目；内容清单指纹 `sha256:`；缺失/空目录为 missing，不阻断 |
| `query/workspace.py` | `prepare_core` 按 Profile 可选挂载工程业务资料到 `knowledge/business-context`；`core-v1` 不创建/探测该目录 |
| `query/tools/files.py` | Read/Glob/Grep 的授权根仍完全来自本轮 MaterialManifest，仅增加 `business_context` 类型 |
| `query/capabilities.py` | 新增无工具 `BusinessContextCapability`，提供 optional 状态和阅读指导；索引能力继续 disabled |
| `query/service.py` | 按 Profile 决定是否挂载业务资料；执行快照、会话配置和 workspace 摘要记录业务资料状态/版本 |
| `query/agent/direct.py` | 模型—工具调查循环未改动；引用核验仅增加 `business_context/source` 来源类型标注 |

业务资料根来源：已注册工程使用组合根注入的 `ProjectContext.business_context_root`；兼容工程继续使用项目配置的 `knowledge.businessContextRoot`（历史 `baselineRoot` 仅作读取兼容），同一工程不并行创建第二套 Business Context。

### 验收矩阵

| 编号 | 本地证据 | 结论 |
| --- | --- | --- |
| B01 Profile 独立 | `core-v1` 不生成业务条目且无法读取；`core-business-v1` 独立生成可选条目 | 通过 |
| B02 可选业务资料 | 删除业务目录后服务仍完成源码问答，状态为 optional/missing，File 仍 ready | 通过 |
| B03 资料访问 | Business Profile 通过 Glob/Grep/Read 读取业务文档 | 通过 |
| B04 资料隔离 | Core Profile 对同一路径返回“不在本轮授权资料范围内” | 通过 |
| B05 不引入索引依赖 | 两种 Profile 的 AST/Code Map/Hybrid/Contract 均为 disabled；Core 工作区无生成索引 | 通过 |
| B06 Runtime 不变 | 两种 Profile 均使用同一 `DirectAgentRuntime`，无新调查循环 | 通过 |
| B07 会话版本 | 业务文档内容指纹变化后重建会话；内容未变复用会话 | 通过 |
| B08 证据区分 | 阅读指导声明业务资料非实现证据；引用条目带 `kind`，`semanticProof=false` | 通过（格式/来源区分，不是语义证明） |
| B09 Trace | 执行快照含业务资料状态/版本；工具事件记录实际读取；引用与源码后续调查可回看 | 通过 |
| B10 Legacy 回归 | 既有配置/会话/协调器/API 回归通过 | 通过（本地） |
| B11 多工程授权 | 业务文档提到未授权仓库时，Read 该仓库源码仍被拒绝 | 通过 |
| B12 工具扩展 | 业务能力不注册新工具；工具集合仍为 Read/Glob/Grep | 通过 |

### 本地测试

阶段 2 新增 2 个测试模块 12 项，另在 `test_core_query_service.py` 增加 `core-business-v1` Runtime 选择用例；阶段 1 五个模块现共 55 项（含该用例、上下文预算回归、重复读取、证据恢复、未完成状态与收尾回归）：

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_business_context_capability.py'
.venv/bin/python -m unittest discover -s tests -p 'test_core_business_query.py'
.venv/bin/python -m unittest discover -s tests -p 'test_core_query_service.py'
```

收尾改造后重跑：55 项阶段 1 模块、12 项阶段 2 新模块、既有 55 项配置/会话/协调器/包边界回归、31 项 API/Trace/工作区/工程上下文回归，共 153 项后端相关测试通过；前端 `npm test` 18 项通过（Trace `no_progress` 标签改动后已跑）。测试使用临时目录、模拟模型和本地业务文档，未生成 AST、Code Map、Hybrid 或 Contract 索引。

### 2026-11-14 重复补读修复

真实长题运行暴露出上下文压缩后的重复补读：修复前同一道“还款/额度恢复”题在 230 次调用中执行 194 次 Read，其中 138 次为完整重复、25 次部分重叠，只有 31 次新内容。

修复：

- `ReadLedger` 记录本轮已成功交付的文件与行区间，跨 `compact_messages` 保留；同一区间再次读取返回 `cached: true`，不重复返回正文。
- 压缩退役的 Read 结果保留文件名与行区间，不再使用无来源的通用占位符。
- `usage.cachedReads` 与 Trace 工具输出记录缓存命中。
- 新增 4 项回归：相同区间、被覆盖的子区间、未覆盖新区间、压缩元数据。

复验中同一题 300 秒有界运行的 46 次 Read 有 9 次被缓存拦截、30 次为新内容，平均重叠率从 0.79 降至 0.23；未再出现原地循环，但因显式时间上限停止，未取得最终答案。该修复不改变 Profile 授权、工具集合或业务资料边界。

### 2026-11-14 证据恢复与未完成状态修复

复核发现两处残留：

- 原文被压缩移除后，重复 Read 仍只返回 `cached: true` 而不含正文，模型无法重新获得证据；直接再次读取被拦截后可能继续循环。
- 有界运行达到调用次数或总时长上限时仍以 `completed` 记录，报告和评测会把限制停止当成正常答案。

修复：

- `ReadLedger.observe` 在每次模型调用前核对有界上下文，区分原文仍在上下文与已被压缩移除。
- 原文仍在上下文时返回可引用提示，不重复正文；已被压缩时按文件允许有限次（2 次/文件，且整轮最多 8 次）恢复原文，恢复后的正文仍参与引用行号核验。
- 恢复额度用尽后返回 `evidenceGap`，要求模型基于已有证据作答并声明无法复核，不再返回无内容的占位结果。
- 会话恢复时，带正文的恢复结果仍按文件修改时间检查失效；仅无正文的缓存提示跳过失效检查。
- Direct 达到显式限制返回 `status=incomplete`、`stopReason=budget`，进行中的模型调用记为 `incomplete`；`QueryService`、数据库、Trace、SSE 与前端均区分 `incomplete`，不再显示为已完成。
- `usage` 增加 `recoveredReads` 与 `evidenceGaps`。

新增 6 项回归：仍在上下文的已读提示、压缩后的受控恢复与用尽缺口、单文件与整轮恢复上限、运行时读取→压缩→再次请求恢复原文、调用上限返回未完成、时长上限将进行中调用记为未完成；服务层验证 `incomplete` 持久化及 Trace/SSE 事件。以上为本地模拟模型测试，不替代真实模型复验；修复后再次探测供应商仍返回 403 insufficient_quota，“还款/额度恢复”题的真实模型复验尚未发起。

### 2026-11-14 长调查收尾与 A/B 试点

16 题对照启动后，首题 `native-lifecycle` 在 64k/80 次/520 秒内两版均未形成答案：Business 80 次调用、62 次 Read、36 次读同一文件、26 次证据缺口、402 秒；Core 53 次调用、94 次工具、43 次 Read、520 秒。根因不是检索失败，而是：

- 运行时没有预算感知或答题收尾点，模型到硬上限才停止；
- 64k 压缩移除行号证据，模型为确认引用反复读取同一文件；
- 恢复额度用尽后的缺口提示没有强制收尾；
- 系统提示缺少“证据足够即作答”的约束。

修复：

- `DirectAgentRuntime` 统计连续无新增证据的 Read（缓存、恢复、缺口均计入）；达到 6 次注入停止提示，达到 10 次或预算临近时进入收尾。
- 预算收尾在剩余调用 ≤2 或剩余时间 ≤ `min(200, 总时长 × 40%)` 时触发；执行一次不带工具的最终答题调用，附已读文件/行区间索引和明确的“立即作答”指令。
- 最终调用使用独立请求超时（最多 200 秒）、输出上限 3072 tokens；收尾超时或异常返回 `incomplete`，不得记为 failed。
- 最终答案保留在 Run/Trace/前端，状态 `incomplete`、`stopReason=budget|no_progress`，并提示“已保留部分答案”。

修复后子集复验（64k，同模型）：

| 题目 | Business | Core |
| --- | --- | --- |
| native-lifecycle | incomplete，63 次调用，418.7 秒，约 2900 字部分答案 | incomplete（no_progress），41 次调用，322.8 秒，约 2500 字部分答案 |
| v2-strategy-code | completed，10 次调用，131.8 秒 | incomplete，38 次调用，383.4 秒，部分答案 |
| biz-repaid-limit | completed，14 次调用，250.5 秒 | incomplete，29 次调用，441.8 秒，部分答案 |

收尾机制保证不再出现“到上限仍无答案”，但宽泛题仍只能得到部分答案；正常问题仍可能需要 2–7 分钟，模型存在明显过度调查。生产默认是否增加更早的“证据足够即收尾”检查，仍需单独决定，不能用本次收尾机制替代问答效率验收。

新增 4 项 Direct 回归：停滞收尾、停滞提示不强制结束、预算预留最后一次无工具答题、空收尾回退；服务层补充部分答案和 Trace 工具列表断言。前端 Trace 增加 `no_progress` 显示。

### 2026-11-14 预算收紧 8 题子集

预算改为 40 次调用/300 秒/600,000 字符上下文，普通与收尾输出上限均设为 131,072 tokens（`glm-5.3-flash` 规格为 1M token 上下文、128K token 输出）。选择 8 题（5 道原生题 + `v2-strategy-code` + `biz-repaid-limit` + `biz-same-credit`），两版本共 16 次运行。

供应商账号并发上限实测为 1：3 路和 2 路均大量返回 `429 code 3008 user concurrency limit exceeded`（2 路批次 15/16 失败），最终改为单路顺序执行；失败批次不纳入结果。

- 7 次 `completed`、9 次 `incomplete`；9 次未完成全部为 `stopReason=budget`，没有 `no_progress`。
- 16 次运行的完整重复读取、证据恢复和证据缺口均为 0，`peakStalledReads=0`；600k 上下文下没有发生压缩，证据恢复不再是当前瓶颈。
- Business 4/8 完成、Core 3/8 完成；Business 合计 166 次调用/1511.6 秒，Core 196 次/1691.4 秒。只有 8 题配对，不能据此宣称业务知识收益。
- 引用确认：Business 167/197，Core 133/164；未确认项主要来自截断路径、缺少行号和路径未匹配到已读文件。
- 9 次未完成的最后一次调用均为不带工具的收尾答题，答案 2041–4392 字；7 次完成由模型自行结束，答案 2721–6934 字。
- 收尾机制保证限制停止不再无答案或伪报完成，但模型仍会持续调查到预算预留点；当前共同瓶颈是缺少“证据足够”判断，而不是证据恢复。

P1 修复：`RunTraceArchive.finish` 将 `usage.stopReason` 规范化到顶层，`TraceDrawer` 通过 `traceStopReason` 兼容旧的 `run.usage` 位置；引用解析支持截断路径（含 `Abstract...Processor.java`）并修正短扩展名边界（`.class` 不再匹配 `.c`），仅在唯一匹配到已读文件时解析。对 8 题 16 次运行的答案离线复算：引用 361→352、确认 300→318；核心/直接相关 41 项回归通过。

### 2026-11-14 充分性停止评估

对 8 题 16 次运行按修复后的引用解析做离线测算：318 条确认引用中，44.3% 来自调用进度 50% 之后首次读取的文件，15.7% 来自 75% 之后；全部引用证据到齐的中位时刻为 96% 调用进度，到齐后约 57 秒结束。直接硬停止会截断约四成最终引用证据，因此不采用“已读足够/无新增即停”。

下一步只做实验性“答案先行检查点”：在约 50% 调查预算处生成“结论—已确认依据—剩余缺口”草稿，有明确结论且引用可核验时接受，否则剩余预算只补列出的缺口；检查点在 `DirectAgentRuntime` 内完成，不新增规划器或协调器。验证方案为 8 题两版本的 A（当前）/B（仅 Prompt）/C（检查点 + 缺口定向补读）对照，比较完成率、调用/耗时、确认引用和人工复核覆盖；通过前不改生产默认，不进入 AST。2026-11-15 调整为先用 3 道退化题做 A/B 两方案两重复，结果见下节。

### 2026-11-14 搜索工具升级（阶段 2.5 Commit 1）

- `CoreFiles.grep` 新增 `output_mode=matches|files`：`files` 按文件聚合 `matches` 与 `sampleLines`（最多 5 个行号），按虚拟路径排序并分页；`matches` 保持原结构，新增可选 `context`（最多前后 5 行）。
- `path` 指向子目录或单个文件时直接从该授权位置开始遍历，不再从整个仓库扫描后过滤；绝对路径、`..`、未授权和忽略目录继续拒绝，多仓库授权不变。
- 新增 4 项工具回归：文件级聚合分页、子目录/单文件扫描与授权、匹配上下文、非法 `output_mode`；核心、直接和业务相关 73 项测试通过。
- 真实冒烟（`biz-repaid-limit`，core-v1，20 次/180 秒）：10 次调用、158.8 秒、`incomplete`；模型使用 Glob+Read，没有调用新的 Grep 模式。仅增加工具描述不足以改变调查习惯，下一步仍需 Commit 2 的调查指令和 A/B 验证，不能把冒烟结果当作搜索效率改善。

### 2026-11-15 调查策略优化（阶段 2.5 Commit 2）

本次只改调查决策指导，不新增工具、不引入 Planner、不改变模型—工具循环，也不修改 `query/tools/files.py`、`ReadLedger`、预算参数或模型配置。改动范围：

- `DirectAgentRuntime._system_prompt` 增加 6 条紧凑调查策略：先明确要确认的对象与必须覆盖的系统/职责环节；已知文件直接 Read；宽范围关键词用 Grep `files` 找候选、缩小 `path` 后用 `matches`/`context` 定位、最后 Read 核实；跨系统问题沿调用、路由、配置和数据线索确认各系统职责；下一步只增加无关细节时不再扩展；关键对象和环节有源码依据后直接回答并如实说明缺口。
- `FileCapability.instructions` 补充工具适用场景：已知文件 Read、已知类名/文件名 Glob（`*`/`**`，不支持 `{a,b}`）、宽范围 Grep `output_mode='files'`、候选文件用 `matches`+`context=2` 定位、结论必须 Read；明确 `output_mode` 仅支持 `matches`/`files`，且 Grep 命中不能作为引用依据。
- `PROMPT_VERSION` 从 1 升到 2；执行设置 `runtimeRevision=core-direct-2`，会话范围包含 `promptVersion`，旧策略会话不会被新运行恢复。

专项回归：新增“策略与工具指导已交付”“Prompt 版本变化使旧会话失效”两项测试；Direct 27 项通过，核心/业务/文件/服务/工作区相关 68 项通过。

真实模型 A/B 设计：同一验证工程、`core-v1`、`glm-5.3-flash`、温度 0.6、40 次调用/300 秒/600,000 字符/131,072 输出 token，单路顺序（供应商单账号并发上限 1）。A 组使用提交 `1559279` 的旧 Prompt + Commit 1 工具；B 组使用本次新 Prompt + 相同工具。第一轮 3 题用于校准 Prompt；第二轮 8 题使用最终 Prompt 与 A 组同题配对。

第一轮（3 题）暴露初始策略的跨系统覆盖退化：`biz-repaid-limit` 未读渠道侧、`native-lifecycle` 未读命令路由层；`v2-strategy-code` 从 28 次调用未完成缩短到 13 次完成。据此补充“问题要求的关键系统/环节不得省略”和“Grep 命中不是引用依据”两条约束，再执行第二轮。

第二轮 8 题同题配对结果（A / B）：

| 题目 | A（旧 Prompt） | B（最终 Prompt） |
| --- | --- | --- |
| biz-repaid-limit | completed，15 次调用，251.0 秒，确认引用 32/33，答案 4315 字 | completed，14 次调用，246.7 秒，确认引用 35/35，答案 4858 字 |
| biz-same-credit | completed，17 次调用，207.1 秒，确认引用 41/43，答案 4633 字 | completed，19 次调用，173.8 秒，确认引用 38/38，答案 5079 字 |
| v2-strategy-code | incomplete（budget），28 次调用，219.8 秒，确认引用 12/17，答案 3277 字 | completed，20 次调用，148.0 秒，确认引用 18/19，答案 4867 字 |
| native-frontend | completed，19 次调用，138.0 秒，确认引用 31/43，答案 5422 字 | incomplete（budget），20 次调用，214.4 秒，确认引用 23/30，答案 2776 字 |
| native-product | completed，14 次调用，129.5 秒，确认引用 22/25，答案 5858 字 | completed，9 次调用，86.8 秒，确认引用 25/25，答案 5544 字 |
| native-repayment | incomplete（budget），33 次调用，211.1 秒，确认引用 15/21，答案 2436 字 | incomplete（budget），34 次调用，218.7 秒，确认引用 15/19，答案 3272 字 |
| native-schedule | completed，28 次调用，200.3 秒，确认引用 19/22，答案 5586 字 | completed，16 次调用，219.2 秒，确认引用 15/17，答案 4451 字 |
| native-lifecycle | incomplete（budget），26 次调用，220.4 秒，确认引用 17/20，答案 3110 字 | incomplete（budget），39 次调用，213.9 秒，确认引用 15/17，答案 3491 字 |

汇总：A 完成 5/8、180 次调用、1577.2 秒、确认引用 189/224；B 完成 5/8、171 次调用、1521.5 秒、确认引用 184/200（确认率 84.4%→92.0%）。

逐题观察：

- 明确改善：`v2-strategy-code` 由 28 次未完成变为 20 次完成（148 秒、18/19）；`native-product` 9 次调用完成（A 14 次、25/25 引用）；`native-schedule` 16 次完成（A 28 次）；`biz-repaid-limit` 14 次完成且 35/35 引用，A 为 3/5 关键文件、B 为 4/5。
- 互有得失：`biz-same-credit` B 少 33 秒但多 2 次调用；`native-repayment` 两者均未完成，B 的部分答案更完整（3272 对 2436 字）。
- 明确退化：`native-frontend` A 完成（138 秒、5422 字），B 预算收尾（214.4 秒、2776 字、23/30），B 组 Glob 调用偏多、Read 覆盖不足；`native-lifecycle` B 39 次调用仍预算收尾，关键文件 4/5（A 5/5），确认引用 15/17（A 17/20）。

结论与边界：

- 工程验收通过：策略与工具指导已交付，版本隔离生效，主循环未改；模拟测试只能证明指导已进入 Prompt，不能证明模型在真实问题中遵守。
- 效果验收**未完全通过**：8 题完成数持平（5/8），调用数 -5.0%、耗时 -3.5%，确认引用率由 84.4% 升到 92.0%；但 `native-frontend` 由完成退化为预算收尾，说明改善有限且不稳定，不能宣称定位效率或自主收敛已有可重复收益。
- 不把 `Grep(files)` 使用率作为成功标准：`native-product` 在 B 组未调用 Grep、仅用 Glob+Read 完成，属于合理工具选择；反过来，B 组 `native-frontend` 的 Glob 过多也是实际失败 Trace。
- 真实模型结果只证明该 8 题、该模型、该预算下的表现；单题单次运行且温度 0.6，第一轮 3 题与第二轮其余 5 题运行时刻不同，供应商偶发连接错误/超时后已重试并保留成功运行。
- 未验证项：core-business-v1 交互、完整 16 题、浏览器人工复核；生产默认仍为 Legacy，不进入 AST。
- 下一步按既定决策树执行**答案先行检查点**（约 50% 预算处生成结论/依据/缺口草稿，仅在引用可核验时接受），继续堆叠 Prompt 不作为优先方案。

### 2026-11-15 调查覆盖管理与 Answer Checkpoint（阶段 2.5 Commit 3）

#### 退化 Trace 诊断（Commit 3.1）

新增离线脚本 `scripts/evaluation/trace_diagnosis.py`，只读取既有 `RunTraceArchive`，不调用模型、不修改工程工作区。诊断报告见[Direct 调查 Trace 诊断](../../records/2026-11/direct-trace-diagnosis-20261115.md)。

- `native-frontend`：B 组在轮次 8 前已读 8/8 关键文件，轮次 14 起转向 `fineract-repo`，轮次 16–19 的搜索没有产生新的答案证据，轮次 20 预算收尾；A 组在轮次 19 完成。退化点是“证据足够后继续扩展”，不是关键文件未找到。
- `native-lifecycle`：B 组只读 4/6 关键文件（缺 `CommandWrapperBuilder`、`CommandHandlerProvider`），轮次 28 起扩展到渠道、中台和前端，轮次 39 收尾；A 组 6/6 并在轮次 19 后少量跨系统读取。退化点是“关键环节遗漏 + 无关扩展”。
- `v2-strategy-code`：B 组轮次 9 前读齐 5/5 关键文件，轮次 20 完成；A 组 5/5 但轮次 28 仍未完成。该题是正向案例。

结论：三题的失败/成功都不能用“搜索工具是否存在”解释，主要差异是**何时认为问题要求的关键环节已有证据、以及下一步是否还能改变结论**。这支持做阶段性检查点，而不是继续增加搜索工具或固定停止阈值。

#### 检查点实现（Commit 3.2 / 3.3）

- 开关：`BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED`，默认关闭；`CHECKPOINT_VERSION=1`。开启时 `runtimeRevision=core-direct-2-checkpoint-1`，会话范围包含 `checkpoint`/`checkpointVersion`，开关变化不会复用历史会话。
- 触发：只在正常调查预算约一半时执行一次（调用预算扣除 2 次收尾保留，时间预算扣除最多 200 秒或 40% 收尾保留）；必须已有至少一次有效 Read；预算/停滞收尾已触发时跳过；检查点请求计入调用与时间预算，只发一次无工具请求。
- 输出：模型只返回 JSON，包含 `draftAnswer`、`aspects`（confirmed/partial/missing + claim + evidence）和 `gaps`（required/optional + reason + nextAction）。程序用既有 `verify_answer` 核验 evidence 的路径、行号和 Read 交付区间；无法核验的 confirmed 降级为 partial。
- 下一轮提示：只列已确认、部分确认和 **required** 缺口；optional 缺口只报数量，不列出，避免模型为可选项继续扩展；没有 required 缺口时提示直接作答并说明未确认项。提示以 HumanMessage 注入，不是工具权限或强制结束。
- 兼容：解析失败或检查点异常时回退原 Direct 调查；取消时检查点请求随 `controlled` 停止；上下文不足时跳过检查点；`ReadLedger` 缓存/恢复逻辑不变；`incomplete` 预算收尾保留。
- Trace：检查点作为独立模型调用 `phase=checkpoint`，记录 `checkpoint` 摘要、已核验 aspects 与 draftAnswer；`usage` 增加 `checkpointCalls` 与 `checkpoint`；前端 Trace 将检查点标为“调查检查点”。

测试：Direct 34 项、核心服务 12 项（含检查点 Trace）、相关后端 131 项、前端 20 项通过。

#### 三题两方案两重复 A/B

A=当前代码检查点关闭，B=相同代码/工具/Prompt 开启检查点；`glm-5.3-flash`、温度 0.6、40 次/300 秒/600k 字符/131,072 输出 token，单路顺序；每题每组 2 次。完整数据与逐题观察见[Answer Checkpoint 三题真实模型 A/B](../../records/2026-11/answer-checkpoint-ab-20261115.md)。

| 指标 | A（关闭） | B（开启） |
| --- | --- | --- |
| 完成 | 2/6 | 1/6 |
| 模型调用 | 155 | 118（-23.9%） |
| 耗时 | 1243.6s | 1293.4s（+4.0%） |
| 确认引用 | 101/137（73.7%） | 72/94（76.6%） |
| 关键文件 Read | 33/38 | 31/38 |

- 正向：`v2-strategy-code` B 组一次 14 次调用完成、关键文件 5/5、确认引用 14/14，检查点后不再调用工具；检查点作为进度整理器有可观察价值。
- 退化：`native-lifecycle` 两次重复关键文件 4/6→3/6，均缺 `CommandWrapperBuilder`、`LoanApplicationApprovalCommandHandler`、`CommandHandlerProvider`，引用确认降至 4/12、5/11；`native-frontend` 完成 1/2→0/2。检查点合计耗时 355.8 秒，约占 B 组总耗时 27.5%，没有转化为时间收益。
- 根因：检查点能整理已确认结论，但 `missing/partial` 与 `required/optional` 缺少稳定映射。`native-lifecycle` 把 `logCommandSource` 命令路由降为 optional，把 `LOAN_CREATED` 触发点列为 required；`v2-strategy-code` 第二次把授权仓库中不存在的产品配置列为 required，检查点后继续 11 次工具调用仍未解决。
- 结论：属于接近情形 B、并带 C 部分特征的实验结果。**不验收自动结束，不进入 8 题扩展**；检查点默认关闭，生产默认仍为 Legacy。人工浏览未发现与源码直接冲突的新错误断言，但关键证据遗漏本身已构成 P0 退化；本轮未做逐条语义复核。
- 下一步：先改进缺口识别（`missing` aspect 必须对应 required gap 或说明不属于原问题；授权仓库中不存在的配置不得列为 required；required 缺口绑定原问题环节），再以同一 3 题、同一预算复验；关键文件覆盖和完成率同时不退化后，才考虑 8 题扩展。

#### 2026-11-15 检查点 v2 修正与复验

针对 v1 验收指出的三个问题，检查点实现升级为 v2（`CHECKPOINT_VERSION=2`）：

- **事项—缺口强制关联**：`aspects` 必须有唯一 `id` 和 `required`，`gaps` 必须用 `aspectId` 关联已有事项；`aspects` 为空、引用不存在事项、`missing` 事项无缺口、全部事项非必需、字段类型错误都判定为结构失败并回退原调查。`required` 且 `missing` 的事项，其 optional 缺口由程序提升为 required，并记录 `adjustedGapCount`。
- **区分必需性与可核实性**：缺口新增 `resolvable`；`resolvable=false` 的必需缺口提示“当前授权资料内无法继续核实，不要继续搜索，答案说明限制”，不进入定向补查。
- **独立短预算**：检查点不再复用完整调查系统提示，使用短 JSON 提示；新增 `BUSINESS_CODE_AGENT_CHECKPOINT_TIMEOUT`（默认 60 秒）、`..._OUTPUT_TOKENS`（1024）、`..._CONTEXT_CHARS`（20000）。检查点请求中的工具原文替换为路径/行号占位，完整原文仍用于最终引用核验。
- **会话隔离**：检查点进展消息带 `runtimeOnly=checkpoint`，保存/加载/压缩都不把它当作用户问题或后续会话历史；检查点关闭时不把新增预算字段放入会话范围，避免无谓失效。
- **结构失败回退**：解析、结构、配置或模型异常都记录检查点失败，不注入指导，继续原 Direct 调查；取消仍随 `controlled` 停止。

测试：Direct 43 项、核心服务 12 项、相关后端 140 项、前端 20 项通过。

三题两方案两重复真实模型 A/B：A=关闭，B=开启；`glm-5.3-flash`、温度 0.6、40 次/300 秒/600k 字符、检查点 60 秒/1024 token/2 万字符，单路顺序。完整数据见[Answer Checkpoint v2 三题真实模型 A/B](../../records/2026-11/answer-checkpoint-v2-ab-20261115.md)。

| 指标 | A（关闭） | B（开启） |
| --- | --- | --- |
| 完成 | 3/6 | 4/6 |
| 模型调用 | 160 | 152（-5.0%） |
| 耗时 | 1143.8s | 1177.4s（+2.9%） |
| 确认引用 | 109/146（74.7%） | 106/133（79.7%） |
| 关键文件 Read | 33/38 | 34/38 |
| 答案关键词覆盖 | 41/48 | 39/48 |

- 检查点 4/6 成功、2/6 因供应商连接错误立即失败（均在 lifecycle）；成功检查点耗时 27.2–58.0 秒，平均 36.8 秒，约占 B 组总耗时 12.5%，较 v1 的 355.8 秒/27.5% 明显下降，但仍是显著开销。
- 正向：`v2-strategy-code` B 组 2/2 完成、关键文件 10/10；A 组 1/2 完成、8/10，其中一次 11 次调用完成但缺两个关键文件。检查点帮助避免了“提前完成但证据不足”的一次运行。
- 未改善：`native-frontend` 两组均 2/2 完成、8/8 关键文件，B 更慢；`native-lifecycle` 主样本检查点全部因连接错误未执行，B 0/2 完成、关键文件 8/12，A 0/2、9/12，主样本没有真正检验命令路由缺口。
- 补充 lifecycle 运行：检查点完成后 16 次调用完成，但关键文件仅 3/6（缺 `CommandWrapperBuilder`、`ApprovalCommandHandler`、`CommandHandlerProvider`），答案却写“所有关键环节已确认到行级证据”。这是“完成但关键证据遗漏并过度声明”的 P0 反例，不能算检查点有效。
- 结论：v2 修复了结构、会话和成本问题，完成数略升、调用略降，但关键覆盖和答案关键词未改善，lifecycle 的关键缺口场景仍失败，并出现提前收尾/过度声明。**不批准默认启用，不进入 8 题扩展；检查点保持默认关闭，生产默认仍为 Legacy。**
- 后续调整见下节。检查点 v2 继续作为可复验的历史实验。

### 2026-11-15 Direct 证据与答案覆盖闭环

Question Contract v3 冻结，保留原问题和模型登记的目标，停止扩展分句与词面覆盖规则。Evidence Ledger 关联目标、模型结论、原文及必要关系。Answer Closure 只从已有记录组织可核对的结论并标明限制，在同一 Direct 循环内工作。`ReadLedger` 继续负责原文区间和恢复。运行方式见[能力与运行合同](agent-capability-contract.md#81-direct-答案覆盖实验)。

使用 `native-frontend`、`native-lifecycle`、`v2-strategy-code` 做同题配对。各组使用同一冻结工程、`core-v1`、模型、温度、预算，均关闭 Answer Checkpoint v2。A 使用原 Direct 答题，B 启用首次闭环实现。B 出现把有效局部结论和无效引用装在同一段而丢失答案的情况，修正版 C 只增加有条件的一次退回，在同一模型循环内拆出有效部分和未核实项。逐题复核答案、连接证据和未确认项，同时统计关键文件、耗时、调用数和 token。

九次真实运行已完成。C 的关键文件 Read 为 18/19，A 为 13/19。C 恢复了前端题的局部结论并核实策略题的产品覆盖条件，三题均以预算收尾，累计耗时较 A 增加约 31%。B 和 C 没有成功登记必要关系，关键命令路由的效果验收尚未通过。闭环继续作为显式实验，后续评估聚焦调查中及时登记连接和局部依据。见[真实对照及轨迹索引](../records/2026-11/direct-answer-closure-ab-20261115.md)。

### 2026-11-15 Context Manager

先验证上下文预算、压缩与恢复，文件输出和调查进展分别作为后续变更，沿用 Direct 的模型和工具循环。压缩策略、引用边界及使用方式见[能力与运行合同](agent-capability-contract.md#82-direct-上下文管理实验)。

此前答案覆盖对照的 600,000 字符上限下，原 Direct 的三个基线运行均未触发 Read 正文裁剪。为观察压缩，另用同一个冻结工程与三题，将三组的字符上限都设为 100,000，并固定模型、输出上限、轮数与时长，关闭检查点与 Answer Closure。首版与修正版使用 40,000 token 的总窗口。比较压缩是否触发、实际交付给答题模型的原文、关键文件覆盖、答案和总用量。

九次真实运行及策略题同请求重放已完成。首版整批裁剪丢失已读请求值，修正版优先缩减旧搜索输出并保留该原文。修正版相对字符基线正常完成数从 1/3 升至 2/3，最终可见关键文件均为 16/19，模型调用、搜索次数、总耗时和 token 上升。继续显式启用，生命周期题的命令分派仍待核实。见[真实对照及轨迹索引](../records/2026-11/direct-context-manager-ab-20261115.md)。

### 未完成的效果验收

阶段 2 的工程验收（B01—B12）已完成，但**业务知识是否真正改善调查质量尚未验证**：

- 2026-11-14 已完成 1 组真实模型同题对照：`core-business-v1` 实际读取 4 份业务文档并完成作答，`core-v1` 同样完成；本题未观察到业务资料带来的正确性提升。记录见[真实模型首轮验证](../../records/2026-11/business-context-real-model-20261114.md)。
- 同一记录中的第二题曾因重复补读运行 230 次调用；重复读取修复后改用新模型 `glm-5.3-flash` 复验，128k/64k 下 Core 与 Business 共四次运行全部完成，64k 两组实际恢复被压缩的原文 1 次和 4 次、证据缺口为 0，已从“无法收敛”转为能够形成答案。该复验仍不是完整的 16 题对照。
- 2026-11-14 完成 8 题预算收紧子集（40 次/300 秒/600k 字符，单路，16 次运行）：7 次 `completed`、9 次 `incomplete`（全部 `budget`），无重复读取、恢复或缺口；Business 4/8、Core 3/8 完成，样本不足以证明业务知识收益。完整 16 题对照仍未执行，浏览器人工复核、关键阶段覆盖率与错误结论统计仍未完成。
- 验证期间修复了系统提示未计入上下文预算、压缩证据不可恢复和限制停止记成 completed 三类问题，并增加回归测试；修复不能替代效果验收。
- 当时生产默认仍为 Legacy；`core-business-v1` 需显式配置启用。2026-11-15 起在线默认已切换为 Pi（见第 10 节）。

效果验收必须在同一模型、同一组问题、仅改变 Business Context 授权的条件下完成，并单独记录；不能由工程测试通过替代。

## 10. Pi Runtime 统一问答主链（2026-11-15，已实现并默认启用）

本轮新增 Pi 作为在线问答的唯一调查循环，旧 LangChain 与 Direct 代码保留但不在 Pi 链路内复用。实现与证据见[Pi Runtime 实现记录](../records/2026-11/pi-runtime-unification-20261115.md)。

- **入口**：在线问答默认选择 `pi-v1`（`BUSINESS_CODE_AGENT_RUNTIME` 缺省 `pi`）；`QueryService._query_pi()` 统一处理 `combined`／`none`／`backbone`／`ast`，Pi 失败不回退 LangChain。显式 `BUSINESS_CODE_AGENT_RUNTIME=langchain` 仅用于历史同题对照与评测，不在默认链路。
- **Pi 职责**：模型选择、工具决策、工具执行、上下文压缩、会话与 Agent Loop 均由 Pi 进程负责；CodeAgent 只准备授权工作区、资料清单、Run/Trace、取消、持久化和结果协议。
- **工具**：只开放 `read`、`grep`、`find`、`ls`、`codemode`，不开放 `bash`／`edit`／`write`。工作区仓库是软链接；Pi 自带 `grep`／`find` 默认不跟随符号链接，因此显式加载只读扩展 `extension/codeagent_search.js`，强制跟随授权链接并在执行前拦截越界路径。该扩展不是 OS 沙箱，不能把工作区 cwd 当作安全边界。
- **会话**：CodeAgent `conversationId` 映射到工程数据目录 `pi-sessions/` 下的 Pi 会话文件；工程、scope、Profile、资料模式/版本或 Pi 配置变化时重建 Pi 会话。旧 LangChain 会话不直接复用，历史答案仍可查看。
- **Trace**：Pi 事件转换为现有 `model`／`tool`／`status`／`compaction`／`text` 事件；模型调用在 `message_start` 即写入 running，`message_end` 补响应，取消时标记 cancelled。Trace 记录 Pi 版本、实际模型、工具、上下文压缩、token 和会话文件。
- **配置**：`BUSINESS_CODE_PI_COMMAND`（默认 `pi`）、`BUSINESS_CODE_PI_MODEL`（空为 Pi 默认模型）、`BUSINESS_CODE_PI_TIMEOUT`（默认 600 秒）、`BUSINESS_CODE_PI_THINKING`。启动前检查 CLI 版本、实际模型、只读搜索扩展命令和授权仓库；任一不满足明确失败，不自动降级。

验证：Pi 专项 21 项、全量后端 513 项及 91 个子测试通过；真实 API/SSE 已完成源码问答、同会话追问和取消三次运行。默认运行已于 2026-11-15 由用户确认切换为 Pi。该验证只证明链路可用，不证明相对旧链的答案质量收益；8／16 题同题同模型对照与现有模式验收仍未完成，旧 LangChain 显式入口暂留作对照，硬封存与删除另行确认。
