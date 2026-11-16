已核实的调用链如下。这里的“创建”是提交贷款申请；审批、放款和还款都基于已创建的 `loanId`。

**1. 创建贷款申请**

入口是 `CreateLoansAccountComponent.submit()`：组件合并各步骤表单数据，调用 `LoansService.buildLoanRequestPayload(...)` 构造请求体，然后调用 `LoansService.createLoansAccount(payload)`。成功后使用返回的 `resourceId` 跳转贷款详情。

- 组件入口：[create-loans-account.component.ts:154](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/loans/create-loans-account/create-loans-account.component.ts:154)
- Service 方法：[loans.service.ts:460](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/loans/loans.service.ts:460)
- 后端接口：`POST /loans`
- 状态含义：后端创建贷款账户，通常进入“Submitted/待审批”状态；本轮源码中没有在创建后额外发起审批命令。

**2. 发起审批**

任务页的 `LoanApprovalComponent.approveLoan()` 经过确认后进入批量审批逻辑。对每笔选中的贷款构造相对 URL：`loans/{id}?command=approve`，HTTP 方法为 `POST`，再通过批量接口提交。

- 入口和命令构造：[loan-approval.component.ts:169](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tasks-tabs/loan-approval/loan-approval.component.ts:169)、[loan-approval.component.ts:198](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tasks-tabs/loan-approval/loan-approval.component.ts:198)
- 后端业务接口：`POST /loans/{loanId}?command=approve`
- 批量提交：组件把各笔请求放入 batch 数据，实际批量传输代码及 batch endpoint 需要继续从该组件后续行核对；已确认的业务相对 URL 和命令如上。
- 待审批列表：`TasksService.getAllLoansToBeApproved()` 调用 `GET /loans?limit=1000&status=100`，见 [tasks.service.ts:60](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/tasks/tasks.service.ts:60)。

**3. 放款**

任务页的 `LoanDisbursalComponent.disburseLoan()` 确认后执行批量放款。每笔贷款构造 `loans/{id}?command=disburse`，方法为 `POST`，请求体是放款表单数据。

- 入口和命令构造：[loan-disbursal.component.ts:121](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tasks-tabs/loan-disbursal/loan-disbursal.component.ts:121)、[loan-disbursal.component.ts:150](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tasks-tabs/loan-disbursal/loan-disbursal.component.ts:150)
- 后端业务接口：`POST /loans/{loanId}?command=disburse`
- 待放款列表：`TasksService.getAllLoansToBeDisbursed()` 调用 `GET /loans?limit=1000&status=200`，见 [tasks.service.ts:68](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/tasks/tasks.service.ts:68)。
- 贷款详情页的单笔动作共用 `LoansService.getLoanActionTemplate(loanId, 'disburse')` 获取模板，接口是 `GET /loans/{loanId}/transactions/template?command=disburse&associations=delinquency`；真正提交则由通用动作方法发送到交易接口，见 [loans.service.ts:28](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/loans/loans.service.ts:28)、[loans.service.ts:238](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/loans/loans.service.ts:238)：`POST /loans/{loanId}/transactions?command={command}`。本轮已确认通用方法，但未进一步核对详情页传入的具体放款 command。

**4. 还款**

贷款详情的还款动作使用同一套交易模板和交易提交方法：

- 获取还款模板：`LoansService.getLoanActionTemplate(loanId, command)`，对应 `GET /loans/{loanId}/transactions/template?command={command}`，见 [loans.service.ts:28](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/loans/loans.service.ts:28)。
- 提交还款：`LoansService.submitLoanActionButton(loanId, data, command)`，调用 `POST /loans/{loanId}/transactions?command={command}`，见 [loans.service.ts:238](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/loans/loans.service.ts:238)。
- 还款 command 的具体字符串在本轮已读源码中未直接核实，通常需要继续追踪 `MakeRepaymentComponent` 的调用参数；因此这里确认了服务方法和交易接口，但不把具体 command 未核实地写死。

**统一处理**

1. **API 地址前缀**：业务 Service 只写相对路径，例如 `/loans`、`/loans/{id}/transactions`。运行时服务器列表来自 `environment.baseApiUrls`，由 `WebAppComponent` 初始化到 `SettingsService`，见 [web-app.component.ts:221](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/web-app.component.ts:221)。因此前缀/服务器地址不是每个贷款 Service 单独拼接的；具体把相对请求接到当前服务器的 HTTP 基础配置需要结合应用的 HTTP provider 或代理配置继续确认。

2. **租户标识**：统一由 `AuthenticationInterceptor` 注入 `Fineract-Platform-TenantId`。优先使用 `SettingsService.tenantIdentifier`，否则使用环境变量 `environment.fineractPlatformTenantId` 的默认值，见 [authentication.interceptor.ts:12](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/core/authentication/authentication.interceptor.ts:12)、[authentication.interceptor.ts:35](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/core/authentication/authentication.interceptor.ts:35)。登录页从环境变量初始化租户列表和 API server 列表，见 [login.component.ts:101](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/login/login.component.ts:101)。

3. **认证**：同一拦截器统一设置 `Authorization`。OAuth 开启时为 `Bearer <token>`，否则为 `Basic <key>`，见 [authentication.interceptor.ts:47](/private/var/folders/jx/j9rb80x14sl4qkrk9fp1t9_80000gn/T/pi-eval-7k4xvb3p/workspaces/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/complex-loan-validation/pi/CONV-580133e569e34f41a38ba387112714df/repos/mifos-web-repo/src/app/core/authentication/authentication.interceptor.ts:47)。因此创建、审批、放款、还款的业务方法都不需要分别传租户或认证参数。

需要注意：本轮证据已经确认了四类业务接口、关键 Service 方法以及租户/认证拦截器；API 基础 URL 的最终拼接 provider、审批和批量接口的实际 batch endpoint、以及还款的具体 command 字符串仍是未核实项。