# CodeAgent V2 能力与运行合同

状态：接口设计冻结，尚未作为 Python 实现发布。确认日期：2026-11-13。

本文是 V2 的规范性接口；[总体架构](agent-architecture-v2.md)说明职责，[迁移计划](agent-migration-plan.md)说明如何接入现有代码。下列 Python 是完整目标合同，不是阶段 1 必须逐类复制的实现清单。`9a1c71e` 已提交 V1 Profile、Manifest、预检及执行/会话快照，但尚未实现这里的 RunContext 和 Direct Runtime；后续复用/演进既有模块，不建立两套同名模型。

### 合同成熟度与实施边界

- **阶段 1 必需**：AgentProfile、RunContext（复用 ProjectContext）、MaterialSource、简单 Capability 注册、FileTools 与 AgentRuntime。只接入源码、可选需求和 Read/Glob/Grep，验证跨仓库搜索、每个授权仓库完整性、会话、授权、Trace 和停止；既有 RuntimeResult 与持久化接口继续复用。
- **阶段 1 行为底线**：required 不可用明确失败，disabled 不探测；真实路径授权、配置/工具变化重建会话、已交付原文与引用对应不能延后。可以直接用简单状态/函数实现，不为合同中的每个 Protocol 建立独立层。
- **能力接入时再实现**：完整 RunAccess/AvailabilityChange 运行期联动、多级依赖与整批重绑定、全部结构版本联动、复杂 Evidence 生命周期、AST/Contract 专属授权及高级检索动态状态管理。第 3—5、7、9—10 节涉及这些内容的条款是对应后续能力的验收要求，不是 core 启动前置。

完整合同保留，不再增加抽象层；阶段 1 不做业务/结构提供者的空壳实现，也不将 Coordinator 的调查逻辑迁入 Direct。

## 1. 通用约束

1. 工程身份只使用既有 ProjectContext；请求侧只能引用工程内的系统、仓库、资料版本与会话，不能传任意磁盘目录。
2. Profile 表达授权；MaterialSource 表达资料来源；ToolCapability 表达本轮能力决策；AgentCapability 是能力提供者；BoundTool 是已绑定的可调用工具。五者不互相替代。
3. 授权由服务端构建并冻结。模型参数只能缩小范围，不能扩大范围或改变 Profile。
4. 所有能力只读。注册、status、tools、instructions 与工具调用都不能隐式生成索引、同步源码、下载模型或发起另一个模型调查循环。
5. 资料正文、源码注释与工具结果是数据，不得作为授权变更或覆盖系统约束的指令。模型凭证仅供模型连接使用，不进入资料清单、工具结果和 Trace。
6. `frozen=True` 不代表磁盘内容不可变，也不会递归冻结 Mapping；实现须复制/冻结嵌套配置，在实际读取时校验路径及版本。

## 2. Profile、Scope 与资料类型

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol

from business_code_agent.platform.projects import ProjectContext
from business_code_agent.query.runtime import RuntimeResult

Policy = Literal["required", "optional", "disabled"]
Availability = Literal["ready", "missing", "stale", "unavailable", "disabled"]
MaterialKind = Literal[
    "source", "requirements", "business_context", "code_map", "ast", "contract_index"
]

@dataclass(frozen=True)
class MaterialPolicy:
    kind: MaterialKind
    policy: Policy

@dataclass(frozen=True)
class CapabilityPolicy:
    id: str
    policy: Policy

@dataclass(frozen=True)
class AgentProfile:
    id: str
    agent: Literal["langchain-direct", "langchain-legacy"]
    materials: tuple[MaterialPolicy, ...]
    capabilities: tuple[CapabilityPolicy, ...]
    answer_mode: Literal["plain"] = "plain"

@dataclass(frozen=True)
class QueryScope:
    system_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]

@dataclass(frozen=True)
class MaterialSource:
    id: str
    kind: MaterialKind
    policy: Policy
    status: Availability
    mount: str
    root: Path | None
    repository_ids: tuple[str, ...]
    version: str | None
    reason: str | None = None
