# MVP2：问答驱动的 Business-Code Mapping

## 目标

第二阶段只验证一件事：一次高质量问答能否发现新的业务对象—代码实现关联，并让下一次检索更快。它不负责修改人工业务基线，也不引入新的业务知识类型。

## 运行方式

1. 先完成代码索引和 MVP1 业务基线导入。
2. 在问答页提出具体的业务行为问题。
3. 只有回答最终 `facts` 同时引用业务 Evidence、代码 Evidence 且状态为 `SUFFICIENT` 时，才会创建候选；检索阶段的 `businessCandidates` 不会单独触发候选。
4. 在“业务知识维护”页查看“问答发现的映射候选”。
5. 管理员确认后，候选写入正式 `business_code_mapping`；忽略则只关闭候选。

## 数据边界

```text
business_baseline_source / business_entity / business_relation_v2
        人工维护的业务事实（不被问答改写）

business_code_mapping_observation
        问答发现的候选，带 run、问题、证据和可信度

business_code_mapping
        已确认或静态计算的 Business-Code 关联
```

观察记录只保存证据编号，不复制回答原文。候选状态有：

- `CANDIDATE`：等待管理员确认；
- `ACCEPTED`：已确认，并已写入正式 Mapping；
- `REJECTED`：管理员忽略。

## HTTP 接口

查询候选：

```http
GET /api/knowledge/mapping-observations?status=CANDIDATE
```

确认或忽略：

```http
POST /api/knowledge/mapping-observations/{id}/accept
POST /api/knowledge/mapping-observations/{id}/reject
```

确认接口可带一个简短说明：

```json
{"note":"该服务是极优提款后的担保文件入口"}
```

查询接口响应中的 `mappingSuggestions` 是本次问答刚产生或已存在的候选列表。候选不是回答事实，回答生成仍然只使用代码、业务和需求 Evidence；观察器也只使用最终回答事实实际引用的 Evidence ID。

## 为什么需要人工确认

业务名称和代码命名可能重叠，也可能一个业务能力由多个类共同实现。自动观察适合提出线索，不适合直接认定语义。确认动作将候选来源标记为 `QUERY_REVIEW`；代码重新索引时，静态计算结果会更新，但已确认的 Query Mapping 会保留。
