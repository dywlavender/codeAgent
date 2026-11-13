# CodeAgent V2 迁移与验收计划

状态：设计冻结；实现阶段尚未开始。确认日期：2026-11-13。

依据：[V2 总体架构](agent-architecture-v2.md)、[能力与运行合同](agent-capability-contract.md)。本次第一步称为阶段 0；下文阶段 1 是下一步 `core-v1` 实现，不能把两者验收混在一起。

## 1. 当前基线与保护范围

核对基线为 `claudecode`，HEAD 为 `2531add`，并包含开始本次任务时已存在的未提交改动。文档描述实际工作树，不宣称远端分支已经同步，也不把未提交实现视作已发布能力。

关键现状：

- [query/service.py](../../business_code_agent/query/service.py) 已调用 `prepare`、`ask_prepared`，保存 `execution_json/session_config_json`，会话比较包含工具与资料；构造函数仍接收多个路径，尚无 RunContext。
- [query/profile.py](../../business_code_agent/query/profile.py)、[query/materials.py](../../business_code_agent/query/materials.py) 和 [langchain_agent/preparation.py](../../business_code_agent/query/langchain_agent/preparation.py) 在本次开始时为未跟踪文件，已有 AgentProfile、MaterialManifest 和预检实现；不是从完全没有接口开始。
- 当前 `standard-v1` 的 `agent_direct` 分支明确抛出未开放错误；其资料默认包含多类资料、搜索 optional，不能直接改名并声称等价 `core-v1`。
- 默认仍是当前 coordinated 及 combined。V2 的 `core-v1/legacy-v1` 是未来注册名称，本次不修改环境变量解析或默认行为。
- [platform/projects.py](../../business_code_agent/platform/projects.py) 已有 ProjectContext；`query/` 目前没有类型化 QueryScope，scope 由服务校验并展开。
- query 已不直接依赖 evaluation；AST 只读投影位于 [platform/ast_material.py](../../business_code_agent/platform/ast_material.py)，完整 AstService 由 API 注入，生成仍在 evaluation。不能误写成当前存在 `platform → evaluation → query` 的直接 import 环。
- 当前 AstService 构造包含目录创建与中断状态修复，AstMaterial 也可能创建目录、写新鲜度缓存。因此 V2 的「纯只读能力预检」不能直接无差别调用这些生命周期构造；core 应完全绕开，后续 AST 阶段提供真正只读的固定版本 reader。

本次只改文档。不得重写 LangChainRuntime、拆分 QueryService、删除 Coordinator、修改 schema/前端/配置、合并 Code Map 和 AST、改变业务资料格式、重新实现检索或契约分析。已有生产代码改动保留，不混入本次文档交付，也不回滚或覆盖。

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
| `api/server.py`、`cli.py` | 仅装配 ProjectContext、提供者、RuntimeFactory 和传输参数 | 1、3 | 不在路由中实现能力选择/循环；本阶段不改入口 |
| `storage/schema.py`、前端问答页 | 继续原行为；阶段 1 优先复用现有执行快照/usage 扩展元数据 | 后续按需 | 本次不迁移数据库、不改页面；不把 UI 重做作为 core 前置 |

目标目录不存在不是问题；接口实现与依赖边界优先于目录搬家。退出旧入口前必须检查真实调用方，不保留无用 re-export，也不因追求「新目录整齐」批量移动代码。

## 3. 实施阶段与闸门

### 阶段 0：本次设计冻结

交付三份 V2 文档，更新当前架构、AGENTS、指导方向和文档索引，完成第 5 节走查与第 6 节验收。只运行指定三组既有测试，不要求零索引 Agent 已实现。

### 阶段 1：下一步只实现 core-v1

顺序固定为：

1. 在已有 query 类型模块演进 AgentProfile、MaterialSource、Runtime 接口；新增 RunContext/QueryScope；补充无模型的授权/会话指纹测试。
2. 在 WorkspaceManager 增加不触发 AST 投影/索引探测的 core 资料路径；落实 ready 根、嵌套排除目录、链接检查和活跃运行隔离。
3. 实现 CapabilityRegistry 与 FileCapability，固定 Read／Glob／Grep；不实现业务、AST、Hybrid 或 Contract 提供者。
4. 新增一个 DirectRuntime 与独立 Session 管理，用配置模型自主调查；不继承旧 LangChainRuntime，不搬入 Coordinator、Investigation 或业务收尾。
5. 接入现有 QueryService：通过工厂选择 V2/Legacy，保存公共 RuntimeResult、执行快照、证据与完整事件。兼容适配只在边界出现一次。
6. 实现并验证 legacy-v1 的标准对照适配；旧开关/模式保留原入口，历史数据读取不变。
7. 完成源码-only 三仓库、磁盘已有但禁用索引、会话隔离、取消/失败、引用与 Trace 回归，再按用户允许的真实模型调用范围验证。