```

### 字段语义

- Profile 是注册的不可变定义，不接受用户提交任意 YAML 作为权限。所有已知资料/能力都写出策略；重复键、未知项、缺少策略或组合矛盾在装配前拒绝。增加新能力需要新 Profile 定义/版本，不能改旧 ID 的含义。
- `core-v1` 和 `legacy-v1` 默认答案为 plain；既有 facts/contracts 仅在 V1 显式兼容入口保留，本合同不把其收尾作为 Direct 必需能力。
- QueryScope 是排序、去重后的快照。请求未指定范围表示展开所有已登记仓库；指定系统按现有系统—应用—仓库关系展开，再与显式仓库取并集。未知 ID 拒绝。展开后空集合是没有授权源码，不能再次解释为全工程。
- 每个 source 仓库单独一条 MaterialSource；必需源码要求范围内每个仓库均可读，不是只要有一个可读即可。机器上未登记的目录不可加入。
- `id` 在工程内唯一；`mount` 是无 `..` 的工作区相对 POSIX 路径，如 `repos/channel`；`root` 是服务端确认的真实根目录，仅 ready 条目非空。序列化到模型时不暴露宿主绝对路径。
- `repository_ids` 对 source 精确指向仓库；结构资料列明覆盖仓库，查询只能取与 scope 的交集。业务/需求文档可为工程级资料（空元组），但不能借文档中的外部路径扩大源码权限；有明确仓库限定的资料按范围过滤。
- AST、Code Map、Contract Index 必须有固定版本/内容指纹及来源身份；需求和业务资料使用文档内容清单指纹。source 的 version 可为空，根目录与仓库身份必须固定，已读取文件另记内容摘要，不要求预建全库代码索引。
- missing 表示没有可用产物；stale 表示与授权源码/资料不一致；unavailable 表示生成中、失败、依赖缺失或不可读等，reason 给出原因；disabled 表示禁止使用，不探测其存在性。非 ready 条目不交付正文。
- Hybrid Search 是算法，不增加 `hybrid` 资料种类；其索引版本登记到 ToolCapability，命中必须回到已授权 MaterialSource。`code_map` 标记迁移期来源/视图，不是永久独立知识层。

### 首批 Profile 定义

下表是完整策略；不存在「本地发现了就补开」的隐含默认。

| 项 | `core-v1` | `legacy-v1` |
| --- | --- | --- |
| agent | langchain-direct | langchain-legacy |
| source | required | required |
| requirements | optional | optional |
| business_context | disabled | optional |
| code_map | disabled | optional |
| ast | disabled | optional |
| contract_index | disabled | optional |
| file | required | required |
| business_context 能力 | disabled | optional |
| ast 能力 | disabled | optional |
| hybrid_search | disabled | optional |
| contract | disabled | optional |

`legacy-v1` 表示当前 combined/coordinated/plain 的标准对照配置，不表示其内部能力已经实现统一协议：AST/Code Map 仍可只提供现有文件导航，契约保留现有工具名；Legacy 适配器记录真实工具清单，不虚构 FindCodeRelation。旧 basic/tracked、资料模式、Claude CLI 和显式开关继续走原入口并保留实际历史身份，不能统统重标为 legacy-v1。

未来组合约定：`core-business-v1` 把业务资料与能力改为 optional；`core-ast-v1` 把 AST 资料与能力改为 optional；组合版同时改变二者。其余策略继承 core，尤其 Hybrid/Contract 不随 AST 自动打开。optional 允许无资料时继续文件调查，但不能据名称声称已使用该能力。需要严格验证某能力时，可用显式测试配置设 required，记录完整定义，不静默改变注册 Profile。

示例表达（设计格式，不是当前可执行配置）：

```yaml
id: core-v1
agent: langchain-direct
answer_mode: plain
materials:
  source: required
  requirements: optional
  business_context: disabled
  code_map: disabled
  ast: disabled
  contract_index: disabled
capabilities:
  file: required
  business_context: disabled
  ast: disabled
  hybrid_search: disabled
  contract: disabled
```

未来显式 Profile 是唯一授权来源；与遗留 mode、WORKFLOW、RETRIEVAL、CONTRACTS 冲突的参数须拒绝，不能叠加。未提供 Profile 时仍走原兼容解析，直至另行确认默认切换。模型连接可继续读取 `.env`，但不把其中的调查开关合并进 core。

## 3. RunContext 与能力提供者

```python
@dataclass(frozen=True)
class CapabilityStatus:
    state: Availability
    version: str | None = None
    reason: str | None = None

