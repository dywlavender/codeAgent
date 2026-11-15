# Pi Runtime 统一问答主链实现记录（2026-11-15）

## 范围

本轮实现 Pi 作为在线问答的唯一调查循环，保留 CodeAgent 的工程、资料授权、会话、Trace、取消与评测边界。实现时 Pi 通过 `BUSINESS_CODE_AGENT_RUNTIME=pi` 显式启用；2026-11-15 用户确认后将默认运行切换为 Pi，显式 `langchain` 仅保留历史同题对照与评测。真实题库对照尚未执行。

## 实现

- 新增 `business_code_agent/query/agent/pi/`：
  - `process.py`：严格 JSONL 子进程、stdout/stderr 分流、进程退出与诊断。
  - `events.py`：Pi 事件转换为现有 `model`／`tool`／`status`／`compaction`／`text` Trace 事件；跟踪模型调用中状态。
  - `sessions.py`：Pi 会话文件限定在工程数据目录，拒绝目录外路径。
  - `runtime.py`：`PiAgentRuntime`，按问题启动 `pi --mode rpc`，等待 `agent_settled`，处理 `abort`、模型错误、截断和会话恢复。
  - `extension/codeagent_search.js`：只读 Pi 扩展。覆盖 `grep`／`find` 以跟随授权仓库软链接，并在执行前拦截越界路径、符号链接逃逸和写工具。
- `profile.py` 新增 `pi-v1`；`capabilities.py` 新增 Pi 原生工具状态与阅读指导；`workspace.py` 新增按会话稳定、按 scope 刷新的 Pi 工作区；`service.py` 新增统一 `_query_pi()`，支持 `combined`／`none`／`backbone`／`ast`。
- Pi 启动参数固定 `--no-extensions`（显式加载 codemode 与只读扩展）、`--no-context-files`、`--no-mcp`、`--no-skills`、只读工具 `read,grep,find,ls,codemode`，不开放 `bash`／`edit`／`write`。
- Pi 事件写入既有 `RunTraceArchive`；模型调用按 `attempt` upsert，取消时未完成的模型调用标记 `cancelled`。Pi 失败直接结束当前 Run，不自动回退 LangChain。

## 验证

### 工程回归

- 新增 `tests/test_pi_runtime.py` 14 项、`tests/test_pi_query_service.py` 7 项，覆盖协议事件、会话复用、取消、超时、模型错误、长度截断、配置失败、越界路径、scope 刷新和四种资料模式。
- 全量后端：`513 passed, 91 subtests passed`。
- 测试使用假 Pi RPC 和临时工作区，不调用真实模型。
- 默认切换后补充验证：`resolve_profile({})` 返回 `pi-v1`；真实 Pi RPC `get_commands` 在最后一次扩展修改后仍返回 `codeagent-readonly-search`，证明工具覆盖加载成功。

### 真实 Pi 验证

服务以 `BUSINESS_CODE_AGENT_RUNTIME=pi` 启动，使用本机 `pi 1.5.0` 与 `deepseek-v4-pro`，经现有 API/SSE 执行：

| 场景 | 结果 |
| --- | --- |
| `loan-middle` 提款入口 | `RUN-f95f1bb1a57347998b6f748852f15d92`，completed，7 次模型调用、16 次工具调用，Pi 会话与 Trace 均保存 |
| 同会话追问 | `RUN-3539599955584c1d810d228da9a0ec31`，completed，复用同一 Pi 会话，1 次模型调用、0 次工具调用 |
| 停止 | `RUN-683eb845eb574a01ab5756f3a1875aa9`，cancelled，回答“已停止。”，Trace 状态 cancelled |

首个真实 Run 的工具轨迹包含 `ls`、`grep`、`find`、`read`，检索范围覆盖 `repos/loan-middle-repo` 和 `knowledge/business-context`；模型给出 `MiddleWithdrawController.applyWithdraw` 的文件与行号，并明确说明“审核通过后”入口在当前仓库未登记，未臆造实现。该单次结果只证明链路和证据表达可用，不证明答案质量优于旧链。

## 已知边界

- 工作区仍通过软链接暴露授权源码；只读扩展拦截工具参数和符号链接逃逸，但不是操作系统级沙箱，`grep --follow` 仍可能遍历授权仓库内部符号链接。
- Pi 与 CodeAgent 的参数、事件和工具名称由适配层维护；Pi 升级后需重新执行协议与扩展加载检查。
- 默认运行已于 2026-11-15 切换为 Pi；`core-v1`／`core-business-v1` 仍可显式选择 Direct。显式 `BUSINESS_CODE_AGENT_RUNTIME=langchain` 暂留历史同题对照与评测，不参与默认链路，Pi 失败不回退；8／16 题真实对照与现有模式验收仍未完成，硬封存/删除旧链另行确认。
