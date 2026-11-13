# AST 实现机制与前端展示方案（2026-09-10）

本文梳理当前代码库中 AST 资料的生成、解析、存储、消费链路，指出做可视化展示前必须解决的缺口，并给出前端展示方案与接口设计。文中所有结论对应仓库中的实际实现，标注 `文件:行号`。

可预览原型：`design/mockups/ast-explorer.html`（本文第 5 节的视觉稿，未改动任何工程源码）。

---

## 1. 结论摘要

1. 项目里的 "AST" 不是内存语法树，也不对外暴露树结构。它是 **tree-sitter 解析 → SQLite 索引 → 机械渲染成 Markdown 结构地图** 的一条离线管线，产物只有 `.md` 文件（`evaluation/ast_docs.py:1-10`）。
2. 解析层只覆盖 Java（tree-sitter），MyBatis XML 用 `ElementTree`，Vue/TS/JS 用正则（`web_intelligence.py:33-38`），三者精度不同，不能统称完整 AST 解析。
3. **结构化的索引库是临时的**：生成时在 `tempfile` 里建 `ast.db`，函数返回即随临时目录销毁（`ast_docs.py:39-56`）。版本目录里只留 `documents/*.md` 和 `metadata.json`。这是前端做树形展示的最大障碍。
4. 现有 4 个接口返回的全是 Markdown 文本或文件清单，前端只能渲染 Markdown，无法按符号展开、过滤、定位源码行。
5. AST 作为"独立结构资料"参与三处消费：问答模式 `mode=ast`、评测对照臂、AST 管理页。问答侧通过软链接挂进工作区，由 Agent 按需 `Glob/Read`（`query_agent/workspace.py:227-240`）。
6. 实测产物（`.data/ast/versions/ast-20260909-223144/`）共 8 份文档、44 KB，无索引库文件，可确认第 3 点。

---

## 2. 后端实现机制

### 2.1 模块分层

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 编排 | `evaluation/ast_service.py` | 版本目录、状态机、生成线程、鉴权后的 API 入口 |
| 渲染 | `evaluation/ast_docs.py` | 索引 → Markdown 分片、总览、导航 |
| 解析 | `code_intelligence.py` | Java tree-sitter/正则、MyBatis XML、符号与事实落库 |
| 解析（Web） | `web_intelligence.py` | Vue/TS/JS 模式解析（继承 `JavaIndexer`，无 tree-sitter） |
| 存储 | `schema.py:25-77` | `code_file / code_symbol / code_fact / evidence / evidence_lifecycle / parse_diagnostic` |
| 消费 | `query_agent/service.py`、`query_agent/workspace.py`、`evaluation/harness.py` | 问答、工作区挂载、评测对照 |
| 接口 | `query_agent/api.py:269-272, 435-445` | 4 个 HTTP 端点 |

### 2.2 生成与版本管理

`AstService`（`ast_service.py:64-83`）：

- 数据根：`<project_config>.parent/.data/ast`，内含 `index.json` 与 `versions/`。
- 状态机 `missing → generating → available → stale / failed`（`ast_service.py:65`）。
- `generate()`（`ast_service.py:169-183`）用 `RLock` 防重入；重复触发返回已有 `generationId` 且 `duplicate: true`；实际工作交给 daemon 线程 `_run_generation`，进度写内存字典 `_active`，前端靠轮询读 `status()` 拿到。
- `_run_generation`（`ast_service.py:185-235`）流程：

  1. `resolve_project_sources` 取配置中的仓库列表，算 `sourceStamp`（`fileCount` + `latestModifiedAt` + 仓库路径，不含内容哈希，`ast_service.py:42-61`）。
  2. `copytree` 把每个仓库拷进 `tempdir/inputs/repo-N`，忽略 `.git / node_modules / target / .venv / __pycache__` —— 这一步是"冻结源码"。
  3. 调 `generate_ast_documents(inputs, repository_names)` 得到 `{文件名: Markdown}`。
  4. 写入 `versions/ast-YYYYmmdd-HHMMSS/documents/`，同秒冲突加 `-2` 后缀。
  5. 写 `metadata.json`：`projectId / versionId / generatedAt / sourceStamp / sourceRepositories / documentCount / generationSeconds / generatorVersion(=2)`。
  6. 更新 `index.json` 的 `currentVersionId` 与状态。

