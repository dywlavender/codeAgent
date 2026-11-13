# 文档索引

`docs/` 分两类，改动功能前只需要读 `spec/`；`records/` 是历史记录，用于追溯结论来源。

## spec/：现行约定

| 文档 | 作用 |
| --- | --- |
| [知识主干与知识图谱：项目整改指导方向](spec/knowledge-backbone-direction.md) | 知识体系的权威依据。涉及项目认知、业务补充知识、代码地图、知识图谱、入口导航、问答调查的设计与修改前必须先读 |
| [当前架构](spec/architecture.md) | 记录实现现状：Python 边界、工作区、多工程隔离、Runtime、数据库与 API |
| [业务补充知识使用指南](spec/business-baseline-guide.md) | 业务补充知识的目录约定与编写方式 |
| [知识主干自动对照验证](spec/knowledge-backbone-automation.md) | 题库配置、对照执行与报告生成的操作入口 |
| [检索诊断约定](spec/retrieval-evaluation.md) | 冻结原文、证据标注、结构单元、混合检索／精排预算、上下文选择及 Agent／固定证据对照 |
| [Windows 部署步骤](spec/offline-deployment.md) | 从生成项目包到首次启动、验证的完整顺序 |
| [Windows 启动与参数参考](spec/windows-usage.md) | 部署后的日常启动、参数和日志位置 |
| [多应用资料流程](spec/multi-application-flow.md) | 多应用项目的资料组织 |
| [前端设计系统](spec/frontend-design-system.md) | 颜色、间距与组件约定 |

`spec/` 中如有结论被新验证推翻，直接修订该文档，并在 `records/` 保留记录指向修订；不要新增"新版说明"与旧版并存。

## records/：历史记录

按年月归档，形如 `records/2026-09/`。内容包括知识主干验证、AST 对照、多工程隔离验收、提示词评审和前端方案等。这些文档记录当时的条件与结论，**不作为新实现的默认依据**；其中的路径与命令已随目录调整更新，但结论有效性以写入 `spec/` 的修订为准。

## 与仓库其他目录的关系

- `design/mockups/`：静态 HTML 原型，先于前端实现。
- `knowledge/baseline/`：历史兼容资料，现行目录为工程内的 `knowledge/business-context/`。
- `validation-projects/`：真实项目验证材料，各自的 `repos/` 与 `.data/` 不入库。
