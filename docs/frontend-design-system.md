# Code Atlas 前端设计系统

这套界面是面向开发人员和业务分析人员的内部知识工作台。核心原则是“安静、可信、证据优先”：问答区保持对话感，证据区保持可核验，治理区保持审核动作清晰。

## 令牌分层

令牌定义在 [`frontend/src/design-tokens.css`](../frontend/src/design-tokens.css)，组件样式在 [`frontend/src/styles.css`](../frontend/src/styles.css)。禁止业务组件直接写颜色值；新增样式应优先引用语义或组件令牌。

| 层级 | 负责内容 | 示例 |
| --- | --- | --- |
| Primitive | 原始色板、间距、圆角、字体、阴影 | `--primitive-brand-500`、`--space-4` |
| Semantic | 产品语义和状态含义 | `--color-canvas`、`--color-text`、`--color-danger` |
| Component | 组件的稳定契约 | `--component-nav-active-bg`、`--component-button-primary-bg` |

深色主题只覆盖语义层，组件不需要写第二套颜色。`styles.css` 中保留的 `--accent`、`--surface` 等变量只是历史选择器的兼容别名，新的组件应使用 `--color-*` 或 `--component-*`。

## 字体规范

- 界面正文使用 `--font-body`，优先使用系统无衬线字体，中文使用苹方、思源黑体或微软雅黑回退。
- 标题使用 `--font-display`，只用于页面标题、产品名和关键结论。
- 代码符号、Evidence ID、运行时长、版本号使用 `--font-code`，保证等宽对齐。
- 字号使用 `--type-xs` 到 `--type-display`，不在组件里新增随意的字号档位。

## 图标规范

图标统一来自 `@phosphor-icons/react`，不再引入手写 SVG 或混用其他图标库。

- 导航：19px，当前项使用 `fill`，其他项使用 `regular`。
- 页面操作：15–18px，和按钮文字垂直居中。
- 空状态或欢迎状态：25–28px，只使用一个主图标。
- 图标只表达动作或来源类型，不能替代文字标签；所有无文字按钮必须提供 `aria-label` 或 `title`。

## 组件契约

| 组件 | 视觉契约 | 交互契约 |
| --- | --- | --- |
| Nav rail | 浅色表面、右侧边界、激活项使用品牌浅底 | 用户工作区（问答、运行记录）与治理/知识库分隔；窄屏折叠为底部导航 |
| Topbar | 64px 高、项目上下文在左、知识源健康度在右 | 只承载上下文和工作台切换，不放业务提交动作 |
| Composer | Raised surface、输入聚焦有统一 focus ring、主按钮为品牌色 | Enter 发送，Shift+Enter 换行；发送期间禁用并显示加载图标 |
| Answer card | 结论、业务流程、事实、待确认分段展示 | 每轮可切换，建议追问进入下一轮，反馈动作写入服务端 |
| Evidence card | 按 CODE / BUSINESS / REQUIREMENT 分组，状态色只表示来源 | 点击展开原文或结构化引用，不在卡片中编辑知识 |
| Status badge | `sufficient` 品牌色、`insufficient` 琥珀色、`conflict` 红色 | 只描述证据状态，不使用颜色单独传递信息 |
| Admin action | 接受为主按钮，驳回为危险按钮，暂缓为次按钮 | 接受、驳回、暂缓均需要明确的当前提案上下文 |

## 页面信息架构

1. **问答 Agent**：面向普通使用者，围绕自然语言问题返回结论和证据。
2. **运行记录**：查看可解释的 Agent 步骤、工具调用和证据状态，不展示隐藏推理。
3. **知识治理**：面向管理员，生成、审核和发布知识变更提案。
4. **Code Fact / 功能知识 / Requirements**：按来源浏览已发布的结构化知识和证据。

这种划分把“问问题”和“维护知识”分开，减少普通使用者误触发布动作，也让治理操作有独立的审核上下文。