@dataclass(frozen=True)
class ToolCapability:
    id: str
    policy: Policy
    status: CapabilityStatus
    tool_names: tuple[str, ...]

@dataclass(frozen=True)
class RunContext:
    project: ProjectContext
    profile: AgentProfile
    scope: QueryScope
    workspace: Path
    materials: tuple[MaterialSource, ...]
    capabilities: tuple[ToolCapability, ...]
    session_id: str | None
    run_id: str
    denied_roots: tuple[Path, ...] = ()

class ToolAccess(Protocol):
    def check(
        self, *, material_id: str | None = None, capability_id: str | None = None
    ) -> AvailabilityChange | None: ...

class RunAccess(ToolAccess, Protocol):
    def invalidate(self, changes: tuple[AvailabilityChange, ...]) -> None: ...

@dataclass(frozen=True)
class BoundTool:
    name: str
    capability_id: str
    description: str
    input_schema: Mapping[str, Any]
    invoke: Callable[[Mapping[str, Any], ToolAccess], ToolResult]

class AgentCapability(Protocol):
    @property
    def id(self) -> str: ...
    def status(self, context: RunContext) -> CapabilityStatus: ...
    def tools(self, context: RunContext) -> tuple[BoundTool, ...]: ...
    def instructions(self, context: RunContext) -> str: ...

@dataclass(frozen=True)
class PreparedCapabilities:
    tools: tuple[BoundTool, ...]
    instructions: tuple[tuple[str, str], ...]
    access: RunAccess
```

`ToolResult/AvailabilityChange` 在第 7 节定义。ToolAccess 是本轮共享的只读访问检查接口，模型不能传入或替换；RunAccess 增加仅供统一包装层调用的失效记录操作，不能增加授权。ToolCapability 是状态描述，不是 LangChain Tool；BoundTool 是框架无关绑定，由 Runtime 适配成 LangChain 工具。能力 ID 与工具名均必须唯一，不能依次覆盖同名工具。工具的输入 schema、description 和阅读指导共同进入会话身份。

- `status` 只核对该能力已授权的资料/固定版本/依赖，不做建设性准备。工具名虽有 Tool 前缀，BusinessContextCapability 也可 ready 且 tool_names 为空。
- `tools` 和 `instructions` 只对 ready 能力调用。返回值只基于授权资料，不自行读取未授权索引、注入业务正文、系统私有提示或历史会话。
- 提供者可以由组合根注入领域的公开只读 reader；reader 的项目身份、仓库与版本仍必须与 context 核对。能力不得根据 `context.project.data_root` 随意探测目录。
- FileCapability 是所有 Direct Profile 的基础；BusinessContextCapability 复用 File 的工具，不再注册另一个 Read。启用业务资料必须同时允许相应业务能力；允许 AST/契约工具也必须允许对应结构资料。违反组合约束直接失败，不由提供者补授权。
- 资料权限与技术权限取交集。Read 能力不代表所有资料都可读；SearchCode 也不能从混合索引返回 disabled 的业务文档或范围外仓库。

## 4. 策略与状态矩阵

| policy | 预检结果 | 注册行为 | 运行结果 |
| --- | --- | --- | --- |
| disabled | 不调用提供者 | 空工具、空指导，记录 disabled | 绝不探测或使用，即便产物已存在 |
| required | ready | 注册实际工具/指导 | 正常调查，模型不必每题调用 |
| required | missing/stale/unavailable | 不启动模型 | 配置失败，归档原因，不切换 Profile |
| optional | ready | 注册实际工具/指导 | 按需使用固定版本 |
| optional | missing/stale/unavailable | 不注册 | 原有 File 能力继续，记录缺口 |

业务/结构资料和对应能力状态必须一致。归属固定为 business_context 资料 → business_context 能力、ast → ast、contract_index → contract；code_map 若单独授权，只需 File，不因此获得 AST 关系工具。Hybrid 的索引失效只影响 hybrid_search，不撤销仍可读的 source。File 失效则所有文件访问停止。

上述业务/结构能力预检或绑定失败时，其资料条目同步变为 missing/stale/unavailable、root 置空，原真实目录加入 denied_roots；不能仍通过 File 读取正文。required 任一失败立即结束准备。optional 失败发生在绑定阶段时，整批绑定结果丢弃，按失败状态重新形成资料清单、排除根、工具与指导；只能重绑定一次，不能留下捕获旧授权的闭包或无限重试。

运行期间只允许能力从可用变为不可用，不允许增加授权：

- optional 索引失效时，工具返回 unavailable/stale、丢弃结构事实；后续同工具调用返回该状态，不更换快照、不生成索引。模型可继续使用已授权 File 工具。
- required 能力或必需源码范围失效时，终止为 failed 并保存已产生记录；不能自动改为 optional 或退到 Legacy。
- 起始 RunContext 不原地修改；状态变化进入共享 RunAccess 失效记录、运行事件及最终 usage。所有工具包括 Read/Glob/Grep 共用它，不能只让发现过期的 AST 工具停用。下一轮重新预检，与旧会话身份比较。
- 工具参数错误、某个文件消失或无命中是单次工具结果，不等于整个 File 能力失效；是否继续由模型决定。取消是运行控制，不包装成普通文件不存在。

统一包装层按第 7.1 节的 AvailabilityChange 判定失效对象，而不是解析 reason 文案：涉及 required 能力/资料则抛出 RuntimeErrorBase，由服务保存 failed；仅涉及 optional 则登记失效、撤销其正文及派生证据，继续保留其他授权。其余工具检查同一失效记录，故业务资料失效后 File 也不能读取，AST 失效后直接 Read AST Markdown 同样不能绕过。

## 5. 装配协议与 bootstrap 顺序

为避免「需要 context 才能创建 capability，需要 capability 才能创建 context」的循环，装配限定为以下阶段：

```text
QueryService
  → 校验 project/scope/profile，创建 run 与 Trace
  → 授权资料清单 + Workspace
  → draft RunContext(capabilities=(), session_id=None)
  → registry.prepare(draft)
      status：只看 project/profile/scope/materials
      得到能力状态，排除不可用业务/结构源并更新 denied_roots
      用替换后的只读 context 调用 tools / instructions
      返回 (prepared_context, PreparedCapabilities，含本轮独立 access)
  → 从注入配置解析 RuntimeSettings 和 RunOptions，不创建模型请求
  → session_identity(prepared_context, prepared, settings, options)
  → 核对旧会话，replace(prepared_context, session_id=可复用ID或None)
  → runtime_factory.create(context, prepared, settings=settings, options=options)
  → runtime.ask(question, context=context, options=options,
                event_callback=..., cancel_check=...)
