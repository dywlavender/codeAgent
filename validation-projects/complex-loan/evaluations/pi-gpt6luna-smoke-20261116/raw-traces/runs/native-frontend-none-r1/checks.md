# 逐项检查复核：native-frontend / none / r1

- Run：`RUN-15f5f10f342446a983b182877e312798`；模型：deepseek/gpt-6-luna；状态：completed
- 结论：通过 1、部分通过 4、未通过 1

## 1. 创建：create-loans-account.component.ts 的 submit() → loansService.createLoansAccount → POST /loans

**结论：通过**

答案原句：
> 入口是 `CreateLoansAccountComponent.submit()`：组件合并各步骤表单数据，调用 `LoansService.buildLoanRequestPayload(...)` 构造请求体，然后调用 `LoansService.createLoansAccount(payload)`。

源码/资料依据：
- repos/mifos-web-repo/src/app/loans/create-loans-account/create-loans-account.component.ts:154
- repos/mifos-web-repo/src/app/loans/loans.service.ts:460

说明：创建组件入口、Service 方法和 POST /loans 均有答案原句及读取确认引用。

## 2. 审批/放款：loanActionButtons(loanId, 'approve'|'disburse', data) → POST /loans/{loanId}?command=...

**结论：部分通过**

答案原句：
> 任务页的 `LoanApprovalComponent.approveLoan()` 经过确认后进入批量审批逻辑。对每笔选中的贷款构造相对 URL：`loans/{id}?command=approve`，HTTP 方法为 `POST`，再通过批量接口提交。
> 任务页的 `LoanDisbursalComponent.disburseLoan()` 确认后执行批量放款。每笔贷款构造 `loans/{id}?command=disburse`，方法为 `POST`，请求体是放款表单数据。

源码/资料依据：
- repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tasks-tabs/loan-approval/loan-approval.component.ts:169
- repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tasks-tabs/loan-approval/loan-approval.component.ts:198
- repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tasks-tabs/loan-disbursal/loan-disbursal.component.ts:121
- repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tasks-tabs/loan-disbursal/loan-disbursal.component.ts:150

说明：覆盖审批/放款组件入口和 command URL，但未使用检查项要求的 loanActionButtons 统一方法，批量 endpoint 也未核实。

## 3. 还款：make-repayment.component.ts 解析模板交易类型 → submitLoanActionButton(loanId, data, command) → POST /loans/{loanId}/transactions?command=repayment

**结论：部分通过**

答案原句：
> 提交还款：`LoansService.submitLoanActionButton(loanId, data, command)`，调用 `POST /loans/{loanId}/transactions?command={command}`

源码/资料依据：
- repos/mifos-web-repo/src/app/loans/loans.service.ts:28
- repos/mifos-web-repo/src/app/loans/loans.service.ts:238

说明：覆盖 submitLoanActionButton 和交易接口，但未定位 make-repayment.component.ts，也未核实 repayment command 字符串。

## 4. ApiPrefixInterceptor 统一拼接 /fineract-provider/api/v1 前缀；AuthenticationInterceptor 注入 Fineract-Platform-TenantId 头与 Basic/Bearer 认证

**结论：部分通过**

答案原句：
> 统一由 `AuthenticationInterceptor` 注入 `Fineract-Platform-TenantId`。优先使用 `SettingsService.tenantIdentifier`，否则使用环境变量 `environment.fineractPlatformTenantId` 的默认值
> OAuth 开启时为 `Bearer <token>`，否则为 `Basic <key>`

源码/资料依据：
- repos/mifos-web-repo/src/app/core/authentication/authentication.interceptor.ts:12
- repos/mifos-web-repo/src/app/core/authentication/authentication.interceptor.ts:35
- repos/mifos-web-repo/src/app/core/authentication/authentication.interceptor.ts:47

说明：覆盖 AuthenticationInterceptor 的租户与 Basic/Bearer 认证；未定位 ApiPrefixInterceptor，API 前缀链未完整核实。

## 5. 各业务域一个 service 文件（如 loans.service.ts），地址配置在 src/environments/environment.ts，可被运行时 window.env 覆盖

**结论：部分通过**

答案原句：
> 业务 Service 只写相对路径，例如 `/loans`、`/loans/{id}/transactions`。运行时服务器列表来自 `environment.baseApiUrls`，由 `WebAppComponent` 初始化到 `SettingsService`

源码/资料依据：
- repos/mifos-web-repo/src/app/loans/loans.service.ts:460
- repos/mifos-web-repo/src/app/web-app.component.ts:221

说明：覆盖 loans.service.ts 与 environment.baseApiUrls；未明确 src/environments/environment.ts 及 window.env 覆盖机制。

## 6. 前端只组装与发送请求，业务规则全部在后端

**结论：未通过**

答案原句：
> 需要注意：本轮证据已经确认了四类业务接口、关键 Service 方法以及租户/认证拦截器；API 基础 URL 的最终拼接 provider、审批和批量接口的实际 batch endpoint、以及还款的具体 command 字符串仍是未核实项。

源码/资料依据：

说明：答案没有明确断言“业务规则全部在后端”，且自述多项接口与配置仍未核实。

## 其它问题

- 答案引用的临时工作区绝对路径包含缺段和重复 repos 前缀；引用归一化后可以映射到工程相对路径，但原始答案的可读性和链接稳定性不合格。
- 只读取 2 个源码文件，6 个关键文件未读取，调查覆盖不足。
