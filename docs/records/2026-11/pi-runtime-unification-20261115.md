# Pi Runtime 统一问答主链实现记录（2026-11-15）

## 范围

本轮实现 Pi 作为在线问答的唯一调查循环，保留 CodeAgent 的工程、资料授权、会话、Trace、取消与评测边界。实现时 Pi 通过 `BUSINESS_CODE_AGENT_RUNTIME=pi` 显式启用；2026-11-15 用户确认后将在线运行切换为 Pi，并封存环境变量回到旧 Runtime 的入口；旧 Runtime 仅保留显式评测注入。真实题库对照尚未执行。

## 实现

- 新增 `business_code_agent/query/agent/pi/`：
  - `process.py`：严格 JSONL 子进程、stdout/stderr 分流、进程退出与诊断。
  - `events.py`：Pi 事件转换为现有 `model`／`tool`／`status`／`compaction`／`text` Trace 事件；跟踪模型调用中状态。
  - `sessions.py`：Pi 会话文件限定在工程数据目录，拒绝目录外路径。
  - `runtime.py`：`PiAgentRuntime`，按问题启动 `pi --mode rpc`，等待 `agent_settled`，处理 `abort`、模型错误、截断和会话恢复。
  - `extension/codeagent_search.js`：只读 Pi 扩展。覆盖 `grep`／`find`，从 `CODEAGENT_PI_MOUNTS` 给出的授权真实根目录分别检索并映射回工作区路径，不使用全局 `--follow`；执行前拦截越界路径、入口符号链接逃逸和写工具。
- `profile.py` 新增 `pi-v1`；`capabilities.py` 新增 Pi 原生工具状态与阅读指导；`workspace.py` 新增按会话稳定、按 scope 刷新的 Pi 工作区；`service.py` 新增统一 `_query_pi()`，支持 `combined`／`none`／`backbone`／`ast`。
- Pi 启动参数固定 `--no-extensions`（显式加载 codemode 与只读扩展）、`--no-context-files`、`--no-mcp`、`--no-skills`、只读工具 `read,grep,find,ls,codemode`，不开放 `bash`／`edit`／`write`。
- Pi 事件写入既有 `RunTraceArchive`；模型调用按 `attempt` upsert，取消时未完成的模型调用标记 `cancelled`。Pi 失败直接结束当前 Run，不自动回退 LangChain。

## 验证

### 工程回归

- 新增 `tests/test_pi_runtime.py` 16 项、`tests/test_pi_query_service.py` 10 项、`tests/test_pi_search_mounts.py` 7 项，覆盖协议事件、会话复用、取消、超时、模型错误、长度截断、配置失败、越界路径、scope 刷新、四种资料模式、空范围拒绝、引用核验、资料版本重建和搜索根目录映射。
- 全量后端：`526 passed, 96 subtests passed`。
- 测试使用假 Pi RPC 和临时工作区，不调用真实模型。
- 默认切换后补充验证：`resolve_profile({})` 返回 `pi-v1`；真实 Pi RPC `get_commands` 在最后一次扩展修改后仍返回 `codeagent-readonly-search`，证明双文件扩展加载成功。

### 真实 Pi 验证

服务以 `BUSINESS_CODE_AGENT_RUNTIME=pi` 启动，使用本机 `pi 1.5.0` 与 `deepseek-v4-pro`，经现有 API/SSE 执行：

| 场景 | 结果 |
| --- | --- |
| `loan-middle` 提款入口 | `RUN-f95f1bb1a57347998b6f748852f15d92`，completed，7 次模型调用、16 次工具调用，Pi 会话与 Trace 均保存 |
| 同会话追问 | `RUN-3539599955584c1d810d228da9a0ec31`，completed，复用同一 Pi 会话，1 次模型调用、0 次工具调用 |
| 停止 | `RUN-683eb845eb574a01ab5756f3a1875aa9`，cancelled，回答“已停止。”，Trace 状态 cancelled |

首个真实 Run 的工具轨迹包含 `ls`、`grep`、`find`、`read`，检索范围覆盖 `repos/loan-middle-repo` 和 `knowledge/business-context`；模型给出 `MiddleWithdrawController.applyWithdraw` 的文件与行号，并明确说明“审核通过后”入口在当前仓库未登记，未臆造实现。该单次结果只证明链路和证据表达可用，不证明答案质量优于旧链。