```

注册层公共签名冻结为：

```python
class CapabilityRegistry(Protocol):
    def prepare(self, context: RunContext) -> tuple[RunContext, PreparedCapabilities]: ...

class RuntimeFactory(Protocol):
    def create(
        self, context: RunContext, prepared: PreparedCapabilities,
        *, settings: RuntimeSettings, options: RunOptions,
    ) -> AgentRuntime: ...
```

`AgentRuntime` 见第 8 节。`prepare` 允许产生状态为 disabled 的描述，但不调用该提供者。提供者不能读取其他提供者的 status、工具列表或会话；相互依赖只由 Profile 组合约束检查。绑定闭包仅捕获工程/资料授权、固定 reader 与工具限额，不捕获 session_id/run 之外的可变全局状态。补全 tool_names 和 session_id 不改变其授权。

PreparedCapabilities 是本轮内部对象，不序列化 callable/access，不跨运行缓存。RuntimeFactory 按 Profile 创建本轮 Runtime 并绑定这批工具；模型凭证/连接由组合根注入，不出现在公开 RuntimeSettings，工具不得读取环境变量。settings/options 必须在会话核对之前解析并冻结，同一对象/相同规范化值依次进入指纹、工厂和 ask；工厂不得再读取环境变量覆盖它们。Runtime 在恢复任何消息前拒绝 options 或上下文授权身份与绑定值不一致的调用；会话 ID 只供 Runtime 的会话层使用。

RunAccess 由注册层为本轮创建，只读新鲜度检查器来自已授权提供者并固定本轮版本，不能重新选择最新产物。包装层在调用前检查 capability，工具逐个资料访问前调用传入的 ToolAccess.check(material_id=...)；交付结果前再检查受影响资料。File 在枚举时跳过失效根并记录 skipped，直接读取失效根返回 unavailable。检查会核对结构版本对应的源码新鲜度，而非仅检查 AST 文件自身有没有变化；发现变化与工具显式报告的失效都通过同一包装层记录。并行工具共享此记录，失效后返回的旧资料结果在交付前丢弃。

## 6. Workspace 授权合同

1. 每轮实际读取范围是 ready MaterialSource 的 root/mount 映射，不能由扫描 workspace 得出。工作区中存在一个目录不代表有权使用。
2. Read／Glob／Grep 使用虚拟相对路径。拒绝绝对路径、`..` 逃逸、范围外根及其别名；逐次解析 symlink/Junction 后检查真实路径，不能只做字符串前缀匹配。目录遍历、搜索、直接读取使用同一检查。
3. `denied_roots` 是服务端从资料配置得出的排除根：禁用/不可用的业务与结构产物、工程索引/数据库、评测评分、会话/Trace、模型凭证等。即使它们嵌套在源码根内，也不能通过 Read 或别名绕过。未出现的已知产物目录仍保留排除规则，运行中出现也不授权。
4. 非敏感普通源码、README、构建文件及正常项目资料保持可读，不按扩展名或内容含业务词而删减对照组。源码中的业务说明不等于单独授予 Business Context；禁用的是已识别的托管/生成资料来源，不是对普通 README 做语义屏蔽。
5. 起始授权还要扣除 RunAccess 的运行期失效记录；只校验文件自身版本不足以授权结构资料，其固定快照对应的源码也必须新鲜。隐藏凭证和内部运行文件不交给模型；工具没有 Write/Bash，不依赖操作系统把仓库改成只读权限。工作区/会话/Trace 可由服务程序写入，但不可被模型工具写入或读作证据。
6. 只读挂载不是不可变源码快照。读取采用内容摘要和真实行号；读取期间发生变更时重读一致版本或返回 changed，不拼接不一致原文。若部署要求抵御恶意并发链接替换，需使用基于已打开目录/文件句柄的校验，不能把一次 resolve 当作完整沙箱。
7. 两个范围或 Profile 的运行不得共享可被重新挂载的活跃 workspace；复用仅限同一授权指纹，刷新不得改变运行中的映射。历史工作区清理不能删除工程 Trace。
8. 索引只读查询与资料新鲜度检查可以读摘要，不能解析/嵌入/写回索引。core 不探测禁用索引的版本，禁用产物改变不影响 core 会话。

## 7. 工具与证据合同

### 7.1 通用结果

```python
EvidenceKind = Literal["source", "requirement", "business_context", "navigation"]