- `status()`（`ast_service.py:103-123`）每次调用都重算当前 `sourceStamp`，与版本 `metadata.sourceStamp` 比对，不等则报 `stale`；生成中则用内存进度覆盖。注意这是**全仓库 `rglob`**，仓库大时该接口会变慢。
- `read_document`（`ast_service.py:157-167`）做了路径穿越校验（目标必须在 `documents/` 之下），版本 ID 用 `ast-[A-Za-z0-9_-]+` 正则白名单。

### 2.3 解析层

**Java（tree-sitter 优先，正则兜底）**

`JavaIndexer._load_tree_sitter`（`code_intelligence.py:42-51`）：能 `import tree_sitter_java` 就用 `Parser`，否则告警并降级为内置正则解析器。降级不是静默的——`parse_diagnostic` 表记录 `backend = tree-sitter-java | conservative-pattern`（`code_intelligence.py:117-131`）。

`_ingest_file`（`code_intelligence.py:90-131`）先比 `content_hash`，命中且声明/集成已索引则跳过；命中变化时把旧证据标 `HISTORICAL` 再重建。

`_index_tree`（`code_intelligence.py:274-360`）是 AST 路径的核心：

- 递归 `visit_types`，识别 `class/interface/enum/record_declaration`，写 `code_symbol(kind=CLASS|INTERFACE|ENUM|RECORD)`，行号取 `start_point.row+1 / end_point.row+1`。
- 遍历类型 body：`method_declaration / constructor_declaration` 写 `kind=METHOD`（带 `@GetMapping` 等注解时升级 `API`）；`field_declaration` 写 `kind=FIELD`。
- 方法体内 `method_invocation` 由 `_invocations`（`code_intelligence.py:362-379`）收集，带祖先链；随后按命名与实参个数判语义（`code_intelligence.py:321-344`）：`setXxx(x)`(1 参) → `WRITE_FIELD`，`getXxx()`(0 参) → `READ_FIELD`，祖先含 `if/assert/throw` 时升级 `CHECK_FIELD`，其余 → `CALL`。
- tree-sitter 报 `root_node.has_error` 时**不信任语法树**，回落到正则路径（`code_intelligence.py:123-127`）。

**MyBatis XML**（`code_intelligence.py:381-456`）：`ET.fromstring` 解析 `<select|insert|update|delete>`，写 `MYBATIS_STATEMENT` 符号 + 表/列级 `READ_TABLE / WRITE_TABLE / READ_COLUMN / WRITE_COLUMN` 事实；`resultMap` 用于桥接 snake_case 列与 camelCase 属性。

**Web**（`web_intelligence.py:33-38`）：`WebIndexer` 继承 `JavaIndexer` 但把 `syntax_backend` 置 `None`，即**完全没有 AST 解析**，只用 `FUNCTION_RE / OPTION_METHOD_RE / HTTP_CALL_RE / FETCH_RE / ROUTE_PATH_RE` 等正则提取 `FUNCTION / PAGE / COMPONENT / ROUTE / HTTP_CALL`。产物粒度与 Java 不一致，`_area()` 对 `src/views/x.vue` 这类路径会退化成前 3 段，容易产生碎片分片。

**证据强制挂载**：所有事实都经 `_fact()`（`code_intelligence.py:498-511`）写 `evidence`（`path / line_start / line_end / excerpt / content_hash`）并置 `evidence_lifecycle='ACTIVE'`；文件被修改或删除时 `_archive_file_evidence` 把相关证据改判 `HISTORICAL`（`code_intelligence.py:483-493`）。这条规则对前端展示有意义：**每个可展示节点都天然带行号与生命周期状态**。

