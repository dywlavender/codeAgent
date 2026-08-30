# 当前架构

## 产品边界

系统包含两个面向不同角色的工作区：

- 用户端由 Query Agent 解答问题，只读取业务知识、代码事实、需求证据和 Mapping；
- 管理端导入人工业务基线、检查结构化知识和代码 Mapping。

当前实现覆盖 MVP1 和 MVP2：人工业务基线、结构化知识、代码映射、业务驱动问答，以及从充分问答证据中观察新的 Business-Code Mapping。问答观察不会自动改写人工业务语义。

## 三层知识

```text
Human Business Baseline
自然语言 Markdown
        ↓
Business Knowledge
System / BusinessTerm / Capability / Flow / Relation / Rule
        ↓ 独立 Mapping
Code Knowledge
Repository / File / Class / Method / API / Job / Table / Field
```

### Business Knowledge

业务知识回答：

- 业务概念是什么；
- 系统具有什么能力；
- 粗粒度业务流程是什么；
- 什么和什么存在业务关系；
- 哪些独立业务规则成立。

人工来源保存为 `HUMAN`，默认是已确认业务事实。模型只负责结构化，不得覆盖或补造原文没有表达的事实。实体名称、关系端点、别名、`codeHints` 和其他属性值均须有原文逐字依据；无法逐字落地的模型字段会被丢弃，定义则回退为引用片段中的原句。

### Code Knowledge

代码事实由 Java/MyBatis 索引器确定性产生，包括 Symbol、调用、字段读写与校验、表和列访问。业务模型与代码模型不共用实体。

### Business-Code Mapping

Mapping 单独保存：

```text
BusinessTerm  -- REPRESENTED_BY --> Code Symbol
Capability    -- IMPLEMENTED_BY --> Code Symbol
Flow          -- IMPLEMENTED_BY --> Code Symbol
Rule          -- ENFORCED_BY    --> Code Symbol
System        -- OWNED_BY       --> Code Symbol
Relation      -- EVIDENCED_BY   --> Code Symbol
```

Mapping 可以随代码重新计算，业务知识保持不变。候选不唯一或没有找到时分别保存为 `CANDIDATE` 和 `UNRESOLVED`。

### MVP2 Mapping 观察

问答完成后，`MappingObservationService` 只读取该回答最终 `facts` 实际引用的证据：

```text
业务实体/关系证据 + 代码 Symbol 证据 + SUFFICIENT
                    ↓
business_code_mapping_observation (CANDIDATE)
                    ↓ 管理员确认
business_code_mapping (VERIFIED, QUERY_REVIEW)
```

观察记录保存问题、候选 Business-Code 对、证据编号和可信度。未被最终回答引用的 `businessCandidates` 只能作为检索导航提示，不能触发观察记录。观察记录与 `business_baseline_source`、`business_entity` 分开，因此不会把一次问答的推断写回人工 Markdown。静态映射重建只清理 `source_type=CODE` 的计算结果，保留管理员确认过的 Query Mapping。

## 导入流程

```text
扫描 Markdown
→ 读取自然语言原文
→ 模型结构化或安全回退解析
→ 验证每条知识有原文片段
→ 保存六类知识和来源 Evidence
→ 从名称、别名、业务动作和代码提示构建搜索计划
→ 在当前 Code Fact 中排名候选
→ 保存 Mapping 状态与代码 Evidence
```

没有配置模型时，只提取标题、明确原句、英文别名和显式流程项，不生成无法证明的规则。

## SQLite 主模型

业务侧：

- `business_baseline_source`
- `business_entity`
- `business_relation_v2`

映射侧：

- `business_code_mapping`
- `business_code_mapping_observation`

代码和证据侧复用：

- `repository`
- `code_file`
- `code_symbol`
- `code_fact`
- `evidence`
- `evidence_lifecycle`

历史 `functional_*` 表暂时保留兼容读取，不再作为新业务基线的主模型。

## Query Agent

Query Agent 的检索顺序是：

```text
理解问题
→ 同时检索业务知识、代码事实和需求摘要
→ 从业务实体与关系取得 Mapping
→ 优先检查映射到的代码 Symbol
→ 证据不足时扩大字段、表和调用关系检索
→ 输出事实、推断、冲突和未知项
```

业务知识负责缩小代码调查空间，不能替代代码证据。`CANDIDATE` Mapping 只能作为搜索线索，不能直接成为回答中的已确认事实。

问答输出中若产生候选映射，会附带 `mappingSuggestions`。管理员确认或忽略候选后，后续问题即可复用确认过的 Mapping；拒绝不会改变业务实体和关系。

## 管理端

“业务知识维护”页面支持：

- 导入业务基线；
- 按六类知识浏览；
- 查看自然语言来源；
- 查看业务关系；
- 查看候选、已验证和未定位 Mapping；
- 查看问答发现的 Mapping 候选，并确认或忽略；
- 在不改业务知识的情况下重新映射当前代码。

## 当前限制

- 代码索引以 Java 和 MyBatis 为主；
- 无模型时的业务结构化是保守能力，不等价于完整语义理解；
- MVP2 只把充分证据下的 Business-Code 关联写入候选观察，不自动把每次问答写回业务知识；
- 第一阶段不根据 Git 变化自动修改业务语义，只允许重建 Mapping；
- 业务关系的跨文档实体合并将在后续知识更新阶段处理。