@dataclass(frozen=True)
class AvailabilityChange:
    capability_ids: tuple[str, ...]
    material_ids: tuple[str, ...]
    state: Literal["stale", "unavailable"]
    reason: str

@dataclass(frozen=True)
class Evidence:
    id: str | None
    project_id: str
    material_id: str
    kind: EvidenceKind
    path: str
    line_start: int
    line_end: int
    text: str
    content_hash: str
    source_version: str | None
    tool_call_id: str | None
    current: bool

@dataclass(frozen=True)
class ToolResult:
    status: Literal["ok", "empty", "invalid_input", "denied", "unavailable", "error"]
    data: Mapping[str, Any]
    evidence: tuple[Evidence, ...] = ()
    truncated: bool = False
    continuation: Mapping[str, Any] | None = None
    reason: str | None = None
    availability_changes: tuple[AvailabilityChange, ...] = ()
```

AvailabilityChange 至少指定一个本轮 capability/material ID；包装层校验其归属和失效原因，按第 4 节资料—能力关系收缩授权，不能影响另一工程或恢复 ready。`status=unavailable` 且变化列表为空只是单次文件/查询不可用；整个能力/资料失效必须携带变化列表。结构过期统一用 `status=unavailable` 加 `state=stale`，不靠自由文本判断。ToolAccess.check 对已失效或新发现失效的对象返回相同结构；对仍授权对象返回 None。新发现失效由检查器同步送入同一事件/失效登记通道，不等待下次调用。

失效登记在任何结果交给模型前完成；关联旧 Evidence 被标记无效，后续模型请求移除/标废对应正文及依赖摘要，最终引用校验不再接纳。原始 Trace 保留当时交付内容与后续失效事件，不覆盖旧事件。整个 required 资料/能力失效由包装层终止运行；optional 失效只保留其他有效来源。

Evidence 的封装前/后共用同一类型：提供者必须显式令 `id=None, tool_call_id=None`；运行内包装层分配唯一非空 ID、填入当前真实调用 ID，再交付给模型/Trace/引用表。边界序列化拒绝任一 ID 为空或来自提供者/模型伪造的证据，提供者不维护另一套运行上下文。`content_hash` 是实际读取文件字节的 SHA-256；text 是所交付行区间的原文。每个片段只能表示连续且完整的行，不补齐未读取的空洞。

`source` 只用于源码/配置原文；仓库 README 作为项目背景归 navigation，不能仅因为所在 MaterialSource.kind=source 就升级为实现证明。需求/业务正文分别为 requirement/business_context；AST Markdown、符号/关系命中和检索排名均为 navigation。无原文的关系元数据放在 data，不制造虚假行号 Evidence。

- `current=true` 只表示与本次核对时的资料一致，不是语义正确；索引快照片段不可未经当前文件核对就标为当前源码。
- 模型实际收到的证据与 Trace 记录一致。因单次上限截断的半行/半字段片段仅是线索，不建立完整行证据；返回续读位置。
- 空结果表示此范围/版本/查询未命中，不表示实现不存在。跳过大文件、编码失败和截断数量放入 data 的 skipped 信息，不能冒充完整检索。
- `denied` 不泄露未授权路径正文；错误不包含凭证。模型提出新的代码分析工具名时，未注册即拒绝，不动态加载。

### 7.2 FileCapability 首批工具

| 名称 | 输入合同 | 输出重点 |
| --- | --- | --- |
| Read | `path: str, offset: int=1, limit: int=160` | 真实 lineStart/lineEnd/totalLines、原文证据；截断后 `continuation={path, offset}` |
| Glob | `pattern: str, limit: int=100, kind: files或directories=files, offset: int=0` | 按虚拟路径排序的 paths；`**` 跨零层/多层；分页 offset 与截断标记 |
| Grep | `pattern: str, path: str="", glob: str="", literal: bool=False, limit: int=60, case_sensitive: bool=True, whole_word: bool=False, offset: int=0` | 匹配 path/line/text、跳过原因、分页位置；关键结论再 Read |

Read 行号从 1 开始，每次最多 240 行，文件超过 2,000,000 字节返回明确限制，不悄悄读取整份文件。Glob 每页最多 200 项，Grep 每页最多 120 项/每行展示最多 600 字符；分页 offset 是稳定排序结果的零基偏移，资料变更后必须重新定位。单次结果另受字符上限约束并记录实际限制，不能静默丢弃正文。非法正则/越界参数返回 invalid_input，无效路径返回 denied 或 unavailable，不用 shell 拼接命令。

Grep 默认 `path=""` 仅搜索所有授权根，而不是宿主磁盘或整个 workspace。Glob 的目录结果同样不能暴露未授权根。Glob/Grep 输出只作为定位线索，首批 core 通过 Read 产生可核对的原文证据。

### 7.3 后续能力的最小对接面

| 提供者 | 工具/指导 | 边界 |
| --- | --- | --- |
| BusinessContextCapability | tools 返回空元组；instructions 标明目录、适用范围、术语与当前实现需分别核对 | File 工具读取已授权正文；不创建流程调度器、不维护第二套知识格式 |
| AstCapability | FindCodeRelation | 读取用户显式生成的固定版本；关系缺失时返回缺口，不推断未支持关系 |
| CodeSearchCapability | SearchCode | 读取已准备快照，过滤 source/其他资料权限，原文与排名分开 |
| ContractCapability | 契约公开只读工具，沿用真实名字并登记 schema | 字段/连接结果与源码分别交付；不携带 V1 自动补查/收尾流程 |

扩展工具统一使用 ToolResult。为场景走查冻结以下最小输入；具体关系集合由提供者真实能力描述，不冒称全部支持：

- `FindCodeRelation(query: str, relation: str, repository_ids: tuple[str, ...]=(), limit: int=20)`：query 可为全限定符号或候选 ID；relation 支持声明的 symbols/calls/implementations 等。空 repository_ids 表示本轮已授权交集。返回 `version, candidates, relations, unresolved`，每个关系有两端 project/repository/symbol、可读 source path/行区间及 resolution 状态。歧义不自动选第一个；消息消费者/任务若未被解析，返回 unsupported 或 unresolved，模型自行用 Grep/Read 补查。
- `SearchCode(query: str, path: str="", limit: int=10)`：返回候选排名、来源、版本、截断/新鲜度与可续读位置；只有核对当前授权文件且实际交付的完整行片段才作为 source Evidence，其余只作导航。语义模型不可用时 optional 能力缺席，不隐式下载。

增加工具只改变提供者、注册清单/Profile 和对应适配器，不在 DirectRuntime 中添加 `if ast`、`if business` 或按工具名执行补查分支。

## 8. Runtime、事件与错误合同

```python
EventCallback = Callable[[Mapping[str, Any]], None]
CancelCheck = Callable[[], bool]

