# 当前架构

## 产品边界

系统分成两个工作台，但共用同一个证据和知识底座：

- 用户端 Query Agent：只读已发布业务知识、代码事实和需求证据，负责回答问题。
- 管理端 Knowledge Update Agent：分析知识来源，生成差异提案；管理员审核后才能发布。

```text
用户问题 → Query Agent → 已发布功能知识 + Code Fact + Requirement Evidence

代码变化 / 需求 / 文档 / 人工说明 / 用户反馈
        → Knowledge Update Agent → 提案 → 管理员审核 → 新知识版本
```

两个 Agent 不共享写权限。Query Agent 不能修改知识，Knowledge Update Agent 不能批准或发布自己的提案。

## 四层知识结构

### 原始来源

Git 代码、需求文档、业务文档、管理员说明和用户反馈。原始内容保持可追溯，不直接等同于业务事实。

### 客观事实

Java / MyBatis 索引器自动维护类、方法、字段、直接调用、字段活动、表列读写和源码 Evidence。代码变化会使旧 Evidence 进入历史状态，但不会自动改写业务语义。

### 业务功能知识

正式知识以 `business_function` 为中心，每个发布版本包含：

- 功能摘要和业务域
- 业务场景
- 核心业务规则
- HTTP、消息、批任务等功能入口
- 业务级数据影响
- 各知识项的 Evidence 引用

详细源码、完整调用链、SQL 和需求原文不复制进知识正文，需要时按 Evidence 读取。

### 治理状态

更新 Agent 先创建 `knowledge_update_proposal` 和逐项差异。状态流转为：

```text
DRAFT → PENDING_REVIEW → APPROVED → PUBLISHED
                         ├→ REJECTED
                         ├→ DEFERRED
                         └→ CHANGES_REQUESTED
```

只有 `APPROVED` 能发布。发布产生新的不可变 `business_function_version`；更新提案必须基于当前版本，避免旧提案覆盖较新的知识。

## LangChain 1.2 边界

Knowledge Update Agent 的模型适配使用：

- `langchain.chat_models.init_chat_model`
- `langchain.agents.create_agent`
- Pydantic `response_format` 结构化响应

没有使用旧式 `LLMChain` 或 `ConversationChain`。模型负责有界语义分析和差异建议，领域代码继续负责：

- 候选功能范围限制
- Evidence 引用范围校验
- 提案状态机
- 审核和发布权限
- 基础版本校验

模型配置统一来自根目录 `.env`，CLI 启动时自动加载，且 `.env` 优先于当前进程环境变量。`project.config.json` 保存项目、仓库、启动默认值和管理员入口；启动脚本的显式命令行参数优先于配置文件。Knowledge Update Agent 使用模型生成知识提案；Query Agent 使用模型理解问题、处理同一会话的追问并归纳回答。Query Agent 只能绑定三个只读工具：Code Fact、已发布/确认业务知识、Requirement Digest。证据加载、状态过滤、冲突、充分性和引用校验不交给模型。没有模型时两者都回退到确定性流程，并在结果中标记 `FALLBACK`。知识治理接口与普通问答接口分开；治理接口使用 `admin.apiTokenEnv` 指定的管理员口令，非本机监听时未配置口令会拒绝启动。模型启用但缺少凭据时，服务启动会输出警告并继续降级，不会伪装成模型结果。

## 内容规模控制

1. 查询默认读取功能摘要和相关规则，不预加载全部 Evidence。
2. 代码只保存事实和位置，不把源码复制到业务知识。
3. 一次来源变化生成聚合提案，不为每个字段单独生成业务知识。
4. 普通查询只使用当前发布版本，历史版本按需读取。
5. 代码重构、日志和技术细节变化停留在 Code Fact 层；只有可能改变业务语义的变化进入审核。
6. Query Agent 和 Update Agent 都使用有界候选数量和证据范围。

## 当前技术实现

- 后端：Python 3.11+
- Agent 框架：LangChain 1.2；Query Agent 采用“模型理解/归纳 + 确定性三轮 Evidence Loop”
- 存储：SQLite 关系模型和 FTS
- 代码解析：Tree-sitter Java，可选依赖缺失时使用保守解析器
- 前端：React + Vite
- 启动：macOS shell 与 Windows PowerShell/batch 一键脚本

系统不依赖图数据库。当前代码分析仍不做完整 Java 类型绑定、重载解析或跨方法数据流；涉及这些能力时需要引入编译器符号信息或专用静态分析工具。Query 会话保存最近消息摘要，接口请求可携带 `conversationId` 和最近几轮 `history`，不会把整段历史源码重复送入模型。

## 管理 API

同源服务 `:8082` 提供：

- `GET /api/knowledge-admin/pending`
- `GET /api/knowledge-admin/functions`
- `GET /api/knowledge-admin/proposals`
- `GET /api/knowledge-admin/proposals/{id}`
- `POST /api/knowledge-admin/proposals/generate`
- `POST /api/knowledge-admin/proposals/{id}/review`

`generate` 支持 `CODE_CHANGE`、`REQUIREMENT`、`DOCUMENT`、`MANUAL` 和 `USER_FEEDBACK`。`review` 支持 `ACCEPT`、`REJECT`、`DEFER`；接受操作会先完成审核状态转换，再单独执行发布校验。

问答接口支持 `conversationId` 和最近几轮 `history`。每轮仍单独生成 Query Run，但会话表保存用户问题和回答摘要，用于追问消解。`POST /api/query/{runId}/feedback` 只记录有帮助/没有帮助及可选说明，不直接修改知识。

`CODE_CHANGE` 的来源标识可解析为仓库、变化记录或文件，并从已索引事实中最多装配 20 个变化、40 条 Code Evidence；`REQUIREMENT` 使用当前 Digest、最多 30 条规则和 30 个 Chunk Evidence。传给模型的来源上下文最多 24,000 字符，受影响功能最多 8 个，避免全库内容进入一次模型调用。