**行号语义（影响前端跳转）**：tree-sitter 的 `class_declaration / interface_declaration` 节点起点包含前置注解，因此索引里的 `line_start` 指向注解行而非 `class` 关键字。实测 `LoanMiddleClient.java` 的 `@FeignClient` 在第 13 行、`public interface` 在第 15 行，而分片中记为 `:13`；方法同理，`@GetMapping` 在第 16 行、`queryLimit(...)` 在第 17 行，分片记为 `queryLimit(L16)`。前端做"跳转到源码"时命中行会落在注解上，这是正确的，但高亮区间的下界不应再做 ±1 修正。

### 2.4 渲染层（索引 → Markdown）

`generate_ast_documents`（`ast_docs.py:31-64`）：

1. 在 `tempfile` 里开一个空库，逐个 `repo-N` 调 `CodeIndexer(db).ingest()`（也就是复用生产索引器，不是另一套解析）。
2. `_render_repository`（`ast_docs.py:86-155`）按 `_area(path)` 分片、按 `_group_key`（类型/方法的包路径，或文件的父目录）分组，输出 `## group` 段：
   - 类型行 `- Name — KIND (path:line)`
   - 端点行：`HTTP_ENDPOINT / HTTP_CALL / RPC_CALL / ROUTE`，类级 `HTTP_BASE_PATH` 会做前缀拼接（`ast_docs.py:99-112`）
   - 方法行：`name(L行号)`，超过 `MAX_METHODS_PER_TYPE=40` 截断并注明"另有 N 个"
3. `_split`（`ast_docs.py:158-172`）按 `MAX_DOC_LINES=240` 或 `MAX_DOC_CHARS=16000` 切分，单个类型块不拆散，命名 `<repo>-<area>-NN.md`。
4. 每仓库生成一份导航 `ast/<repo-N>-index.md`，最后生成 `project-index.md`，并写一份同内容别名 `project-overview.md`（旧评测批次可复现，`ast_docs.py:59-63`）。
5. `db.close()` 后临时目录销毁 —— **索引数据到此结束**。

### 2.5 消费端

**问答（`mode=ast`）**：`service.py:301-316` 在开跑前用 `AstService.current_version()` 取当前版本，拿不到就报"请先在 AST 管理页手动生成"；随后 `WorkspaceManager.ensure(mode="ast", code_map_source=<documents 目录>)` 把资料目录软链接到工作区的 `knowledge/generated-code-map`，并写进 `CLAUDE.md` 的检索范围（`workspace.py:236-240, 262-273`）。AST 模式下 `business_context_source` 被强制置空——**结构与业务语义资料互斥**。会话表记 `ast_version_id`，版本变化会重置运行时会话（`service.py:139-165`）。

**评测**：`harness.py:264-302` 把 arm（`ast` / `code_map` / `code_map_context`）映射到 mode，把冻结的 `baselines/<arm>` 拷成 `generated-code-map`，并按 `--comparison diagnostic_ast` 的五组设计统计接入证据（`astContentCalls`、`overviewInjected`、`referencePathsAccessed`，`harness.py:198-223`）。

**页面**：`AstPage` 看状态、触发生成、浏览文档。

### 2.6 与最新方向文档的一致性

对照 `docs/knowledge-backbone-direction.md` 第 10、11 节的约定逐条核对：

| 约定 | 当前实现 | 结论 |
| --- | --- | --- |
| 必须由用户在 AST 管理页手动生成 | 生成只挂 `POST /api/ast/generate`；`AstService.__init__` 仅读索引，页面进入、切换模式、启动实验都不触发生成 | 已落实 |
| 源码变化只标记"待更新"，不自动重生成 | `status()` 比对 `sourceStamp` 后返回 `stale` | 已落实 |
| 主对话与三组实验共用同一套已生成资料 | 问答走 `current_version()`，评测走冻结快照，都记录 `astVersionId` | 已落实 |
| AST 模式不提供人工业务主干 | `workspace.py:239-240` 在 `mode in {"ast","code_map"}` 时把 `business_context_source` 置空 | 已落实 |
| AST 模式提供"正常项目资料 + 手动生成的结构资料 + 自动代码地图"（11.2） | `mode=ast` 只把 AST 的 `documents/` 挂到 `knowledge/generated-code-map`，未同时挂 `code_map_root` 的产物——两个来源共用同一个槽位（`workspace.py:227-240, 248`） | **待确认** |