@dataclass(frozen=True)
class RuntimeSettings:
    provider: str
    endpoint: str
    model: str
    model_parameters: Mapping[str, Any]
    runtime_revision: str
    system_prompt: str
    context_max_chars: int
    tool_result_max_chars: int
    request_timeout_seconds: float

@dataclass(frozen=True)
class RunOptions:
    max_calls: int | None = None
    max_seconds: float | None = None

class AgentRuntime(Protocol):
    runtime_name: str
    def ask(
        self,
        question: str,
        *,
        context: RunContext,
        event_callback: EventCallback | None = None,
        cancel_check: CancelCheck | None = None,
        options: RunOptions = RunOptions(),
    ) -> RuntimeResult: ...
```

沿用当前 RuntimeResult 的 `answer, runtime_session_id, events, usage, status` 和序列化键，不为每个能力定义另一套 Answer。现有 normalize_runtime_result 继续用于兼容；本阶段不改签名。未来 LegacyAdapter 将 context 转为当前 `workspace/session_id/repositories/preparation` 输入，适配位于 query 边界，不要求修改旧 LangChainRuntime。

- RuntimeSettings 是会话核对前解析的公开有效设置；端点须去除认证信息，model_parameters 仅允许非敏感的有效模型参数，深复制/冻结后使用。凭证由组合根单独注入模型连接，不参与此对象。运行中不得再修改设置；其 runtime_revision/system_prompt 随实现或提示变化参与指纹。
- 生产 RunOptions 两项为 None，无模型调用次数/调查总时长限制。有界评测可明确传正值，max_seconds 从 ask 开始计时，调用额度包含必要的最终答题调用；到限时不能超限再请求模型，保留已有输出/缺口与 `stopReason`。该参数不能改变 Profile 授权，必须与工厂绑定及会话指纹中使用的值相同。
- 模型连接、上下文容量、单次网络超时由 RuntimeFactory 按已冻结 RuntimeSettings 注入并保存，不在每个工具中重新读 `.env`。取消检查在模型请求前后及工具边界执行，能中断可取消的请求；不能在取消后继续安排新工具。
- `status=completed` 表示本次执行成功产生回答，不表示业务问题完全解决。`cancelled` 允许空答案；失败通过现有 RuntimeErrorBase 体系抛出，由服务记录 failed，不填造回答。required 预检失败映射 RequiredToolUnavailable/QueryConfigurationError。
- 工具错误先交给模型；底层必需能力整体失效、模型调用失败和持久化失败不能伪装为工具空结果。失败不自动切换到 Legacy。

事件继续使用现有 `eventType/subtype/payload` 信封；新增细节放 payload，不要求改前端。服务分配单调 sequence，Runtime 分配稳定 eventId/toolCallId；流式事件和 RuntimeResult.events 中同 ID 只持久化一次。至少记录 run/context、capability_status、model_request、model_response、tool_use、tool_result、verification、error、stop，并区分 `origin=model` 与 Legacy 的 `origin=workflow`。Direct 不产生程序自动调查工具调用。

公开请求/响应、工具 schema、完整实际结果、来源与停止原因持续写工程 Trace。`usage.references` 保存引用与 Evidence ID；`usage.verification` 保存来源检查结果、缺口和 `semanticProof=false`；`usage.execution` 保存 Profile 定义、起始/实际能力状态、资料、会话指纹和运行设置。禁止记录认证头、密钥和模型私有思考。先持久化事件再向客户端发送；客户端断开不删除记录，写入失败不得报告成功。

引用检查只核对已交付原文/行号/版本，不能在校验阶段从磁盘补证据、隐式调用工具或重新进入调查循环。无效引用保留校验失败及未确认提示，不能以通过格式检查宣称语义正确。答案应分别陈述当前实现、需求目标、业务约定及结构线索；冲突分别说明，不让一种证据覆盖另一种。

## 9. 会话身份与历史隔离

会话身份使用规范化 JSON（固定键序，集合排序，保留有意义的指令顺序）计算 SHA-256。字段至少为：

- 工程 project_id + 规范化 config/data/db 根身份，避免同名工程或兼容 default 相撞；
- 展开的 scope、完整 Profile、runtime/适配版本、系统提示与阅读指导；
- 有效模型连接标识（供应商/端点/模型/影响输出的非敏感设置，不含凭证）；
- ready 资料 id/kind/mount/root/仓库归属/version 与 denied_roots；
- 能力策略/实际状态/采用版本、实际工具名/description/schema；
- 上下文和工具上限、答题模式、显式评测限额等影响执行的设置。

`run_id`、问题正文、session_id、时间戳及不影响行为的 reason 文案不参与指纹。disabled 产物版本不探测也不参与；optional 从缺席到 ready 或 ready 到 stale 会改变身份。指纹缺失或不匹配时不恢复旧模型消息、摘要或工具结果，即使前端仍使用同一 conversationId。

ProjectContext 归属必须先校验；外工程 conversation/session/version 引用直接拒绝，不通过清空 session 静默接纳。相同会话有活跃 running/cancelling 运行时拒绝并发追问。SessionStore 由工程根和身份隔离，不接受客户端传磁盘 session 文件路径。

相同身份下源码文件变化不要求预建全库快照：恢复时检查历史已读文件内容摘要，变化/删除的原文及依赖摘要标为失效，不作为当前证据；需要时由模型重新 Read。业务/需求/结构版本变化直接重建会话。最终引用应来自本轮交付或本轮重新校验并重新交付的原文，旧回答文本不算证据。

旧 run 的 Profile/mode/AST 版本/Trace 永不被新一轮补写。历史没有执行快照时展示未知/原有信息，不推断它当时是 core 或 legacy。新一轮可选择新 Profile，但必须创建新执行记录与新的模型会话身份。

## 10. 接口验收最小用例

以下覆盖完整设计，不要求阶段 1 一次全部实现。core 先验 File、逐一仓库完整性、禁用能力零探测、会话/授权、基本原文引用、Trace 与停止；Business Context、AST、共享失效和复杂 Evidence 用例随相应能力接入验收。

- 将所有结构解析/索引准备函数替换为「调用即失败」的测试桩，core 仍可从三个仓库完成 Glob/Grep/Read；磁盘有索引或没有索引两种情况工具清单一致。
- 注册一个返回固定 ToolResult 的新只读能力，Direct 循环不修改即可调用；未注册工具与未知 Profile 被拒绝。
- Business Context 无新增工具也能提供资料指导；AST 工具只给导航，Read 后才产生源码证据。
- optional 缺失/过期继续 File；required 缺失启动前失败；disabled 不调用 status；运行期失效不更换 Profile/版本。
- A/B 工程同 repo ID、同 session ID、同版本字符串不能交叉引用；缩小 scope、切换 Profile、资料/工具版本变化不能恢复旧消息。
- 托管结构目录嵌套在源码中、symlink/Junction 指向范围外、工作区残留旧 AST、历史 session 含业务正文，均不能绕过 core 授权。
- settings/options 在会话指纹、工厂和 ask 之间一致；在 ask 时更换限额须在恢复会话前拒绝，不允许先恢复旧消息再计算新身份。
- 业务能力不可用时 File 不能读取对应资料；AST 工具或 File 任一发现结构对应源码过期，另一工具也必须拒绝旧结构正文。单文件 unavailable 不触发 required File 整体失败。
- 提供者产生的 Evidence 使用空 id/tool_call_id，包装层分配后必须非空；模型/提供者伪造 ID 拒绝。
- 取消、模型错误、无效引用、截断分页、源码变化和客户端断开均有可追溯事件；既有 RuntimeResult/API/历史记录保持可读取。

这些是后续实现测试要求，本阶段仅完成接口级走查，不能标记为已经运行通过。
