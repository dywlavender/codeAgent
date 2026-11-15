# Direct 调查 Trace 诊断（2026-11-15）

## 方法与数据

使用离线脚本 `scripts/evaluation/trace_diagnosis.py` 读取 Commit 2 A/B 已保存的 `RunTraceArchive`，不调用模型、不修改工程工作区。诊断对象为 `native-frontend`、`native-lifecycle`、`v2-strategy-code` 的 A（旧 Prompt）与 B（Commit 2 最终 Prompt）成功运行；每道题每组只有一次运行，温度为 0.6。

关键文件由人工按题库 `checks` 指定，用于定位证据覆盖，不作为 Agent 可见的标准答案。候选文件数来自 Grep/Glob 的原始返回，包含目录路径和宽泛 Glob 结果，只用于观察搜索范围，不等同于“应读文件”。

复现：

```bash
python scripts/evaluation/trace_diagnosis.py <trace.json> \
  --keys FileA.java,FileB.java --output /tmp/diagnosis.md
```

## native-frontend

| 指标 | A（旧 Prompt） | B（最终 Prompt） |
| --- | --- | --- |
| 状态 | completed，19 次调用，138.0 秒 | incomplete（budget），20 次调用，214.4 秒 |
| 关键文件 | 7/8；`environment.ts` 未 Read | 8/8；全部在轮次 8 前读取 |
| 仓库 | 仅 mifos-web-repo | mifos-web-repo + fineract-repo（轮次 14 起） |
| 搜索/读取 | 222 个候选，11 个实际 Read | 239 个候选，15 个实际 Read |
| 收尾前已读 | 11 个文件 | 15 个文件 |
| 引用确认 | 31/43 | 23/30 |

关键时序：

- A：轮次 1–15 逐步读取前端服务、审批、放款、还款和拦截器，轮次 16–18 补充少量读取，轮次 19 直接作答。
- B：轮次 3 已读 `loans.service.ts` 全文（1–758），轮次 8 已覆盖全部关键文件；轮次 14 开始转向 `fineract-repo`，轮次 16–19 的 Glob/Grep 没有产生新的有效答案证据，轮次 20 进入预算收尾，答案只有 2776 字。

结论：B 的退化不是“关键源码没找到”，而是**关键证据已充分后仍继续扩展，直到预算收尾**。检查点在约一半正常预算（20 次调用中的轮次 10）本可看到全部前端关键文件已读；如果模型能确认剩余缺口不影响问题，就应更早回答。

## native-lifecycle

| 指标 | A（旧 Prompt） | B（最终 Prompt） |
| --- | --- | --- |
| 状态 | incomplete（budget），26 次调用，220.4 秒 | incomplete（budget），39 次调用，213.9 秒 |
| 关键文件 | 6/6；`CommandHandlerProvider` 在轮次 25 读取 | 4/6；缺 `CommandWrapperBuilder`、`CommandHandlerProvider` |
| 仓库 | fineract 为主；轮次 19 起少量 web，轮次 20 各 1 次 channel/middle | fineract；轮次 28–29 起 channel/middle/web，共约 71 次工具调用 |
| 收尾前已读 | 17 个文件 | 15 个文件 |
| 引用确认 | 17/20 | 15/17 |

结论：B 在轮次 9 已读审批 Handler，但没有继续核实 `CommandWrapperBuilder` 与命令路由 Handler，反而在轮次 28 后扩展到渠道、中台和前端；这属于**关键环节遗漏 + 后续范围扩展**，不是工具定位失败。检查点若要求模型按原问题的“REST 入口 → command → Handler”核对 aspects，应能把命令路由列为缺口；但它依赖模型自己识别该缺口，程序不能凭引用核验推断语义完整性。

## v2-strategy-code

| 指标 | A（旧 Prompt） | B（最终 Prompt） |
| --- | --- | --- |
| 状态 | incomplete（budget），28 次调用，219.8 秒 | completed，20 次调用，148.0 秒 |
| 关键文件 | 5/5；`LoanProduct.java` 轮次 15 | 5/5；全部在轮次 9 前读取 |
| 仓库 | loan-middle + fineract | loan-middle + fineract |
| 引用确认 | 12/17 | 18/19 |

结论：B 在轮次 9 已定位全部关键文件，轮次 20 完成；A 在关键文件齐备后仍有多个轮次的宽范围 Grep。该题说明当前策略在“已知符号、跨工程扩展”场景有效，正面结果不应被前端/生命周期的退化掩盖。

## 共同结论

1. 三个案例的失败点都不是“缺少搜索工具”：关键文件要么已读（frontend 8/8），要么只差命令路由层（lifecycle 4/6）。真正的问题是**何时停止扩展、如何确认问题要求的环节已覆盖**。
2. `native-frontend` 在约一半预算时已具备全部关键证据，却继续调查到预算收尾；`native-lifecycle` 在一半预算时仍缺关键命令路由证据，却继续扩展无关系统。两者分别对应“结束过晚”和“缺口识别错误”，正是阶段性检查点要区分的两种状态。
3. `v2-strategy-code` 在一半预算前已读齐关键文件，检查点不应拖慢它；如果检查点发现没有关键缺口，应允许直接作答。
4. 检查点不能把“引用都有效”当成“业务答案完整”，也不能用固定阈值强制结束；它只整理已确认结论、部分结论和关键缺口，是否结束仍由模型决定，预算收尾继续兜底。

## 限制

- 单题单次运行、温度 0.6，且 A/B 运行时刻不同；结论用于解释失败 Trace，不是统计效果证明。
- 关键文件清单由人工按题库定义，Agent 本轮不可见；检查点只能依赖模型自己提出 aspects/gaps。
- 候选/未读统计把目录和宽泛 Glob 结果计入候选，不能直接当作“无效调查”数量。
- 引用核验只验证路径、行号和 Read 交付区间，不证明业务语义正确。