最后一条不一定是缺陷，取决于产品意图：若"自动代码地图"与"AST 结构资料"被视为同一种结构导航的两个来源，则应合并展示；若视为两类资料，当前槽位设计无法同时提供。建议在动前端之前先定这条，否则页面要把两个来源画在一棵树上还是两棵树上会反复改。

---

## 3. 现有接口

`query_agent/api.py`（工程路由：`/api/projects/<projectId>/...` 为规范形式，前端 `lib/api.js:15-20` 自动封装）：

| 方法 | 路径 | 鉴权 | 返回 |
| --- | --- | --- | --- |
| POST | `/api/ast/generate` | 管理员口令 | 202，`{generationId, status, phase, progress, startedAt}` |
| GET | `/api/ast` | 无 | `status()`：状态、当前版本、`current` 元数据 |
| GET | `/api/ast/documents?versionId=` | 无 | `{versionId, items:[{path, bytes}], metadata}` |
| GET | `/api/ast/document?versionId=&path=` | 无 | `{versionId, path, content}`（Markdown 原文） |

前端侧：`api.js:29-32` 把 `/api/ast/generate` 列入 `adminPath`，自动附 `Authorization: Bearer <token>`，401 时清 token 并弹解锁框（`AstPage.jsx:16-23`）。生成中用 1.8 秒轮询 `status`（`AstPage.jsx:47-52`）。

---

## 4. 做可视化前必须解决的缺口

| # | 问题 | 依据 | 影响 |
| --- | --- | --- | --- |
| 1 | 结构化索引未落盘，临时库随生成结束销毁 | `ast_docs.py:39-56`；实测版本目录只有 `documents/` 与 `metadata.json` | 无法提供树形/过滤接口，只能反解 Markdown |
| 2 | 接口只返回 Markdown 文本 | `api.py:438-445` | 前端无法做节点级展开、折叠、跳转 |
| 3 | 文档清单无层级与类型 | `list_documents` 只回 `path` 和 `bytes`（`ast_service.py:142-155`） | 214 份分片平铺在一个 List 里，找不到目标 |
| 4 | 行号缺快照保障 | `metadata.sourceStamp` 只有 `fileCount/latestModifiedAt`，无哈希（`ast_service.py:42-61`）；源码快照只在生成期存在 | 版本 `stale` 后按行号取源码会错位，需要显式降级提示 |
| 5 | 渲染存在重复标题 | 同一包下多个类型时，`## group` 在类型循环内重复输出（`ast_docs.py:131-148`）。实测 `repo-1-src-main-java.md` 中 `## demo.channel.model` 出现 3 次 | 反解 Markdown 的方案会误判层级，宜从索引重建 |
| 6 | 构造器混入方法清单 | `constructor_declaration` 与 `method_declaration` 同样写 `METHOD`（`code_intelligence.py:303-313`），实测出现 `ChannelWithdrawController(L19)` | 树上应单独标注，否则与同名方法混淆 |
| 7 | `status()` 每次全量 `rglob` | `ast_service.py:52-59` | 大仓库下轮询会持续占用 IO |

缺口 1、2 是硬阻塞；3、4 影响体验与正确性；5、6、7 属于顺带修的质量问题。

---

## 5. 前端展示方案

原型见 `design/mockups/ast-explorer.html`（深色默认，可切浅色；数据取自 `ast-20260909-223144` 的真实结构）。视觉沿用 `frontend/src/design-tokens.css` 的变量，落地时直接映射现有 token，不新增配色体系。

### 5.1 信息架构

保留现有"AST 管理"页，把状态区降为页面顶部一条细带，主体换成三栏结构：

