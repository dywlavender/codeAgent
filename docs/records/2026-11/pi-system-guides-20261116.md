# System Guide 真实问答对照（2026-11-16）

## 设置

- 通过生产 `QueryService` 和 Pi 1.5.0 运行，实际模型均为 `deepseek/gpt-6-luna`，thinking 关闭，单次超时 360 秒。每题每组各运行一次，每次使用新会话。审批题先跑有导航组，另外两题先跑无人工导航组。
- 使用融商贷验证工程。HTTP/DTO 和定时补偿题仅授权 `loan-middle-repo`，审批分发题仅授权 `fineract-repo`。同一题两组均使用 `mode=none`，从 Trace 核对了源码、需求版本和工具授权一致。
- 两组都保留工程配置中的应用位置。有导航组额外加入下表文档，内容为定位规则。题目检查项和关键文件清单只用于评测。

| 草稿路径（相对 `knowledge/system-guides/`） | 导航内容 |
| --- | --- |
| `middle-system/SYSTEM.md` | 核对类级和方法级 Mapping、嵌套 request record、响应构造、Service 与适配器。批处理依次找调度注解、配置、查询筛选和服务分支。 |
| `fineract-platform/SYSTEM.md` | 用 JAX-RS 注解和 `ApiResource` 命名找入口，按符号和注册信息追到其他模块。 |
| `fineract-platform/applications/fineract-backend.md` | 沿命令构造、提交、执行查到 `@CommandType` 登记与实际 Handler，核对实体、动作和委派。 |

## 结果

六次均返回答案。各题四项按答案内容逐条核对源码，表中为「通过 / 部分通过 / 未通过」。引用确认数来自本轮 `read` 交付的文件和行区间。关键文件计数表示模型读到了文件，答案里的行号是否支持结论另行核对。耗时包括本次运行启动，Token 为该次运行累计用量。

| 题目 | 人工导航 | 内容核查 | 关键文件 Read | 引用确认／总数 | 模型／工具调用 | Token | 耗时（秒） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| HTTP/DTO | 无 | 4 / 0 / 0 | 2/2 | 15/15 | 10/9 | 146,746 | 112.81 |
| HTTP/DTO | 有 | 2 / 2 / 0 | 0/2 | 0/4 | 4/3 | 36,585 | 72.00 |
| 审批分发 | 无 | 1 / 1 / 2 | 1/4 | 5/5 | 11/10 | 201,541 | 98.32 |
| 审批分发 | 有 | 4 / 0 / 0 | 4/4 | 7/7 | 22/21 | 369,039 | 139.95 |
| 定时补偿 | 无 | 4 / 0 / 0 | 2/2 | 7/8 | 9/8 | 139,064 | 83.62 |
| 定时补偿 | 有 | 4 / 0 / 0 | 2/2 | 13/13 | 11/10 | 153,750 | 155.95 |

### HTTP/DTO

题目要求说明 `POST /middle/api/v1/drawdowns/quote` 的 REST 入口、请求 DTO、服务校验及试算、响应数据来源。

- 两组都正确说明了路由、请求传参和 `QuoteRequest` 的六个字段。无人工导航组在第 4、5 轮首次读到 `DrawdownController.java` 和 `DrawdownService.java`，追到了校验条件、Fineract 试算、报价落库和五个响应字段。有导航组搜索定位后即作答，本轮无 `read`。它找到了校验调用与试算，漏掉校验方法体、报价落库细节及响应字段。
- Controller 的路由、DTO 和转发分别见第 19–21、36–38、60–64 行。有导航组把 DTO 定义指到了第 31 行。无人工导航组也把部分结论指到了构造方法和产品列表里的行，即使这些引用位于已读区间。
- Service 的报价流程见第 58–84 行，校验规则见第 272–286 行。验证工程快照中第 61 行传入 `ProductConfig`，而第 272 行的校验方法接受 `ProductConfigVersion`，两类型无继承关系。此题复核源码写出的行为意图。

### 审批分发

题目要求说明 Fineract 贷款审批命令如何经过 REST、命令包装、注册查找和具体 Handler。

- 无人工导航组核实了 `LoansApiResource` 分支与提交，提到传入贷款 ID，缺少 Builder 写入的实体、动作、实际 Bean 查找以及 Handler 委派。有导航组在第 10、11、14、15 轮读到 Provider、Handler、Builder 和 REST 分支，随后核对同步执行层，补全了四项。它的 REST 引用指向方法前的第 1311 行，实际交付的片段覆盖了分支和提交。
- 源码依据包括 `LoansApiResource.java:608–630、1313–1324、1351–1356`，`CommandWrapperBuilder.java:1110–1117` 的 `LOAN|APPROVE` 与贷款 ID，`SynchronousCommandProcessingService.java:315–319` 的 `getHandler`，`CommandHandlerProvider.java:61–77、91–100` 的注解扫描及 Bean 查找，以及 `LoanApplicationApprovalCommandHandler.java:30–41` 的 `approveApplication` 委派。

### 定时补偿

题目要求说明提款付款主动查单的触发任务、默认调度间隔、超时和状态筛选，以及三种结果的处理。

- 两组均给出了固定延迟 15 秒、默认超时 60 秒、只筛选 `PROCESSING` 且 `updatedAt` 非空并早于截止时间。两组均核实成功与失败进入 `onPaymentResult`，`RESULT_UNCONFIRMED` 只更新付款状态。有导航组还明确指出该状态不会再被这条只筛选 `PROCESSING` 的任务选中。
- `CompensationTask.java:42–46、57–69` 和 `DrawdownService.java:189–209、244–253` 支持这些结论。有导航组读齐两处关键文件的轮次为第 5 轮，无人工导航组为第 6 轮。有导航组两次 `codemode` 脚本语法错误后改用文件工具，最终耗时更长。

## 验收判断

审批分发题观察到覆盖提升，额外消耗 11 次工具调用和 167,498 Token。HTTP/DTO 题用量下降伴随提前作答和内容缺失。定时补偿题内容检查持平。有导航组在三个案例中均按本轮范围挂载了正确文档。问答收益的结论限于单次配对、单模型及这三类问题。

完整题目、文档正文、答案、原始 Trace 和逐项复核保存在本机 `.data/evaluations/pi-system-guides-20261116/`。使用 `scripts/evaluation/evaluate_pi.py review` 校验答案原句与复核结构并生成 `checks.md`。