验收修复后的真实冒烟：`RUN-bd5a78cf89db4950a534ed0b3f688bed`（默认 Pi、combined）completed，2 次模型调用、1 次 `grep` 调用。grep 返回路径同时包含 `repos/channel-service-repo/...`、`repos/loan-middle-repo/...` 和 `knowledge/generated-code-map/...`，证明授权真实根检索与工作区路径映射生效。该答案只引用了 grep 输出，没有 `read` 原文，因此引用核验记录 `readFiles: []`、全部 `unconfirmed`、`semanticProof=false`；这是核验器的预期边界，不把 grep 命中当作原文交付。

## 验收后修复（2026-11-15）

- **空范围授权（P0）**：`repository_ids=None` 才表示全部仓库；显式空数组在 `prepare_pi()`／`prepare_core()` 中直接失败，服务记录失败 Run，不回退为全部仓库。新增 Pi 与 Direct 工作区、服务回归。
- **在线旧链封存（P1）**：`resolve_online_profile()` 固定在线入口为 `pi-v1`；`_default_runtime()` 不再读取旧 Profile／Runtime 选择，只保留告警。旧 Runtime 仅允许评测代码显式构造并注入。新增 Pi/Core 默认入口回归。
- **引用核验（P1）**：抽出 `query/agent/citations.py`，Direct 保持原行为；Pi 按本轮实际交付的 `read` 路径与行区间核验最终答案引用，结果写入 usage／Trace，`semanticProof=false`。新增虚假路径、未读行号与部分交付回归。
- **资料版本（P1）**：`MaterialManifest.from_workspace()` 为业务文档／需求／代码地图计算内容指纹，为源码仓库计算文件元数据修订号；版本进入 Pi 会话配置，资料变化时重建会话。新增业务文档和源码变化回归。
- **搜索边界（P1）**：扩展不再使用全局 `--follow`，改由 `CODEAGENT_PI_MOUNTS` 指定授权真实根目录逐个检索并映射回工作区路径；仓库内部软链接不会被遍历。纯映射逻辑有独立 Node 回归，真实 Pi 扩展加载检查通过。
- **仍未验证**：Codemode 真实运行、三题同题问答对照、Trace 模型请求 `messages`／`tools` 的完整记录（P2）。本轮修复只证明授权、引用、会话和搜索工程行为，不证明答案质量。

## 在线模型选择（2026-11-15）

- 新增 `query/agent/pi/models.py`：通过 Pi RPC `get_available_models` 读取模型目录，`get_state` 记录 Pi 默认模型；只保留 `id`、名称、提供方、上下文/输出上限、是否推理和输入类型，`baseUrl`、密钥等提供方细节不返回浏览器。
- `GET /api/pi/models` 返回模型列表、Pi 默认与配置模型；`POST /api/query` 的 `model` 只支持 Pi 运行时，格式校验后由 `PiAgentRuntime.with_model()` 绑定本轮进程，不改写共享配置。模型进入会话配置，切换模型重建 Pi 会话。
- 前端问答输入区从该接口加载模型列表，按工程保存选择；留空表示 Pi 默认。默认值不显式下发，避免恢复历史会话时无谓重建会话。
- 真实验证：`GET /api/pi/models` 返回 14 个模型，包含 `zcode/glm-5.3-flash`，无提供方地址/密钥字段。通过网页选择 `glm-5.3-flash` 后提交问题，`RUN-347773fa313e464e836820cf20b001ba` completed，`requestedModel`／`execution.model`／`usage.actualModel` 均为 `zcode/glm-5.3-flash`，回答为“模型验证成功”。该结果只证明模型选择链路，不证明该模型答案质量优于默认模型。

## 已知边界

- 工作区仍通过软链接暴露授权源码；只读扩展拦截工具参数和入口符号链接逃逸，但不是操作系统级沙箱。搜索不再跟随仓库内部软链接，`codemode` 等其它工具仍受 `tool_call` 授权检查，不能替代进程级隔离。
- Pi 与 CodeAgent 的参数、事件和工具名称由适配层维护；Pi 升级后需重新执行协议与扩展加载检查。
- 正常在线 API 已封存旧 Runtime；`core-v1`／`core-business-v1` 和旧 LangChain 仅能由评测代码显式注入。Pi 失败不回退；8／16 题真实对照与现有模式验收仍未完成。Trace 中模型请求的 `messages`／`tools` 仍为空数组，作为 P2 待补。