```
┌ 顶部：版本 / 状态 / 生成时间 / 覆盖仓库 / 重新生成 ─────────────┐
├ 左：仓库 → 目录 → 包 树  ├ 中：类型与方法 树  ├ 右：详情 + 源码片段 ┤
└ 底部固定提示：结构条目只用于定位，不等于实现证据 ────────────────┘
```

### 5.2 树形与展开折叠

- **两级懒加载**：左栏加载到"包/目录"，中栏在该包内展开类型 → 方法/字段/端点。默认展开第一层包，类型默认折叠，减少首屏节点数。
- **节点类型徽章**：`CLASS / INTERFACE / ENUM / RECORD / METHOD / FIELD / API / MYBATIS_STATEMENT`，各自固定色（复用 `--color-code / --color-business / --color-requirement` 三色系，不引入新色）。
- **事实徽章**：`HTTP_ENDPOINT / RPC_CALL / READ_TABLE / WRITE_TABLE` 等贴在所属节点右侧，点击直接跳到源码片段对应行。
- **证据状态**：节点随 `evidence_lifecycle` 显示 `ACTIVE / HISTORICAL` 点标；版本整体 `stale` 时顶部横幅提示"行号可能已失效"。
- **大仓库**：超过约 500 个节点启用虚拟滚动，只渲染可视区域。
- **可分享选中态**：把 `versionId / repositoryId / symbolId` 写进 URL query，刷新与转发都能复原。

### 5.3 语法高亮

高亮作用于"右侧源码片段"，不作用于树节点。三个理由：

1. AST 产物里只有声明行和方法名清单，没有方法体，单独高亮那几行意义不大；
2. 真正要看的是方法实现，必须回到源码按 `[line_start, line_end]` 取片段；
3. 片段只有数十行，前端做轻量 tokenizer 足够，不必引入 Prism/Shiki 这类依赖（离线部署包体敏感）。

实现：按行扫描的简易 Java tokenizer，识别注释（`//`、`/* */`）、字符串、字符、`@注解`、关键字、数字、类型名。结构上就是「行号列 + 代码列」，命中行整行浅底高亮。

### 5.4 检索

- 输入框过滤 `name / qualified_name / path`，支持 `kind:` 前缀过滤（如 `kind:METHOD applyWithdraw`）。
- 结果列表点击后自动展开树到该节点，并定位源码行。
- 服务端用 SQL `LIKE` + `kind` 索引即可；`code_symbol(file_id, qualified_name, line_start)` 已有唯一约束（`schema.py:33-37`），符号量大时再上 FTS5（`functional_knowledge` 之外，`requirement` 已有先例）。

---

## 6. 接口设计

### 6.1 前置改造：索引随版本落盘

在 `_run_generation` 中把索引库建在版本目录，而不再建在临时目录：

- `generate_ast_documents` 增加 `db_path` 参数（默认沿用临时库，保持旧评测批次可复现）；`AstService` 传 `version_root / "ast-index.db"`。
- `metadata.json` 增加 `indexDb: "ast-index.db"` 与 `symbolCount / factCount`。
- 源码快照二选一，需明确取舍：
  - **轻量**：只存索引库 + 每个文件的 `content_hash`。读源码时用当前工作区，哈希不符则返回 `sourceStale: true`，前端降级为"仅显示行号，不保证内容"。磁盘零增长，推荐先用这个。
  - **完整**：版本目录下保留 `inputs/repo-N` 源码快照。行号绝对可靠，代价是磁盘随仓库大小线性增长。
- 为兼容，`documents/*.md` 继续生成——Markdown 仍是 Agent 侧（Glob/Read）的检索形态，树形接口只服务页面，两者不互相替代。

### 6.2 新增接口

均沿用 `/api/projects/<projectId>/api/ast/...` 与 `X-Project-Id` 机制，只读、无需管理员口令（与现有 GET 一致）。

**① 树（懒加载）**

```
GET /api/ast/tree?versionId=&repositoryId=&parentId=&limit=200
```

