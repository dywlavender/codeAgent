# 功能知识模板已停用

第一阶段 MVP 已改为自然语言业务基线，不再要求人工填写固定 frontmatter、入口类和关键表。

请使用 [业务基线使用指南](business-baseline-guide.md)。原 `knowledge/functions` 目录只为旧数据兼容保留，新项目应配置：

```json
{
  "knowledge": {
    "baselineRoot": "knowledge/baseline"
  }
}
```