验收必须包含：不安装可选解析/检索依赖也能导入 core；禁用能力探测函数「调用即失败」仍可问答；跨仓库线索由模型选择继续读取；新增测试能力无需改 Direct 循环即可使用。以真实模型做独立样例验证时，明确模型、资料、用量和未确认关系，不将测试桩通过当答案收益。

阶段 1 不自动切换生产默认，也不删除旧链。切换需单独确认，不能靠重命名 standard-v1 绕过新授权合同。

### 阶段 2：Business Context

只增加资料授权/版本、阅读指导和业务能力提供者，复用原内容格式和 File 工具。验证术语消歧、提款/额度语义、系统职责与源码冲突的区分；同模型配对 core 与业务组合，正常源码/README/需求不得删减。实现可用与业务语义收益分别验收。

### 阶段 3：AST 与结构导航

将 AST 生命周期从 evaluation 调整到 CodeIntel，保留已有版本目录、显式生成和历史引用。提供真正只读的状态/版本 reader 及 AstCapability，复用已有解析结果和 Code Map 导航视图；问答不解析、不更新缓存型索引、不重新生成。

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

### 回归命令

```bash
python -m unittest discover -s tests -p 'test_package_boundaries.py'
python -m unittest discover -s tests -p 'test_import_paths.py'
python -m unittest discover -s tests -p 'test_query_runtime_service.py'
```

这些检查覆盖现有包约束、导入路径与 Query 行为，不直接验证文档内容或全部 V2 依赖。还须检查文档链接、A01—A12、五场景和文件差异。已有生产代码脏改动存在时，测试结果属于该工作树，不能仅据通过断言本次未改生产代码，需另核对差异/指纹。

2026-11-13 已在项目 `.venv/bin/python`（Python 3.14.3）下执行以上三个模块；当前 shell 没有 `python` 命令，因此使用虚拟环境解释器的完整路径，测试范围未改变。

| 检查 | 结果 |
| --- | --- |
| test_package_boundaries.py | 5 项通过 |
| test_import_paths.py | 2 项通过 |
| test_query_runtime_service.py | 8 项通过；失败路径用例产生的 RuntimeError 日志为预期 |
| 新增文档相对链接/章节锚点 | 15 个通过 |
| Python 类型合同代码块 | 语法解析及 21 个类型/协议的前向引用解析通过；不代表生产实现或静态类型检查通过 |
| git diff --check | 通过 |
| 开始时已有 17 个实现改动文件 | 聚合 SHA-256 前后相同，保留原工作树改动 |

未运行全量测试、未调用真实模型、未生成索引。上述回归针对当前含已有改动的工作树，不能替代阶段 1 的 Direct 功能与真实问答验证。

独立设计审查最初发现四项接口缺口：设置进入会话指纹的时机、业务能力与 File 授权联动、跨工具的运行期失效信号、Evidence ID 封装前后类型。已补齐合同并同步场景；第二次只读复核确认四项闭合，无剩余阻塞独立实现的问题。A01—A12 按设计冻结范围完成，五场景均为接口级演算，不是实际 Agent 运行验收。

实施与验收进度：**7/7（阶段 0）**。

| 交付项 | 状态 |
| --- | --- |
| agent-architecture-v2.md：总体架构与执行主链 | 完成 |
| agent-capability-contract.md：上下文、Profile、Capability、Runtime | 完成，审查缺口已修订并复核 |
| agent-migration-plan.md：迁移、顺序与回退 | 完成 |
| architecture.md：V1 当前实现与 V2 目标 | 已同步 |
| AGENTS.md、指导方向与文档索引一致 | 已同步 |
| 五个场景接口级走查 | 完成设计走查与复核；未执行模型 |
| 现有架构边界及 Query 回归 | 三组共 15 项通过 |

下一步范围仍仅为 core-v1，按阶段 1 实施；本次不创建任何目标 Python 模块，不改变生产默认，也不声称已验证零索引问答或扩展能力收益。