```json
{
  "versionId": "ast-20260909-223144",
  "status": "available",
  "parentId": null,
  "children": [
    { "id": "group:demo.channel", "kind": "PACKAGE", "label": "demo.channel",
      "path": "src/main/java/demo/channel", "childCount": 1, "hasChildren": true },
    { "id": "sym:SYM-xxx", "kind": "CLASS", "label": "LoanMiddleClient",
      "qualifiedName": "demo.channel.client.LoanMiddleClient",
      "path": "src/main/java/demo/channel/client/LoanMiddleClient.java",
      "lineStart": 13, "lineEnd": 45, "childCount": 10, "hasChildren": true,
      "facts": [{ "type": "RPC_CALL", "subject": "GET", "target": "/middle/withdraw/limit", "line": 16 }],
      "lifecycle": "ACTIVE" }
  ],
  "truncated": false
}
```

`parentId` 为 `null` 时返回该仓库的顶层（目录/包）；`kind` 取值 `PACKAGE / CLASS / INTERFACE / ENUM / RECORD / METHOD / FIELD / CONSTRUCTOR / MYBATIS_STATEMENT`。构造器单独用 `CONSTRUCTOR`，避免与同名方法混淆（对应缺口 6）。

**② 符号详情**

```
GET /api/ast/symbol?versionId=&symbolId=
```

返回该符号的完整事实列表（表读写、字段读写、调用、端点）、所属文件、行区间、证据状态。调用类事实里的 `target` 是**调用表达式字符串**（如 `applicationService.create(request.customerId())`），不是符号 ID —— 后端原样返回即可，前端不得渲染成可点击跳转（`ast_docs` 与 `functional_service` 都存在同一问题）。

**③ 搜索**

```
GET /api/ast/search?versionId=&q=&kind=&repositoryId=&limit=50
```

```json
{ "items": [ { "symbolId": "SYM-xxx", "kind": "METHOD", "name": "applyWithdraw",
               "qualifiedName": "demo.channel.service.ChannelWithdrawService.applyWithdraw",
               "path": "src/main/java/demo/channel/service/ChannelWithdrawService.java",
               "lineStart": 49, "score": 1.0 } ], "total": 1 }
```

**④ 源码片段**

```
GET /api/ast/source?versionId=&repositoryId=&path=&start=&end=
```

实现可直接参照 `evaluation/service.py:1251-1279` 的 `read_source`：同样的 `start/end` 区间（默认 400 行上限）、同样的越界校验、同样的 `lines: [{n, text}]` 结构。差异在错误码与 `sourceStale` 字段。

```json
{ "repositoryId": "channel-service-repo", "path": "src/main/java/demo/channel/service/ChannelWithdrawService.java",
  "totalLines": 120, "start": 41, "end": 62,
  "sourceStale": false,
  "lines": [ { "n": 41, "text": "    public void signContract(ContractSignRequest request) {" } ] }
```

### 6.3 错误约定

| HTTP | 场景 |
| --- | --- |
| 404 | `versionId` 不存在或格式非法（沿用 `ast_service.py:133` 的正则白名单） |
| 409 | 版本 `status != available`（`generating` / `stale` / `failed`），响应带 `status` 供前端提示 |
| 400 | `path` 越出该版本源码范围；`start/end` 非法 |

---

## 7. 落地顺序

1. 生成侧持久化索引库 + `metadata` 补字段（约改 `ast_docs.generate_ast_documents` 与 `ast_service._run_generation` 两处），同步补 `tests/test_ast_evaluation.py` 的断言。
2. 后端 4 个只读接口 + 越权与状态校验测试。
3. 前端新增 `AstExplorerPage`，把 `AstPage` 的状态卡收进顶部条；先做树 + 源码片段，再做搜索。
4. 顺手修缺口 5、6（重复标题、构造器归类）与 7（`status()` 加 `sourceStamp` 缓存或 mtime 采样）。

第 1 步不做，后面三步都只能停在 Markdown 渲染，无法交付树形与定位。
