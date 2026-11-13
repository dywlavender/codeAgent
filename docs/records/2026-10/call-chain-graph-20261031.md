# 调用链图实现与验证（2026-10-31）

用户确认将“接口 → 服务方法 → HTTP 客户端 → 另一系统接口”展示成图。本次在现有知识图谱页增加默认的“调用链”标签，原来的图保留在“业务关系”。

## 实现范围

- 从当前工程显式准备的契约索引读取节点和边；浏览页面、查询接口和打开源码面板均不生成 AST，也不调用模型。
- 按接口选择入口，横向展示方法委托与跨系统 HTTP 连接，支持搜索、缩放、平移，以及从远端接口继续查看。
- 节点和连线可打开源码面板，HTTP 连接同时展示客户端调用、地址配置与目标接口依据。面板展示索引时的原文，并限制总行数。
- 工程与索引版本共同约束引用。当前文件变化时标为待更新；无法确定的目标保留缺口，不补造接口。

后端入口是 `business_code_agent/codeintel/contracts/graph.py`，API 为 `/api/projects/<projectId>/call-chains` 及其链路、证据子资源。前端位于 `frontend/src/features/graph/CallChainView.jsx`，布局独立放在 `call-chain-layout.js`。

当前投影覆盖已有解析器记录的返回依赖调用，主图筛出通向 HTTP 客户端或未解析目标的路径；没有 HTTP 连接时展示已解析的方法委托。它不表示所有副作用调用、完整控制流或运行时轨迹，也不能作为问答收益已验证的依据。

## 验证

后端用例覆盖主链裁剪、HTTP 源码依据、索引过期、未解析目标、缺失索引时不生成结构、版本约束，以及同名仓库／接口的跨工程隔离。相关测试命令：

```sh
.venv/bin/python -m unittest tests.test_call_chain_graph tests.test_contract_index tests.test_project_context tests.test_query_api
cd frontend
npm test
npm run build
```

后端相关测试 39 项通过，前端测试 13 项通过，生产构建通过。构建仍有现有的大资源包提示。

实际浏览器验证使用 `scripts/evaluation/verify_call_chain_ui.js`，在 complex-loan 工程中确认：

1. 搜索并选择 `GET /api/drawdowns/{channelRefNo}`，展示 `ChannelController.drawdownDetail → ChannelService.drawdownDetail → MiddleClient.drawdownDetail → DrawdownController.detail` 四个节点。
2. 点击 HTTP 连线，能看到客户端请求、`http://localhost:8092` 配置与目标 `@GetMapping` 的索引原文。
3. 放大、适应画布、拖动平移均改变或恢复画布视口。
4. 从远端接口继续查看，展示其到 `DrawdownService.detail` 的方法关系。
5. 切换回业务关系图可正常显示，再切回调用链可重新选择入口。
6. 验证过程未出现浏览器运行错误。

本机预览使用 `BUSINESS_CODE_MODEL_ENABLED=false` 启动，未执行真实模型验证。截图保存在本机 `output/playwright/call-chain-final.png` 与 `output/playwright/call-chain-evidence-final.png`，为浏览器实际页面截图。

预览地址：`http://127.0.0.1:18082/?projectId=complex-loan-validation#/graph`。这是本次本机验证服务，并非部署地址。
