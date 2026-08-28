# 项目长期笔记：Business Code Agent（Code Atlas）

## 定位
面向 Java / MyBatis 老项目的「证据优先」Agent 问答工作台。人工只写简短功能文档，系统负责定位入口与关键表、建检索索引、生成带证据的流程/规则摘要，最终由 Query Agent 回到当前代码回答。

## 架构要点
- 三层知识分离：人工功能定义（`functional_knowledge`）→ 检索索引（`functional_entry_anchor` / `functional_key_table` / `functional_retrieval_link`）→ Agent 分析（`functional_analysis`）。代码重索引不改写人工文档。
- 两个 Agent：Knowledge Update Agent（`knowledge_update/functional_service.py`）做入口定位与有界分析；Query Agent（`query_agent/`）做检索计划与回答，只读不写知识。
- 后端零框架：`http.server` 手写路由（`query_agent/api.py`），SQLite（`schema.py`，含一次性清理旧表的迁移）。
- 模型：LangChain 1.2 `init_chat_model` + `create_agent`，Pydantic 结构化输出；配置全在根目录 `.env`，未配置时降级 FALLBACK。
- 前端 React + Vite + antd，黑白灰 Codex 风格，四页：问答 / 浏览知识库 / 知识图谱 / 功能知识维护（需管理员口令）。

## 目录速查
- `business_code_agent/cli.py`：全部子命令入口（serve-query / sync-project / query / requirement-* / query-validate）
- `code_intelligence.py`：Java + MyBatis 索引（tree-sitter 优先，正则兜底）
- `project_sync.py`：按 project.config.json 同步 Git 仓库并增量索引
- `knowledge_graph.py`：只读图谱投影（功能→工程→入口代码 / 功能→关键表 / 功能→标签）
- `requirement/`：早期需求文档管线，仍保留但非主链路
- `examples/validation-project`：内置验证仓库（申请服务 / 提款服务 / 结算批任务）

## 运行约定
- macOS 用 `./start-mac.sh`（建 .venv、装依赖、同步索引、构建前端、启动 8083）；Windows 用 `start-windows.bat`
- 当前 `project.config.json` 指向 `examples/validation-project`，库在 `.data/validation-project.db`，端口 8083
- 测试：`python3 -m unittest discover -s tests -v`；前端 `cd frontend && npm run build`

## 当前状态（2026-08-28）
- 库内：1 个功能（application-withdrawal）、3 个入口全部 RESOLVED、15 条检索链接
- 功能分析状态 FAILED：阿里云百炼 qwen3.7-plus 返回 403 insufficient_quota（免费额度耗尽），需换 Key 或改模型

## 架构评审结论（2026-08-28）：方向对，检索索引层偏薄

站得住的设计：三层分离、证据引用强制校验（`_validate_grounded_items`，无证据条目整批丢弃）、
代码变更传播 STALE（`_mark_file_relations_stale`，只失效自动层不改人工文档）、
有界上下文（CALL 提示 LIMIT 24、表链接 LIMIT 40）、模型降级不编造。

已确认的缺陷（按优先级）：
1. **调用链没接进索引（知识生成的真实断点）** —— `functional_service._build_index` 的
   DIRECT_CALL_HINT 既限制同文件（`cs.file_id=owner.file_id`），又把 `cf.target` 原样存成
   `CODE_HINT`，target 是 `'applicationService.create(request.customerId())'` 这类**表达式字符串**，
   不是符号 ID，因此 6 条提示全部不可导航。
   关键前提：`code_fact` 里 14 条 CALL 已经把 Controller→Service→Mapper 三跳完整解析出来了
   （subject=被调方法名，target=调用表达式）。**数据已有，是索引没用起来**，改造不是从零建调用图。
   实测 14 条的目标方法名在本仓库内均唯一，按「方法名唯一匹配」解析即可，不唯一则记 AMBIGUOUS 不猜。
   反例对照：表那一路是通的——5 条 READ/WRITE_TABLE 全部落到真实 Mapper 符号。
2. **STALE 传播不到 retrieval_link，查询层也不过滤 HISTORICAL** —— `tools.evidence()` 与
   `business_tools.get_business_knowledge` 直接 `SELECT * FROM evidence WHERE id IN (...)`，
   不带 lifecycle 过滤；重索引后 refresh 前可能返回已失效代码行。
3. **关键表链接无工程隔离** —— `WHERE lower(cf.subject)=lower(?)` 全库匹配，多功能共用同一张表时互相污染。
4. **功能检索 O(n) + N+1** —— `BusinessTools.search_business_knowledge` 全表扫描、每功能 5 次查询、
   无 FTS5（requirement 有，functional_knowledge 没有）；且 `status` 硬编码 CONFIRMED，
   无法区分 analysis 为 FAILED/STALE 的情况。
5. **测试薄弱** —— 33 个测试 / 731 行对 7000 行代码，`query_agent/retriever.py` 零覆盖，
   而检索质量恰是最脆的一环。

小瑕疵：`cli.py` 重复 `import os`；`frontend/src/App.jsx` 末尾 `pendingCount()` 为未使用的死函数。
