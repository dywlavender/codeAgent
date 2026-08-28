# 当前架构

## 产品目标

系统是一个面向老项目的 Agent 问答工作台。人工业务文档告诉 Agent“有哪些功能、涉及哪些工程、从哪里开始查”；代码索引提供当前实现事实；Query Agent 最终回到业务文档和当前代码形成答案。

```text
Git 仓库 → Code Fact
功能文档 → 人工功能定义 → 入口与关键表定位 → 功能分析
                                          ↓
用户问题 → 功能匹配 → 检索计划 → 当前代码/业务文档证据 → 答案
```

## 三层功能知识

### 人工功能定义

来自 `knowledge.root` 目录中的 Markdown 文件，只包含：

- 名称、别名和标签；
- 简短功能说明；
- 业务场景；
- 工程、入口类型和入口类；
- 关键表及数据作用；
- 可选补充说明。

人工文档不填写调用链、流程和规则。

### 检索索引

更新知识库时确定性生成：

- 入口类的准确代码符号和文件位置；
- 入口附近的直接调用提示；
- 关键表的 MyBatis 读写位置；
- 功能、工程、入口、代码和表之间的导航关系。

无法唯一定位的入口标记为 `AMBIGUOUS` 或 `NOT_FOUND`，不让模型自行选择。

### Agent 功能分析

模型只读取当前功能的有界代码证据，生成不超过八步的流程摘要和少量核心规则。每一条必须引用已有代码 Evidence；证据不足时不生成。该结果用于后续检索导航，不替代当前代码和人工文档。

三层分别存入 `functional_knowledge`、`functional_*_anchor/link` 和 `functional_analysis`，代码刷新不会改写人工功能定义。

## 两个 Agent

- Knowledge Update Agent：扫描功能文档、定位入口和关键表、生成有证据的功能分析。
- Query Agent：匹配功能索引、规划需要检查的工程与代码位置、读取原始证据并回答。

Query Agent 不修改知识；Knowledge Update Agent 的自动摘要不能直接成为无需核实的最终结论。

## LangChain 1.2

模型通过 `langchain.chat_models.init_chat_model` 和 `langchain.agents.create_agent` 接入，使用 Pydantic 结构化输出。模型配置全部来自根目录 `.env`。未配置模型时仍可完成文档解析、入口定位和关键表关联，分析状态显示为 `INDEXED`，不会生成虚假流程或规则。

## 管理接口

- `POST /api/knowledge/refresh`：扫描功能文档并重建检索索引，可同时触发分析。
- `GET /api/knowledge/functions`：查询功能列表。
- `GET /api/knowledge/functions/{id}`：查看人工定义、入口、关键表、索引和分析结果。
- `POST /api/knowledge/functions/{id}/analyze`：重新分析一个功能。
- `GET /api/knowledge-graph`：查看功能、工程、入口代码、关键表和标签的只读投影。

写操作使用 `admin.apiTokenEnv` 配置的管理员口令；读取接口保持只读。

## 图谱边界

图谱是检索导航，不是第二套事实库。第一版只展示：

```text
功能 → 工程 → 入口代码
功能 → 关键表 ← 读写代码
功能 → 标签
```

调用关系和表读写关系必须来自 Code Fact；人工登记关系来自功能文档；Agent 摘要不直接生成无证据图边。
