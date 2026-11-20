"""只读业务知识 MCP 服务。

面向通用 Agent（Pi、Codex、Claude Code、OpenCode）的只读知识接口，不做问答、
不生成索引、不写文件。实现见 :mod:`business_code_agent.mcp.server`，启动入口:

    business-code-agent serve-mcp --project-config project.config.json
    business-code-agent serve-mcp --project-registry .data/platform --project-id <工程 ID>

四个工具:

- ``get_navigation``：当前工程的系统、应用、仓库和真实文件系统位置。
- ``get_system_guide``：系统或应用导航 Markdown 原文。
- ``search_business_knowledge``：在业务补充知识中检索，返回文档路径与命中摘要。
- ``read_business_knowledge``：读取一篇业务补充知识 Markdown 全文。

MCP SDK 属于可选依赖（``pip install "business-code-agent[mcp]"``），因此本包
在导入时不依赖 SDK；缺少 SDK 时由 :func:`business_code_agent.mcp.server.serve`
报出可执行的安装提示，而不是抛出裸 ``ImportError``。
"""
