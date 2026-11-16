# Pi 真实问答评测

- 批次：`pi-baseline-glm53flash-20261115`
- 生成时间：2026-11-16T00:45:23.583092+00:00
- 运行数：7

> 工具/搜索指标是确定性计数或启发式代理，不是答案正确性评分；结论可信度仍需按 checks 阅读答案与 Trace。

## native-frontend

**问题**：Mifos X 前端在创建贷款申请、发起审批、放款和还款时，分别调用哪些服务方法和后端接口？API 地址前缀、租户标识和认证是如何统一处理的？

| 组 | 重复 | 状态 | 模型 | 关键文件 | 末键轮次 | 末键后工具/搜索 | 模型调用 | 工具调用 | 读取文件 | 搜索 | 候选/未读 | 空搜索 | 无读取搜索 | 重复调用 | Token | 耗时 s | 引用 确认/搜索见/未交付 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| backbone | 1 | completed | zcode/glm-5.3-flash | 8/8 | 11 | 17/12 | 19 | 48 | 19 | 19 | 334/325 | 2 | 5 | 0 | 539025 | 142.24 | 22/20/0 |
| backbone | 2 | completed | zcode/glm-5.3-flash | 7/8 | 17 | 5/3 | 23 | 39 | 12 | 18 | 78/67 | 1 | 3 | 0 | 327716 | 260.84 | 30/14/1 |
| none | 1 | completed | zcode/glm-5.3-flash | 7/8 | 14 | 26/14 | 32 | 52 | 19 | 20 | 161/153 | 0 | 6 | 0 | 744483 | 234.34 | 23/22/1 |
| none | 2 | completed | zcode/glm-5.3-flash | 8/8 | 14 | 36/22 | 40 | 58 | 22 | 22 | 33/19 | 2 | 5 | 0 | 1131863 | 291.97 | 33/9/0 |

**评测检查项（仅评测器）**

1. 创建：create-loans-account.component.ts 的 submit() → loansService.createLoansAccount → POST /loans
2. 审批/放款：loanActionButtons(loanId, 'approve'|'disburse', data) → POST /loans/{loanId}?command=...
3. 还款：make-repayment.component.ts 解析模板交易类型 → submitLoanActionButton(loanId, data, command) → POST /loans/{loanId}/transactions?command=repayment
4. ApiPrefixInterceptor 统一拼接 /fineract-provider/api/v1 前缀；AuthenticationInterceptor 注入 Fineract-Platform-TenantId 头与 Basic/Bearer 认证
5. 各业务域一个 service 文件（如 loans.service.ts），地址配置在 src/environments/environment.ts，可被运行时 window.env 覆盖
6. 前端只组装与发送请求，业务规则全部在后端

### backbone / 第 1 次

- 状态：completed；Run：`RUN-f41e3d3cf7eb46a0910551e07266c251`；实际模型：zcode/glm-5.3-flash；Pi：1.5.0
- 关键文件齐备轮次：11；之后工具 17 次、搜索 12 次、模型调用 8 次
- 读取原文未核实引用 22/47，其中搜索输出可见 20、读取文件但行区间未交付 0、其它未出现 0、无行号 5
- 未读候选（启发式）：repos/mifos-web-repo/src/app/accounting/common-resolvers/loan-products.resolver.ts、repos/mifos-web-repo/src/app/loans/、repos/mifos-web-repo/src/app/loans/common-resolvers/glim-loan-template.resolver.ts、repos/mifos-web-repo/src/app/loans/common-resolvers/loan-arrear-delinquency.resolver.ts、repos/mifos-web-repo/src/app/loans/common-resolvers/loan-buy-down-fees-data.resolver.ts、repos/mifos-web-repo/src/app/loans/common-resolvers/loan-collaterals.resolver.ts、repos/mifos-web-repo/src/app/loans/common-resolvers/loan-datatable.resolver.ts、repos/mifos-web-repo/src/app/loans/common-resolvers/loan-datatables.resolver.ts、repos/mifos-web-repo/src/app/loans/common-resolvers/loan-deferred-income-data.resolver.ts、repos/mifos-web-repo/src/app/loans/common-resolvers/loan-delinquency-actions.resolver.ts
- 工具时序：R1 ls {"path": "repos/mifos-web-repo"} → completed；R1 find {"pattern": "**/*loan*", "path": "repos/mifos-web-repo"} → completed；R2 grep {"pattern": "createLoan|createLoansAccount", "path": "repos/mifos-web-repo/src/a → completed；R2 ls {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions"} → completed；R2 ls {"path": "repos/mifos-web-repo/src/app/core"} → completed；R3 read {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/app → completed；R3 read {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/dis → completed；R3 read {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/mak → completed；R3 read {"path": "repos/mifos-web-repo/src/app/loans/create-loans-account/create-loans-a → completed；R4 grep {"context": 10, "path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", " → completed；R4 ls {"path": "repos/mifos-web-repo/src/app/core/http"} → completed；R4 ls {"path": "repos/mifos-web-repo/src/app/core/authentication"} → completed；R5 read {"path": "repos/mifos-web-repo/src/app/core/http/api-prefix.interceptor.ts"} → completed；R5 read {"path": "repos/mifos-web-repo/src/app/core/authentication/authentication.interc → completed；R5 read {"path": "repos/mifos-web-repo/src/app/core/core.module.ts"} → completed；R6 read {"path": "repos/mifos-web-repo/src/app/settings/settings.service.ts"} → completed；R6 ls {"path": "repos/mifos-web-repo/src/environments"} → completed；R7 read {"path": "repos/mifos-web-repo/src/environments/environment.ts"} → completed；R7 read {"path": "repos/mifos-web-repo/src/app/core/authentication/authentication.servic → completed；R8 ls {"path": "knowledge/business-context"} → completed；R8 ls {"path": "requirements"} → completed；R8 ls {"path": "repos/loan-middle-repo"} → completed；R9 read {"path": "knowledge/business-context/cross-system.md"} → completed；R9 read {"path": "requirements/系统职责.md"} → completed；R9 read {"path": "requirements/接口与状态约定.md"} → completed；R10 grep {"context": 3, "path": "repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tas → completed；R10 grep {"context": 4, "path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-accou → completed；R10 grep {"context": 2, "path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "p → completed；R11 read {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "offset": 410, " → completed；R11 grep {"context": 4, "path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "p → completed；R11 read {"path": "repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tasks-tabs/loan-a → completed；R12 grep {"context": 6, "path": "repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tas → completed；R12 grep {"context": 8, "path": "repos/mifos-web-repo/src/app/tasks/tasks.service.ts", "p → completed；R13 grep {"context": 6, "path": "repos/mifos-web-repo/src/app/tasks/tasks.service.ts", "p → completed；R13 grep {"context": 8, "path": "repos/mifos-web-repo/src/app/tasks/checker-inbox-and-tas → completed；R14 read {"path": "repos/mifos-web-repo/src/app/core/http/http.service.ts"} → completed；R14 read {"path": "repos/mifos-web-repo/src/app/loans/common-resolvers/loans-account-tran → completed；R14 read {"path": "repos/mifos-web-repo/proxy.conf.js"} → completed；R15 read {"path": "repos/mifos-web-repo/src/app/loans/common-resolvers/loan-action-button → completed；R15 read {"limit": 30, "offset": 30, "path": "repos/mifos-web-repo/src/app/loans/loans.se → completed；R16 grep {"path": "repos/mifos-web-repo/src", "pattern": "setTenantIdentifier", "limit":  → completed；R16 grep {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "pattern": "getL → completed；R17 grep {"context": 2, "path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-accou → completed；R17 grep {"context": 2, "path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-accou → completed；R18 grep {"path": "repos/mifos-web-repo/src/app/loans/create-loans-account/create-loans-a → completed；R18 grep {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/app → completed；R18 grep {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/dis → completed；R18 grep {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/mak → completed

以下结论均基于 `repos/mifos-web-repo` 源码核实（Mifos X 前端直接对接的只有 Fineract 原生 API；渠道后端、贷款中台是另外两套自研应用自己的前端/接口，不在 mifos-web 的调用范围内，此跨系统关系见 `requirements/系统职责.md`）。

## 一、四个业务动作的调用链

### 1. 创建贷款申请

| 环节 | 组件/方法 | 服务方法 | 后端接口 |
| --- | --- | --- | --- |
| 表单模板 | `create-loans-account.component.ts:86`（resolver `loans-account-template.resolver.ts`） | `getLoansAccountTemplateResource()` | `GET /loans/template?clientId/groupId&templateType&productId`（`loans.service.ts:427-436`） |
| 产品切换后模板 | 同组件 `setTemplate()` | `getLoansAccountTemplateResource()` | 同上 |
| **提交创建** | `create-loans-account.component.ts:167,183`：先用 `buildLoanRequestPayload()`（`loans.service.ts:653`）组装 payload（含 locale/dateFormat/datatables），再调 `createLoansAccount(payload)` | `createLoansAccount()` | **`POST /loans`**（`loans.service.ts:460-462`） |

### 2. 发起审批（Approve，也含 Reject/Withdraw）

| 环节 | 组件/方法 | 服务方法 | 后端接口 |
| --- | --- | --- | --- |
| 审批模板 | `loan-action-button.resolver.ts:78-80`（'Approve' 分支） | `getLoanApprovalTemplate()` | `GET /loans/{id}/template?templateType=approval&associations=delinquency`（`loans.service.ts:521-524`） |
| 补充数据 | `approve-loan.component.ts:63,70` | `getApproveAssociationsDetails()`、`getLoanDelinquencyDataForTemplate()` | `GET /loans/{id}?associations=multiDisburseDetails`、`?associations=delinquency`（`loans.service.ts:190-192,104-106`） |
| **提交审批** | `approve-loan.component.ts:128` | `loanActionButtons(loanId, 'approve', data)` | **`POST /loans/{id}?command=approve`**（通用方法 `loans.service.ts:339-342`） |
| 拒绝 | `reject-loan.component.ts:87` | `loanActionButtons(loanId, 'reject', data)` | `POST /loans/{id}?command=reject` |
| 批量审批 | `loan-approval.component.ts:198-203`：对每笔贷款拼 `loans/{id}?command=approve` 的 batch 条目 | `tasksService.submitBatchData()` | **`POST /batches`**（`tasks.service.ts:93-95`），待审列表来自 `GET /loans?status=100`（`tasks.service.ts:60-62`） |

### 3. 放款（Disburse）

| 环节 | 组件/方法 | 服务方法 | 后端接口 |
| --- | --- | --- | --- |
| 放款模板 | `loan-action-button.resolver.ts:70-72`（'Disburse' 分支） | `getLoanActionTemplate(loanId,'disburse')`（自动附加 `associations=delinquency`，`loans.service.ts:30-38`） | `GET /loans/{id}/transactions/template?command=disburse` |
| **提交放款** | `disburse.component.ts:157` | `loanActionButtons(loanId, 'disburse', data)` | **`POST /loans/{id}?command=disburse`** |
| 放到储蓄账户 | `disburse-to-savings-account.component.ts:108` | `loanActionButtons(loanId, 'disbursetosavings', data)` | `POST /loans/{id}?command=disbursetosavings` |
| 批量放款 | `loan-disbursal.component.ts:150-155`：每笔拼 `loans/{id}?command=disburse` | `submitBatchData()` | `POST /batches`；待放款列表 `GET /loans?status=200`（`tasks.service.ts:68-70`） |

### 4. 还款（Repayment / Prepay）

| 环节 | 组件/方法 | 服务方法 | 后端接口 |
| --- | --- | --- | --- |
| 还款模板 | `loan-action-button.resolver.ts:40-42`（'Make Repayment' 分支） | `getLoanActionTemplate(loanId,'repayment')` | `GET /loans/{id}/transactions/template?command=repayment` |
| **提交还款** | `make-repayment.component.ts:191`；command 取自 `dataObject.type.code.split('.')[1]`（如 `transactionType.repayment`→`repayment`） | `submitLoanActionButton(loanId, data, command)` | **`POST /loans/{id}/transactions?command=repayment`**（`loans.service.ts:238-241`） |
| 提前结清试算 | `prepay-loan.component.ts:133` | `getLoanPrepayLoanActionTemplate()`（`command=prepayLoan`，`loans.service.ts:45-66`） | `GET /loans/{id}/transactions/template?command=prepayLoan&transactionDate=…` |
| 提前结清提交 | `prepay-loan.component.ts:179`（合同终止则走 ：188 的 `loanActionButtons(…,'contractTermination')`） | `submitLoanActionButton(…,'repayment')` | `POST /loans/{id}/transactions?command=repayment` |

说明：审批/放款类生命周期动作统一走 `POST /loans/{id}?command=…`（`loanActionButtons`，`loans.service.ts:339-342`），还款类交易统一走 `POST /loans/{id}/transactions?command=…`（`submitLoanActionButton`，`loans.service.ts:238-241`），与 `requirements/接口与状态约定.md` §四记录的 Fineract 原生约定一致。

## 二、API 地址前缀、租户、认证的统一处理

所有前端请求都写相对路径（如 `/loans`），由 `core.module.ts:74-98` 注册的拦截器链（AuthenticationInterceptor → ApiPrefixInterceptor → ErrorHandlerInterceptor → CacheInterceptor → ProgressInterceptor）统一加工，实际 HttpClient 为扩展的 `HttpService`（`http.service.ts`）。

**1. API 前缀 — `api-prefix.interceptor.ts:24-40`**
- 默认前缀 `settingsService.serverUrl` = `server + apiProvider + apiVersion`（`settings.service.ts` 的 `serverUrl` getter；`environment.ts:60-63,106`：`apiProvider='/fineract-provider/api'`、`apiVersion='/v1'`，`baseApiUrl` 默认取 `window.location.origin`，即最终 `https://<host>/fineract-provider/api/v1/loans`）。
- 例外分支：URL 以 `/v1..v9` 开头走不带版本的 `baseServerUrl`；含 `/actuator/` 走 `serverHost`；含 `http(s):` 的完整 URL 与 i18n 请求不加工。

**2. 租户标识 — `authentication.interceptor.ts:15-18,33-38`**
- 每个请求注入头 `Fineract-Platform-TenantId`；默认值来自 `environment.fineractPlatformTenantId`（`environment.ts:26`，默认 `'default'`）。
- 运行时可覆盖：登录时 `login.component.ts:101-103`、`web-app.component.ts:227-229` 把 `environment.fineractPlatformTenantId` 写入 localStorage（`settings.service.ts:86-88` 的 `mifosXTenantIdentifier`），拦截器每次请求读取 `settingsService.tenantIdentifier` 覆盖默认头；页头的 tenant-selector 组件（`tenant-selector.component.ts:43-44`）也可切换。

**3. 认证 — `authentication.interceptor.ts` + `authentication.service.ts`**
- Basic 模式：登录 `POST /authentication`（`authentication.service.ts:185-190`），成功后 `onLoginSuccess` 调 `setAuthorizationToken(base64EncodedAuthenticationKey)`，后续请求统一带 `Authorization: Basic <key>`（`authentication.interceptor.ts:43-50`）；支持两步验证，OTP 校验后加 `Fineract-Platform-TFA-Token` 头（:52-58, 96-98）。
- OAuth2/OIDC 模式（`environment.oauth.enabled`，`environment.ts:65-73`）：由 `angular-oauth2-oidc` 走授权码 + PKCE，token 刷新后经 `updateCredentialsToken()` 同步到同一个 `Authorization: Bearer <token>` 头（`authentication.service.ts:125-135`）；凭证存于 sessionStorage/localStorage（`mifosXCredentials`）。

**证据缺口**：`mifos-web` 内未发现任何指向 channel-service（8091）/ loan-middle（8092）/ simulator（8093）的请求路径，与业务文档“三系统独立、中台经 `fineract-adapter` 自行调 Fineract”一致；但中台侧实际调用 Fineract 的代码在 `repos/loan-middle-repo`，本轮未逐一核实，如需中台如何复用同一批 Fineract 端点可继续下钻。


### backbone / 第 2 次

- 状态：completed；Run：`RUN-35a2249e102b4d87afc2298e15f82852`；实际模型：zcode/glm-5.3-flash；Pi：1.5.0
- 未读取的关键文件：repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/make-repayment/make-repayment.component.ts
- 最后关键文件轮次：17；之后工具 5 次、搜索 3 次、模型调用 6 次
- 读取原文未核实引用 30/47，其中搜索输出可见 14、读取文件但行区间未交付 1、其它未出现 0、无行号 2
- 未交付引用（读取原文与搜索输出均未出现）：repos/mifos-web-repo/src/app/login/login.component.ts:99-103
- 未读候选（启发式）：repos/mifos-web-repo/src/app/organization/bulk-loan-reassignmnet/bulk-loan-reassignmnet.component.ts、repos/mifos-web-repo/src/app/organization/organization.service.ts、repos/mifos-web-repo/src/app/products/products.service.ts、repos/mifos-web-repo/src/app/products/loan-products/create-loan-product/create-loan-product.component.ts、repos/mifos-web-repo/src/app/products/loan-products/loan-product-stepper/loan-product-terms-step/loan-product-terms-step.component.ts、repos/mifos-web-repo/src/app/products/loan-products/view-loan-product/general-tab/general-tab.component.ts、repos/mifos-web-repo/src/app/products/loan-products/loan-product-stepper/loan-product-interest-refund-step/loan-product-interest-refund-step.component.ts、repos/mifos-web-repo/src/app/products/loan-products/loan-product-stepper/loan-product-details-step/loan-product-details-step.component.ts、repos/mifos-web-repo/src/app/loans/loans-account-stepper/loans-account-details-step/loans-account-details-step.component.ts、repos/mifos-web-repo/src/app/products/loan-products/loan-product-stepper/loan-product-settings-step/loan-product-settings-step.component.ts
- 工具时序：R1 ls {"path": "repos/mifos-web-repo"} → completed；R2 ls {"path": "repos/mifos-web-repo/src"} → completed；R2 grep {"pattern": "createLoan|saveLoan|submitLoan", "path": "repos/mifos-web-repo/src" → completed；R3 read {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "offset": 220, " → completed；R4 read {"limit": 80, "offset": 140, "path": "repos/mifos-web-repo/src/app/loans/create- → completed；R4 find {"pattern": "src/app/**/*interceptor*.ts", "path": "repos/mifos-web-repo/src"} → completed；R4 grep {"glob": "*.ts", "limit": 40, "path": "repos/mifos-web-repo/src", "pattern": "Fi → completed；R5 read {"path": "repos/mifos-web-repo/src/app/core/authentication/authentication.interc → completed；R5 read {"path": "repos/mifos-web-repo/src/app/core/authentication/authentication.helper → error；R6 read {"limit": 120, "path": "repos/mifos-web-repo/src/app/core/authentication/authent → completed；R6 ls {"path": "repos/mifos-web-repo/src/environments"} → completed；R7 read {"path": "repos/mifos-web-repo/src/environments/environment.ts"} → completed；R7 grep {"glob": "*.ts", "limit": 30, "path": "repos/mifos-web-repo/src", "pattern": "pr → completed；R8 grep {"glob": "*.ts", "limit": 40, "path": "repos/mifos-web-repo/src/app", "pattern": → completed；R9 read {"path": "repos/mifos-web-repo/src/app/core/http/api-prefix.interceptor.ts"} → completed；R9 read {"limit": 70, "offset": 160, "path": "repos/mifos-web-repo/src/app/settings/sett → completed；R10 grep {"glob": "*.ts", "limit": 40, "path": "repos/mifos-web-repo/src/app/loans", "pat → completed；R11 grep {"glob": "make-repayment.component.ts", "limit": 30, "path": "repos/mifos-web-re → completed；R11 read {"limit": 60, "offset": 100, "path": "repos/mifos-web-repo/src/app/loans/loans-v → completed；R12 grep {"glob": "loans.service.ts", "limit": 15, "path": "repos/mifos-web-repo/src/app/ → completed；R12 read {"limit": 30, "offset": 95, "path": "repos/mifos-web-repo/src/app/core/core.modu → completed；R12 grep {"glob": "authentication.service.ts", "limit": 20, "path": "repos/mifos-web-repo → completed；R13 read {"limit": 75, "offset": 160, "path": "repos/mifos-web-repo/src/app/core/authenti → completed；R13 read {"limit": 80, "offset": 640, "path": "repos/mifos-web-repo/src/app/loans/loans.s → completed；R14 ls {"path": "knowledge/business-context"} → completed；R14 ls {"path": "repos/channel-service-repo"} → completed；R15 grep {"path": "knowledge/business-context", "pattern": "审批|放款|还款|创建贷款|租户|tenant", "li → completed；R15 grep {"path": "repos/channel-service-repo/src", "pattern": "loans|disburse|repayment| → completed；R16 read {"limit": 55, "offset": 55, "path": "repos/mifos-web-repo/src/app/core/core.modu → completed；R16 grep {"glob": "authentication.service.ts", "limit": 10, "path": "repos/mifos-web-repo → completed；R17 grep {"glob": "core.module.ts", "limit": 10, "path": "repos/mifos-web-repo/src/app/co → completed；R17 grep {"glob": "settings.service.ts", "limit": 10, "path": "repos/mifos-web-repo/src/a → completed；R17 grep {"glob": "environment.ts", "limit": 5, "path": "repos/mifos-web-repo/src/environ → completed；R17 read {"limit": 25, "offset": 140, "path": "repos/mifos-web-repo/src/app/loans/loans-v → completed；R18 read {"limit": 40, "offset": 100, "path": "repos/mifos-web-repo/src/app/login/login.c → completed；R19 read {"path": "repos/mifos-web-repo/proxy.conf.js"} → completed；R20 grep {"glob": "loans.service.ts", "limit": 20, "path": "repos/mifos-web-repo/src/app/ → completed；R21 grep {"glob": "*.ts", "limit": 10, "path": "repos/mifos-web-repo/src/app/loans", "pat → completed；R22 grep {"glob": "*.ts", "limit": 15, "path": "repos/mifos-web-repo/src", "pattern": "vN → completed

## 结论概览

Mifos X 前端（`repos/mifos-web-repo`）的贷款四类操作都集中在 `src/app/loans/loans.service.ts` 的少量通用方法里，组件只负责组装表单并调用；所有请求走相对路径，由两个全局拦截器统一补齐 **API 前缀**、**租户头** 和 **认证头**。前端只对接 Fineract API（`/fineract-provider/api/v1`），代码中没有对 channel-service / loan-middle 的任何调用（已 grep 核实，无 `loan-middle|channel-service|drawdown` 引用）。

## 一、创建贷款申请

| 环节 | 服务方法 | 后端接口 |
|---|---|---|
| 取模板 | `getLoansAccountTemplateResource`（`loans.service.ts:427`），由 `loans-account-template.resolver.ts:25` 触发 | `GET /loans/template?activeOnly=true&staffInSelectedOfficeOnly=true&clientId=…&templateType=individual` |
| 试算还款计划 | `calculateLoanSchedule`（`loans.service.ts:641-642`） | `POST /loans?command=calculateLoanSchedule` |
| **正式提交** | `createLoansAccount`（`loans.service.ts:460-462`） | `POST /loans` |

- 提交入口在 `create-loans-account.component.ts:183` 的 `submit()`：先把各步骤表单合并为 `loansAccount`（`create-loans-account.component.ts:151-158`），再由 `buildLoanRequestPayload`（`loans.service.ts:653`）做日期格式化（`dateFormat`/`locale`）、charges/collateral/disbursementData 映射和 `loanType`（individual/group/glim）判定。
- 试算由还款计划步骤 `loans-account-schedule-step.component.ts:59` 调用。

## 二、发起审批（approve）

- 入口 `approve-loan.component.ts:128`：`loanService.loanActionButtons(this.loanId, 'approve', data)`，payload 含 `approvedOnDate`、`expectedDisbursementDate`、`dateFormat`、`locale`（`approve-loan.component.ts:110-127`）。
- 服务方法 `loanActionButtons`（`loans.service.ts:339-343`）：**`POST /loans/{loanId}?command={command}`**。
- 同一端点承载其他审批相关动作：`reject`（`reject-loan.component.ts:87`）、`withdrawnByApplicant`（`withdrawn-by-client.component.ts:90`）、`assignLoanOfficer`（`assign-loan-officer.component.ts:88`）、`undoapproval`（`undo-approval.component.ts:38`）。

## 三、放款（disburse）

- 入口 `disburse.component.ts:157`：`loanService.loanActionButtons(this.loanId, 'disburse', data)` → **`POST /loans/{loanId}?command=disburse`**；payload 含 `actualDisbursementDate`、`transactionAmount`、`dateFormat`、`locale`（`disburse.component.ts:140-152`）。
- 放款到储蓄账户：`disburse-to-savings-account.component.ts:108` 用 `command=disbursetosavings`，同一端点。
- 辅助接口：`editDisbursements`（`loans.service.ts:388`）→ `PUT /loans/{loanId}/disbursements/editDisbursements`；放款模板预览走 `GET /loans/{loanId}/transactions/template`（`loans.service.ts:372-379`）。

## 四、还款（repayment）

- 服务方法 `submitLoanActionButton`（`loans.service.ts:238-243`）：**`POST /loans/{loanId}/transactions?command={command}`**（与审批不同，这里走 `transactions` 子资源）。
- `command` 由具体动作决定：
  - 常规还款/预还款：`prepay-loan.component.ts:179` → `command=repayment`；
  - 通用还款弹窗：`make-repayment.component.ts:191` → `command` 从 `dataObject.type.code.split('.')[1]` 动态解析（`make-repayment.component.ts:75`，如 capitalizedIncome、buyDownFee 等）；
  - 恢复性还款：`recovery-repayment.component.ts:146` → `command=recoverypayment`；
  - 还有 `waiveinterest`、`close`、`writeoff`、`foreclosure` 等同端点动作。

## 五、API 前缀、租户、认证的统一处理

三个机制都在 `core` 层注册为全局 HTTP 拦截器（`core.module.ts:67-79`，`provideHttpClient(withInterceptorsFromDi())` 在 `core.module.ts:110`），业务 Service 全部只写相对路径：

1. **API 前缀 — `ApiPrefixInterceptor`**（`core/http/api-prefix.interceptor.ts:17-37`）
   - 把相对路径请求拼接为 `settingsService.serverUrl`，即 `server + apiProvider + apiVersion`（`settings.service.ts:175-180`）= `{baseApiUrl}/fineract-provider/api/v1`；`baseApiUrl` 来自 `window.env.fineractApiUrl`，缺省为 `window.location.origin`（`environment.ts:22-26`），`environment.ts:88` 组装出完整 `serverUrl`。
   - 以 `http://`/`https://` 开头的完整 URL 不加前缀（OAuth2 端点由 angular-oauth2-oidc 库自己处理）；`/v1../` 开头的走 `baseServerUrl`、`/actuator/` 走 serverHost 的特殊分支在 `api-prefix.interceptor.ts:23-30`。

2. **租户标识 — `AuthenticationInterceptor`**（`core/authentication/authentication.interceptor.ts:32-41`）
   - 每个请求都注入头 `Fineract-Platform-TenantId`：默认值 `environment.fineractPlatformTenantId`（`'default'`，`environment.ts:15`），若登录页租户选择器选过，则用 localStorage 的 `mifosXTenantIdentifier` 覆盖（`settings.service.ts:86-88, 223-226`；登录页初始化见 `login.component.ts:99-103`）。OAuth2 启用时租户选择器被隐藏、租户由 OAuth 服务器决定（`login.component.ts:108-113`）。

3. **认证 — 同一拦截器 + `AuthenticationService`**
   - Basic 模式登录：`POST /authentication`（`authentication.service.ts:187-196`），成功后 `setAuthorizationToken` 写入 `Authorization` 头：`environment.oauth.enabled` 为真用 `Bearer <token>`，否则 `Basic <base64(user:pass)>`（`authentication.interceptor.ts:47-55`）。凭据存 sessionStorage/localStorage 的 `mifosXCredentials`。
   - 双因子：额外头 `Fineract-Platform-TFA-Token`（`authentication.interceptor.ts:57-61`）。
   - OAuth2/OIDC 模式由 PKCE 授权码流获取 access token 后同样调用 `setAuthorizationToken`（`authentication.service.ts:119, 147`），OIDC 用户详情走 `POST {oidcApiUrl}authentication/userdetails`（`authentication.service.ts:225-230`）。

## 证据缺口（未核实项）

- 前端 `environment.ts` 中的 `vNextApiUrl`（`apis.flexcore.mx`，`environment.ts:71`）只在账户转账 `account-transfers.service.ts:127-146` 使用，与贷款流程无关。
- 本轮授权的 channel-service-repo / loan-middle-repo / fineract-repo 中，接收上述 `/loans` 请求的具体 Controller 属于 Fineract 侧，尚未逐行核实其入参处理；如需后端视角（含中台的 `POST /repayments` 等，见 `channel-service-repo/.../ChannelController.java:38`）可进一步调查。


### none / 第 1 次

- 状态：completed；Run：`RUN-70b7a8e712ba463ea7fce13b0a02474c`；实际模型：zcode/glm-5.3-flash；Pi：1.5.0
- 未读取的关键文件：repos/mifos-web-repo/src/app/loans/create-loans-account/create-loans-account.component.ts
- 最后关键文件轮次：14；之后工具 26 次、搜索 14 次、模型调用 18 次
- 读取原文未核实引用 23/53，其中搜索输出可见 22、读取文件但行区间未交付 1、其它未出现 0、无行号 7
- 未交付引用（读取原文与搜索输出均未出现）：repos/mifos-web-repo/src/app/core/http/api-prefix.interceptor.ts:26-43
- 未读候选（启发式）：repos/mifos-web-repo/src/app/loans/loans.service.ts-30-  getLoanActionTemplate(loanId、repos/mifos-web-repo/src/app/loans/loans.service.ts-57-  getLoanForeclosureActionTemplate(loanId、repos/mifos-web-repo/src/app/loans/loans.service.ts-69-  getLoanAccountResource(loanId、repos/mifos-web-repo/src/app/loans/loans.service.ts-238-  submitLoanActionButton(loanId、repos/mifos-web-repo/src/app/loans/loans.service.ts-249-  getReAgePreview(loanId、repos/mifos-web-repo/src/app/loans/loans.service.ts-267-  getReAmortizePreview(loanId、repos/mifos-web-repo/src/app/loans/loans.service.ts-279-  getLoanScreenReportsData()、repos/mifos-web-repo/src/app/loans/loans-view/account-details/account-details.component.ts、repos/mifos-web-repo/src/app/loans/loans-view/charges-tab/charges-tab.component.ts、repos/mifos-web-repo/src/app/loans/loans-view/datatable-tab/datatable-tab.component.ts
- 工具时序：R1 ls {"path": "repos/mifos-web-repo"} → completed；R1 ls {"path": "requirements"} → completed；R2 ls {"path": "repos/mifos-web-repo/src"} → completed；R2 read {"path": "repos/mifos-web-repo/env.sample"} → completed；R3 ls {"path": "repos/mifos-web-repo/src/app"} → completed；R3 ls {"path": "repos/mifos-web-repo/src/environments"} → completed；R4 read {"path": "repos/mifos-web-repo/src/environments/environment.ts"} → completed；R4 ls {"path": "repos/mifos-web-repo/src/app/core"} → completed；R5 ls {"path": "repos/mifos-web-repo/src/app/core/authentication"} → completed；R5 ls {"path": "repos/mifos-web-repo/src/app/core/http"} → completed；R5 ls {"path": "repos/mifos-web-repo/src/app/loans"} → completed；R6 read {"path": "repos/mifos-web-repo/src/app/core/http/api-prefix.interceptor.ts"} → completed；R6 read {"path": "repos/mifos-web-repo/src/app/core/http/http.service.ts"} → completed；R7 read {"path": "repos/mifos-web-repo/src/app/core/authentication/authentication.interc → completed；R7 read {"path": "repos/mifos-web-repo/src/app/core/authentication/authentication.servic → completed；R8 grep {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "pattern": "crea → completed；R9 read {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "limit": 30} → completed；R9 find {"path": "repos/mifos-web-repo/src/app/loans/loans-view", "pattern": "*.ts"} → completed；R10 ls {"path": "repos/mifos-web-repo/src/app/loans/create-loans-account"} → completed；R10 read {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/app → completed；R11 grep {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "pattern": "loan → completed；R12 grep {"pattern": "loanActionButtons", "path": "repos/mifos-web-repo/src/app/loans/loa → completed；R12 grep {"pattern": "createLoansAccount|submit|save|this\\.loansService|loansService\\." → completed；R13 grep {"context": 6, "path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "p → completed；R14 read {"limit": 80, "offset": 68, "path": "repos/mifos-web-repo/src/app/loans/loans-vi → completed；R14 read {"limit": 80, "offset": 40, "path": "repos/mifos-web-repo/src/app/loans/loans-vi → completed；R15 read {"limit": 40, "offset": 148, "path": "repos/mifos-web-repo/src/app/loans/loans-v → completed；R15 read {"limit": 77, "offset": 120, "path": "repos/mifos-web-repo/src/app/loans/loans-v → completed；R16 grep {"context": 3, "path": "repos/mifos-web-repo/src/app/settings", "pattern": "serv → completed；R16 grep {"context": 6, "path": "repos/mifos-web-repo/src/app/app.module.ts", "pattern":  → completed；R17 read {"path": "repos/mifos-web-repo/src/app/core/core.module.ts"} → completed；R17 grep {"context": 5, "path": "repos/mifos-web-repo/src/app/settings/settings.service.t → completed；R18 read {"limit": 80, "offset": 120, "path": "repos/mifos-web-repo/src/app/settings/sett → completed；R18 grep {"context": 2, "limit": 40, "path": "repos/mifos-web-repo/src/app", "pattern": " → completed；R19 grep {"context": 3, "path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "p → completed；R19 read {"limit": 60, "path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-accoun → completed；R20 grep {"context": 2, "path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-accou → completed；R20 read {"path": "requirements/接口与状态约定.md"} → completed；R21 read {"path": "repos/mifos-web-repo/src/app/core/authentication/oauth.config.ts"} → completed；R21 ls {"path": "repos/mifos-web-repo/src/app/loans/loans-account-stepper"} → completed；R22 grep {"context": 1, "path": "repos/mifos-web-repo/src/app/loans/loans-routing.module. → completed；R23 read {"path": "repos/mifos-web-repo/proxy.conf.js"} → completed；R23 read {"limit": 50, "path": "repos/mifos-web-repo/src/app/core/http/error-handler.inte → completed；R24 read {"limit": 40, "path": "repos/mifos-web-repo/src/environments/.env.ts"} → completed；R25 grep {"context": 3, "path": "repos/mifos-web-repo/src/app/loans/loans-routing.module. → completed；R26 grep {"context": 3, "path": "repos/mifos-web-repo/src/app/loans/loans-routing.module. → completed；R27 read {"path": "repos/mifos-web-repo/src/app/loans/common-resolvers/loan-action-button → completed；R28 grep {"context": 4, "path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "p → completed；R29 grep {"context": 1, "path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-accou → completed；R30 grep {"context": 4, "path": "repos/mifos-web-repo/src/app/loans/loans-account-stepper → completed；R31 grep {"context": 1, "path": "repos/mifos-web-repo/src/environments/environment.ts", " → completed；R31 grep {"context": 2, "path": "repos/mifos-web-repo/src/app/core/authentication/authent → completed

以下结论均基于 mifos-web-repo 源码逐行核实（Angular 前端，Mifos X Web App），并结合 requirements/接口与状态约定.md 对照。

## 一、四个业务动作的服务方法与后端接口

所有贷款相关的 HTTP 调用集中在 `LoansService`（`repos/mifos-web-repo/src/app/loans/loans.service.ts`），服务内部直接用相对路径调用 Fineract 原生接口，由拦截器统一补前缀。

### 1. 创建贷款申请
| 环节 | 前端方法 | 后端接口 |
| --- | --- | --- |
| 取模板 | `getLoansAccountTemplateResource()` loans.service.ts:427-433 | `GET /loans/template?templateType=individual\|group&clientId/groupId=&productId=` |
| 组装 payload | `buildLoanRequestPayload()` loans.service.ts:653（组件在 create-loans-account.component.ts:167 调用） | — |
| 提交创建 | `createLoansAccount(payload)` loans.service.ts:460-462（组件 submit 在 create-loans-account.component.ts:183 调用） | `POST /loans` |

入口路由为 `loans/create`（loans-routing.module.ts:77-79），创建页为多步 stepper（details/terms/charges/schedule/preview）。

### 2. 发起审批（Approve）
| 环节 | 前端方法 | 后端接口 |
| --- | --- | --- |
| 取审批模板 | `getLoanApprovalTemplate()` loans.service.ts:521-524（由 `LoanActionButtonResolver` 在 loan-action-button.resolver.ts 的 'Approve' 分支调用） | `GET /loans/{loanId}/template?templateType=approval&associations=delinquency` |
| 审批辅助数据 | `getApproveAssociationsDetails()` loans.service.ts:190-193、`getLoanDelinquencyDataForTemplate()` loans.service.ts:104-107（approve-loan.component.ts ngOnInit） | `GET /loans/{loanId}?associations=multiDisburseDetails` / `?associations=delinquency` |
| 提交审批 | `loanActionButtons(loanId, 'approve', data)` loans.service.ts:339-342（approve-loan.component.ts:128 调用） | `POST /loans/{loanId}?command=approve`，body 含 `approvedOnDate/expectedDisbursementDate/approvedLoanAmount/note/dateFormat/locale` |

审批动作走“账户级 command”端点 `POST /loans/{loanId}`，与“拒绝/撤销审批/撤销放款”同路径，只是 `?command=` 不同（如 reject-loan.component.ts:87 用 `'reject'`，undo-approval.component.ts:38 用 `'undoapproval'`）。

### 3. 放款（Disburse）
| 环节 | 前端方法 | 后端接口 |
| --- | --- | --- |
| 取放款模板 | `getLoanActionTemplate(loanId, 'disburse')` loans.service.ts:30-38（resolver 'Disburse' 分支）；该方**对 disburse/disbursetosavings 自动附加 `associations=delinquency`**（loans.service.ts:33-35） | `GET /loans/{loanId}/transactions/template?command=disburse` |
| 提交放款（现金/缴费渠道） | `loanActionButtons(loanId, 'disburse', data)`（disburse.component.ts:157） | `POST /loans/{loanId}?command=disburse` |
| 放款至储蓄账户 | `loanActionButtons(loanId, 'disbursetosavings', data)`（disburse-to-savings-account.component.ts:108） | `POST /loans/{loanId}?command=disbursetosavings` |

body 含 `actualDisbursementDate/transactionAmount/externalId/paymentTypeId/note` 及可选支付明细字段（disburse.component.ts:124-157）。

### 4. 还款（Repayment）
| 环节 | 前端方法 | 后端接口 |
| --- | --- | --- |
| 取还款模板 | `getLoanActionTemplate(loanId, 'repayment')`（resolver 'Make Repayment' 分支，loan-action-button.resolver.ts） | `GET /loans/{loanId}/transactions/template?command=repayment` |
| 提交还款 | `submitLoanActionButton(loanId, data, command)` loans.service.ts:238-241（make-repayment.component.ts:191 调用） | `POST /loans/{loanId}/transactions?command=repayment` |

`make-repayment` 组件是多种资金交易的复用组件：command 从模板的 `dataObject.type.code` 截取（make-repayment.component.ts:75），同一端点支持 `repayment`、`goodwillCredit`、`capitalizedIncome`、`buyDownFee` 等；提前还款（prepay-loan.component.ts:179）也走 `POST /loans/{loanId}/transactions?command=repayment`，而"合同终止"走 `loanActionButtons(..., 'contractTermination')`（prepay-loan.component.ts:188，即 `POST /loans/{loanId}?command=contractTermination`）。

也就是说，前端把 Fineract 的两类动作端点分开封装：
- `loanActionButtons()` → `POST /loans/{loanId}?command=...`（审批/放款/拒绝等账户级动作）
- `submitLoanActionButton()` → `POST /loans/{loanId}/transactions?command=...`（还款/核销/关闭等资金交易动作）

## 二、API 地址前缀的统一处理

1. **前缀拼接**：`ApiPrefixInterceptor`（core/http/api-prefix.interceptor.ts:26-43）拦截所有请求：默认拼 `settingsService.serverUrl`；URL 以 `/vN/` 开头时改拼不含版本的 `baseServerUrl`；URL 含 `/actuator/` 时拼 `serverHost`；已经是完整 `http(s)://` 的 URL 不处理。
2. **serverUrl 组成**：`server + apiProvider + apiVersion`，即 `{baseApiUrl}/fineract-provider/api/v1`（settings.service.ts:175-177；environment.ts:88 同样计算 `environment.serverUrl`）。`server` 优先取 localStorage `mifosXServerURL`，否则取环境变量 `baseApiUrl`（settings.service.ts:150-160）。
3. **环境来源**：`baseApiUrl`/`apiProvider`/`apiVersion` 来自 `window.env`（Docker 部署注入 env.sample 中 `FINERACT_API_URL`、`FINERACT_API_PROVIDER=/fineract-provider/api`、`FINERACT_API_VERSION=/v1`），默认回落 `window.location.origin`（environments/environment.ts:14-28）。
4. **注入方式**：`HttpService` 继承 HttpClient 并把 `ApiPrefixInterceptor + ErrorHandlerInterceptor` 作为动态拦截器链（core/http/http.service.ts:62-73），在 core.module.ts 中以 `{provide: HttpClient, useClass: HttpService}` 全局替换（core.module.ts:84-91），因此所有业务服务拿到的 HttpClient 自动带前缀。

## 三、租户标识的统一处理

- **请求头**：`AuthenticationInterceptor` 给每个请求注入 `Fineract-Platform-TenantId` 头（core/authentication/authentication.interceptor.ts:12-16 定义常量，:35-40 在 intercept 中 clone 请求；若 `settingsService.tenantIdentifier` 存在则覆盖默认值）。该拦截器在 core.module.ts:68-72 注册为全局 `HTTP_INTERCEPTORS`。
- **取值来源**：默认 `environment.fineractPlatformTenantId`（默认 `'default'`，environment.ts:19）；登录页 `reloadSettings()` 会写入 `localStorage['mifosXTenantIdentifier']`（login.component.ts:101-103），web-app.component.ts:226-228 在未设置时兜底写入；顶栏 `tenant-selector` 组件可切换并调用 `setTenantIdentifier()`（shared/tenant-selector/tenant-selector.component.ts:43-45）。
- **多租户列表**：`environment.fineractPlatformTenantIds`（逗号分隔，env.sample:10）供下拉选择。

## 四、认证的统一处理

- **三种模式**：`getActiveAuthMode()` 依环境配置返回 Basic / OAuth2 / OIDC（core/authentication/oauth.config.ts:14-21；开关在 environment.ts:38-45 oauth 块与 :97-104 OIDC 块）。
- **Basic 模式**：登录 `POST /authentication`（authentication.service.ts:189-194，经前缀拦截器实际为 `/fineract-provider/api/v1/authentication`），成功后把返回的 `base64EncodedAuthenticationKey` 设置到拦截器（authentication.service.ts:243-247 → interceptor 的 `setAuthorizationToken()`，authentication.interceptor.ts:47-55），后续请求自动带 `Authorization: Basic <key>`；凭据存 sessionStorage/localStorage（key `mifosXCredentials`）。
- **OAuth2/OIDC 模式**：由 angular-oauth2-oidc 管理 token（authentication.service.ts:38-67、184-187 PKCE code flow），token 到手后同样写入 `Authorization: Bearer <token>`；OIDC 的用户详情经 `${oidcApiUrl}authentication/userdetails`（authentication.service.ts:139-156）。
- **双因子**：`/twofactor`、`/twofactor/validate`、`/twofactor/invalidate`（authentication.service.ts:459、468、337-341），OTP 通过后注入独立头 `Fineract-Platform-TFA-Token`（authentication.interceptor.ts:20、57-62）。
- **模块级选择**：若启用 Zitadel OIDC，app.module.ts:131-134 会用 `ZitadelTokenInterceptor` 替换标准 `AuthenticationInterceptor`。
- **守卫**：`AuthenticationGuard`（core/authentication/authentication.guard.ts）配合 `AuthenticationGuard`/`isAuthenticated()` 控制路由访问。

## 五、对照需求的说明

requirements/接口与状态约定.md 第四部分描述的 Fineract 原生约定与前端实现一致：API 前缀 `/fineract-provider/api/v1`、租户头 `Fineract-Platform-TenantId`、动作经 `?command=` 查询参数；其中 `POST /loans`、`POST /loans/{id}?command=disburse`、`POST /loans/{id}/transactions?command=repayment` 正是上文前端四类动作实际命中的端点（该文件注明这些端点主要由中台 loan-middle 调用，Mifos X 前端与中台共用同一套 Fineract 契约）。

**证据缺口**：本次仅核实了前端仓库；`proxy.conf.js` 中的 `/api` 代理指向 chucknorris.io 示例站点，与 Fineract 无关（仅开发期 i18n 等资源代理，不影响贷款接口）。贷款动作在后端（fineract-repo）的具体处理类（如 `LoanApiResource`/`LoanLifecycleStateMachine`）本轮未逐一打开核实，如需后端实现细节可继续追查。


### none / 第 2 次

- 状态：completed；Run：`RUN-2ffade5afd324d78bb34dd7144b72554`；实际模型：zcode/glm-5.3-flash；Pi：1.5.0
- 关键文件齐备轮次：14；之后工具 36 次、搜索 22 次、模型调用 26 次
- 读取原文未核实引用 33/44，其中搜索输出可见 9、读取文件但行区间未交付 0、其它未出现 0、无行号 2
- 未读候选（启发式）：repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/infrastructure/security/service/TenantAwareJpaPlatformUserDetailsService.java、repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/infrastructure/security/service/AuthTenantDetailsServiceJdbc.java、repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/infrastructure/security/service/TwoFactorConfigurationServiceImpl.java、repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/infrastructure/security/service/TwoFactorServiceImpl.java、repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/disburse-to-savings-account/disburse-to-savings-account.component.ts、repos/mifos-web-repo/src/app/loans/loans-account-stepper/loans-account-schedule-step/loans-account-schedule-step.component.ts、repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/edit-repayment-schedule/edit-repayment-schedule.component.ts、repos/mifos-web-repo/src/app/loans/loans-view/transactions/view-transaction/view-transaction.component.ts、repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/undo-approval/undo-approval.component.ts、repos/mifos-web-repo/src/app/loans/loans-view/loans-view.component.ts
- 工具时序：R1 ls {"path": "repos/mifos-web-repo"} → completed；R2 ls {"path": "repos/mifos-web-repo/src"} → completed；R3 ls {"limit": 100, "path": "repos/mifos-web-repo/src/app"} → completed；R4 ls {"limit": 200, "path": "repos/mifos-web-repo/src/app/loans"} → completed；R4 ls {"limit": 100, "path": "repos/mifos-web-repo/src/app/core"} → completed；R5 read {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts"} → completed；R5 ls {"path": "repos/mifos-web-repo/src/app/core/http"} → completed；R6 read {"path": "repos/mifos-web-repo/src/app/core/http/api-prefix.interceptor.ts"} → completed；R6 read {"path": "repos/mifos-web-repo/src/app/core/http/http.service.ts"} → completed；R6 ls {"path": "repos/mifos-web-repo/src/app/core/authentication"} → completed；R7 read {"path": "repos/mifos-web-repo/src/app/core/authentication/authentication.interc → completed；R7 read {"path": "repos/mifos-web-repo/src/app/core/authentication/authentication.servic → completed；R8 read {"path": "repos/mifos-web-repo/src/app/settings/settings.service.ts"} → completed；R9 read {"path": "repos/mifos-web-repo/src/environments/environment.ts"} → completed；R10 ls {"path": "repos/mifos-web-repo/src/app/loans/create-loans-account"} → completed；R10 ls {"path": "repos/mifos-web-repo/src/app/loans/loans-view"} → completed；R11 read {"path": "repos/mifos-web-repo/src/app/loans/create-loans-account/create-loans-a → completed；R12 ls {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions"} → completed；R13 read {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/app → completed；R13 read {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/dis → completed；R14 read {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/mak → completed；R14 read {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/loa → completed；R15 grep {"pattern": "interceptor|ApiPrefixInterceptor|AuthenticationInterceptor", "path" → completed；R15 read {"path": "repos/mifos-web-repo/proxy.conf.js"} → completed；R16 read {"path": "repos/mifos-web-repo/src/app/core/core.module.ts", "limit": 60, "offse → completed；R17 grep {"path": "repos/fineract-repo", "pattern": "@Path\\(\"/loans\"\\)", "glob": "**/ → completed；R17 grep {"path": "repos/fineract-repo", "pattern": "command=repayment|REPAYMENT.*command → completed；R18 grep {"glob": "**/LoansApiResource.java", "path": "repos/fineract-repo", "pattern": " → completed；R19 read {"limit": 90, "offset": 549, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R20 grep {"glob": "**/LoanTransactionsApiResource.java", "limit": 15, "pattern": "@Path\\ → completed；R20 grep {"glob": "**/ToLoanApiJsonSerializerService.java", "path": "repos/fineract-repo" → completed；R21 read {"limit": 60, "offset": 283, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R21 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R22 read {"limit": 76, "offset": 121, "path": "repos/mifos-web-repo/src/app/loans/loans-v → completed；R22 grep {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/dis → completed；R23 ls {"path": "requirements"} → completed；R24 read {"path": "requirements/接口与状态约定.md"} → completed；R25 read {"path": "requirements/系统职责.md"} → completed；R26 grep {"path": "repos/mifos-web-repo/src/app/loans", "pattern": "getLoanActionTemplate → completed；R27 read {"path": "repos/mifos-web-repo/src/app/loans/common-resolvers/loan-action-button → completed；R28 read {"limit": 40, "offset": 95, "path": "repos/fineract-repo/fineract-provider/src/m → completed；R28 grep {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/pre → completed；R29 grep {"path": "repos/mifos-web-repo/src/app/loans", "pattern": "loanActionButtons\\(" → completed；R30 grep {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "pattern": "crea → completed；R30 grep {"path": "repos/mifos-web-repo/src/app/core/authentication/authentication.servic → completed；R31 read {"limit": 70, "offset": 125, "path": "repos/mifos-web-repo/src/app/loans/loans-v → completed；R32 grep {"path": "repos/mifos-web-repo/src/app/loans/loans-account-stepper", "pattern":  → completed；R33 grep {"path": "repos/mifos-web-repo/src/app/loans/common-resolvers", "pattern": "getL → completed；R33 ls {"path": "repos/mifos-web-repo/src/app/loans/common-resolvers"} → completed；R34 read {"path": "repos/mifos-web-repo/src/app/loans/common-resolvers/loans-account-temp → completed；R35 grep {"path": "repos/loan-middle-repo", "pattern": "fineract-provider/api|Fineract-Pl → completed；R36 grep {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/mak → completed；R36 grep {"path": "repos/mifos-web-repo/src/app/loans/create-loans-account/create-loans-a → completed；R37 ls {"path": "repos/mifos-web-repo/src/environments"} → completed；R37 grep {"path": "repos/mifos-web-repo/env.sample", "pattern": "tenant|apiProvider|apiVe → completed；R38 grep {"path": "repos/mifos-web-repo/src/app/settings/settings.service.ts", "pattern": → completed；R38 grep {"path": "repos/mifos-web-repo/src/environments/environment.ts", "pattern": "api → completed；R39 grep {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "pattern": "buil → completed

调查完成。以下结论均基于 mifos-web-repo 源码核实，并对照 fineract-repo 后端与 requirements 需求原文验证。

## 一、总体调用结构

前端所有贷款操作都汇聚到一个服务类 `LoansService`（`repos/mifos-web-repo/src/app/loans/loans.service.ts`），组件通过它发 HTTP 请求，请求 URL 只写相对路径（如 `/loans/xxx`），由拦截器统一补全前缀、租户头和认证头。

## 二、四个业务动作的调用链

### 1. 创建贷款申请
| 步骤 | 前端方法 | 后端接口 |
| --- | --- | --- |
| 进入创建页加载模板 | `LoansService.getLoansAccountTemplateResource()`（loans.service.ts:427，经 `loans-account-template.resolver.ts:24` 和 `loans-account-details-step.component.ts:189` 调用） | `GET /loans/template?clientId|groupId=&templateType=...` |
| 产品切换后取产品模板 | 同上（带 `productId`） | `GET /loans/template` |
| 计算还款计划 | `LoansService.calculateLoanSchedule()`（loans.service.ts:641，经 `loans-account-schedule-step.component.ts:59`） | `POST /loans?command=calculateLoanSchedule` |
| **提交创建** | `LoansService.buildLoanRequestPayload()`（loans.service.ts:653，组装 charges/collateral/disbursementData/日期格式等）→ `LoansService.createLoansAccount()`（loans.service.ts:460，经 `create-loans-account.component.ts:167-183` 调用） | `POST /loans` |

Fineract 侧对应 `LoansApiResource`（`repos/fineract-repo/.../loanaccount/api/LoansApiResource.java:188` `@Path("/v1/loans")`，POST 在 549-577 行）：`command=calculateLoanSchedule` 走试算，否则 `createLoanApplication()` 提交申请。

### 2. 发起审批（Approve）
| 步骤 | 前端方法 | 后端接口 |
| --- | --- | --- |
| 路由预取审批模板 | `LoansService.getLoanApprovalTemplate()`（loans.service.ts:521，经 `loan-action-button.resolver.ts:66`） | `GET /loans/{loanId}/template?templateType=approval&associations=delinquency` |
| 补充分期放款信息 | `getApproveAssociationsDetails()`（loans.service.ts:270 附近）、`getLoanDelinquencyDataForTemplate()`（approve-loan.component.ts:96-105） | `GET /loans/{loanId}?associations=multiDisburseDetails` 等 |
| **提交审批** | `LoansService.loanActionButtons(loanId, 'approve', data)`（loans.service.ts:339，经 `approve-loan.component.ts:128`） | `POST /loans/{loanId}?command=approve` |

Fineract 侧是 `LoansApiResource.java:608-633` 的 `stateTransitions()`，同一端点按 `command` 分派 approve/reject/withdrawnByApplicant/disburse/disbursetosavings/undoapproval 等状态转移（reject、withdraw 等组件用同一方法，见 `reject-loan.component.ts:87`、`withdrawn-by-client.component.ts:90`）。

### 3. 放款（Disburse）
| 步骤 | 前端方法 | 后端接口 |
| --- | --- | --- |
| 预取放款模板 | `LoansService.getLoanActionTemplate(loanId, 'disburse')`（loans.service.ts:30-40，disburse/disbursetosavings 会额外带 `associations=delinquency`；经 `loan-action-button.resolver.ts:62`） | `GET /loans/{loanId}/transactions/template?command=disburse` |
| **提交放款** | `LoansService.loanActionButtons(loanId, 'disburse', data)`（`disburse.component.ts:157`） | `POST /loans/{loanId}?command=disburse` |
| 放款至储蓄账户 | `loanActionButtons(loanId, 'disbursetosavings', data)`（`disburse-to-savings-account.component.ts:108`） | `POST /loans/{loanId}?command=disbursetosavings` |

### 4. 还款（Repayment / Prepay）
| 步骤 | 前端方法 | 后端接口 |
| --- | --- | --- |
| 预取交易模板 | `getLoanActionTemplate(loanId, 'repayment')`（`loan-action-button.resolver.ts:28`） | `GET /loans/{loanId}/transactions/template?command=repayment` |
| **提交还款** | `LoansService.submitLoanActionButton(loanId, data, command)`（loans.service.ts:238，经 `make-repayment.component.ts:75,191`，command 从模板 `type.code` 拆出，如 `repayment`/`goodwillCredit`） | `POST /loans/{loanId}/transactions?command=repayment` |
| 提前结清试算 | `getLoanPrepayLoanActionTemplate()`（loans.service.ts:45，经 `prepay-loan.component.ts:133`） | `GET /loans/{loanId}/transactions/template?command=prepayLoan&transactionDate=...` |
| 提前还款提交 | `submitLoanActionButton(loanId, data, 'repayment')`（prepay-loan.component.ts:179）；合同终止走 `loanActionButtons(loanId, 'contractTermination', data)`（:188） | `POST /loans/{loanId}/transactions?command=repayment`；`POST /loans/{loanId}?command=contractTermination` |

Fineract 侧对应 `LoanTransactionsApiResource`（`repos/fineract-repo/.../api/LoanTransactionsApiResource.java:87` `@Path("/v1/loans")`，POST `{loanId}/transactions` 在 283-302 行），按 `command=repayment/waiveinterest/writeoff/foreclosure/...` 分派。这与需求文档《接口与状态约定》第四节的约定一致：动作经 `?command=` 查询参数传递。

## 三、API 前缀、租户、认证的统一处理

**拦截器链**（`repos/mifos-web-repo/src/app/core/core.module.ts:63-101`）：`AuthenticationInterceptor → ApiPrefixInterceptor → ErrorHandlerInterceptor → CacheInterceptor → ProgressInterceptor`，且 `HttpClient` 被替换为 `HttpService`（`core/http/http.service.ts`）。

1. **API 前缀** — `core/http/api-prefix.interceptor.ts:20-40`：
   - 默认前缀取 `settingsService.serverUrl`；
   - URL 以 `/v1/`、`/v2/` 等版本开头时改用 `baseServerUrl`（不含版本段，由请求路径自带）；
   - 含 `/actuator/` 的请求只用主机地址 `serverHost`。
   - 前缀来源：`settings.service.ts:154-183` — `server`（localStorage `mifosXServerURL` 优先，否则 `environment.baseApiUrl`）+ `apiProvider` + `apiVersion`；`environment.ts:28-29,88` 默认拼出 `{baseApiUrl}/fineract-provider/api/v1`，可通过 `env.sample` 中的 `FINERACT_API_URL / FINERACT_API_PROVIDER / FINERACT_API_VERSION` 覆盖。

2. **租户标识** — `core/authentication/authentication.interceptor.ts:15-18,26-36`：默认头 `Fineract-Platform-TenantId: environment.fineractPlatformTenantId`（environment.ts:17，默认 `'default'`，来自 `FINERACT_PLATFORM_TENANT_IDENTIFIER`）；若用户在界面上切换了租户（localStorage `mifosXTenantIdentifier`，settings.service.ts:224），则以 `settingsService.tenantIdentifier` 覆盖。每个请求都会被 `request.clone({ setHeaders })` 注入。Fineract 后端由 `TenantAwareBasicAuthenticationFilter` 消费该头（`fineract-repo/.../security/filter/TenantAwareBasicAuthenticationFilter.java:71,114-117`：优先取头，缺省回退 `tenantIdentifier` 查询参数，并加载租户到 `ThreadLocalContextUtil`）。

3. **认证** — 同一拦截器维护 `Authorization` 头（authentication.interceptor.ts:38-60）：
   - Basic 模式：登录 `POST /authentication`（authentication.service.ts:189）返回 `base64EncodedAuthenticationKey`，以 `Basic <key>` 形式设置；
   - OAuth2/OIDC 模式（`environment.oauth.enabled`）：用 angular-oauth2-oidc 库走授权码 + PKCE（authentication.service.ts:176 `initCodeFlow`），以 `Bearer <accessToken>` 设置；
   - 另有双因素头 `Fineract-Platform-TFA-Token`（`/twofactor`、`/twofactor/validate`，authentication.service.ts:330-350）。

即：前端四个业务动作本身只发裸相对路径请求，前缀拼接、租户隔离和凭证注入全部由拦截器链统一完成，与需求文档中"API 前缀 `/fineract-provider/api/v1`、租户头 `Fineract-Platform-TenantId`、动作经 `?command=`"的约定完全对应。

**证据缺口说明**：本回答聚焦 Mifos X 前端与 Fineract 原生接口；channel-service 与 loan-middle 各自调用 Fineract 的链路（中台 `FineractClient.java:39` 同样固定发送 `Fineract-Platform-TenantId`）属于系统间契约问题，如需展开可另行核实。

## native-lifecycle

**问题**：在当前项目中，一笔贷款申请从提交、审批到放款，账户状态如何流转？状态定义和流转校验分别在代码哪里实现？请结合 REST 入口说明每次动作是如何触发状态变化的。

| 组 | 重复 | 状态 | 模型 | 关键文件 | 末键轮次 | 末键后工具/搜索 | 模型调用 | 工具调用 | 读取文件 | 搜索 | 候选/未读 | 空搜索 | 无读取搜索 | 重复调用 | Token | 耗时 s | 引用 确认/搜索见/未交付 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| backbone | 1 | completed | zcode/glm-5.3-flash | 4/6 | 102 | 14/7 | 110 | 248 | 33 | 107 | 317/290 | 35 | 16 | 4 | 6628987 | 738.22 | 40/2/0 |
| none | 1 | completed | zcode/glm-5.3-flash | 3/6 | 6 | 72/48 | 52 | 85 | 20 | 53 | 104/86 | 8 | 14 | 0 | 2080351 | 533.74 | 25/8/0 |
| none | 2 | completed | zcode/glm-5.3-flash | 3/6 | 10 | 61/39 | 26 | 114 | 21 | 61 | 107/89 | 16 | 14 | 0 | 1035551 | 339.08 | 39/13/0 |

**评测检查项（仅评测器）**

1. 状态定义在 LoanStatus 枚举（fineract-core 模块），列出 SUBMITTED_AND_PENDING_APPROVAL(100)、APPROVED(200)、ACTIVE(300)、CLOSED_OBLIGATIONS_MET(600) 等关键状态
2. 流转校验在 DefaultLoanLifecycleStateMachine（fineract-loan 模块），按 LoanEvent 事件判定，不是在 REST 层硬编码
3. LOAN_APPROVED 只允许从 SUBMITTED_AND_PENDING_APPROVAL 转入 APPROVED；LOAN_DISBURSED 从 APPROVED（或结清后）转入 ACTIVE
4. 创建走 POST /loans（LoansApiResource），审批/放款走 POST /loans/{loanId}?command=approve|disburse
5. command 经 CommandWrapperBuilder 构造为 entity=LOAN 的命令，按 @CommandType 注解路由到对应 Handler（如 LoanApplicationApprovalCommandHandler）

### backbone / 第 1 次

- 状态：completed；Run：`RUN-7059a6fba38643c1a8291d79c2f25b64`；实际模型：zcode/glm-5.3-flash；Pi：1.5.0
- 未读取的关键文件：repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/provider/CommandHandlerProvider.java、repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/LoanApplicationApprovalCommandHandler.java
- 最后关键文件轮次：102；之后工具 14 次、搜索 7 次、模型调用 8 次
- 读取原文未核实引用 40/45，其中搜索输出可见 2、读取文件但行区间未交付 0、其它未出现 0、无行号 3
- 未读候选（启发式）：repos/loan-middle-repo/src/main/java/com/rsd/middle/MiddleApplication.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/common/BizException.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/common/Ids.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/CreditApplicationRepository.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/CreditController.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/CreditLimit.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/CreditLimitRepository.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/GroupLimit.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/GroupLimitRepository.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/GroupMember.java
- 工具时序：R1 ls {"path": "."} → completed；R1 ls {"path": "knowledge/business-context"} → completed；R2 ls {"path": "repos"} → completed；R2 ls {"path": "requirements"} → completed；R2 read {"path": "knowledge/business-context/terminology.md"} → completed；R3 read {"path": "requirements/接口与状态约定.md"} → completed；R3 read {"path": "requirements/产品规则.md"} → completed；R4 read {"path": "knowledge/business-context/cross-system.md"} → completed；R4 read {"path": "knowledge/business-context/boundaries.md"} → completed；R5 ls {"path": "repos/loan-middle-repo"} → completed；R5 ls {"path": "repos/channel-service-repo"} → completed；R5 read {"path": "requirements/系统职责.md"} → completed；R6 codemode {"code": "const out = [];\nfor (const repo of ['repos/loan-middle-repo', 'repos/ → completed；R6 find {"pattern": "repos/loan-middle-repo/src/**/*.java"} → completed；R6 find {"pattern": "repos/channel-service-repo/src/**/*.java"} → completed；R7 ls {"path": "repos/loan-middle-repo/src"} → completed；R7 ls {"path": "repos/channel-service-repo/src"} → completed；R8 codemode {"code": "const out = [];\nfor (const repo of ['loan-middle-repo', 'channel-serv → error；R8 ls {"path": "repos/loan-middle-repo/src/main"} → completed；R8 ls {"path": "repos/channel-service-repo/src/main"} → completed；R8 ls {"path": "repos/fineract-repo/src/main"} → error；R9 codemode {"code": "const out = [];\nfor (const repo of ['loan-middle-repo', 'channel-serv → completed；R9 ls {"path": "repos/loan-middle-repo/src"} → completed；R9 ls {"path": "repos/channel-service-repo/src"} → completed；R9 ls {"path": "repos/fineract-repo/src"} → error；R9 ls {"path": "repos/external-simulator-repo/src"} → completed；R9 ls {"path": "repos/mifos-web-repo/src"} → completed；R10 codemode {"code": "const out = [];\nfor (const repo of ['loan-middle-repo', 'channel-serv → completed；R10 ls {"path": "repos/loan-middle-repo/src/main"} → completed；R10 ls {"path": "repos/channel-service-repo/src/main"} → completed；R10 ls {"path": "repos/external-simulator-repo/src/main"} → completed；R10 ls {"path": "repos/fineract-repo"} → completed；R11 codemode {"code": "const out = [];\nfor (const repo of ['loan-middle-repo', 'channel-serv → completed；R11 find {"pattern": "repos/loan-middle-repo/src/main/java/**/*.java"} → completed；R11 find {"pattern": "repos/channel-service-repo/src/main/java/**/*.java"} → completed；R11 find {"pattern": "repos/external-simulator-repo/src/main/java/**/*.java"} → completed；R12 find {"pattern": "**/*.java", "path": "repos/loan-middle-repo/src/main/java", "limit" → completed；R13 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownO → completed；R13 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownS → completed；R14 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownC → completed；R14 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/external/ExternalR → completed；R14 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/compensation/Compe → completed；R15 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/fineract/FineractC → completed；R15 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/CreditAppli → completed；R16 find {"limit": 100, "path": "repos/channel-service-repo/src/main/java", "pattern": "* → completed；R17 read {"path": "repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelApplic → completed；R17 read {"path": "repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelServic → completed；R18 read {"path": "repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelContro → completed；R18 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentOrd → completed；R18 codemode {"code": "const out = [];\nout.push(await tools.find({ path: 'repos/fineract-rep → completed；R18 find {"path": "repos/fineract-repo/fineract-loan", "pattern": "**/DefaultLoanLifecycl → completed；R18 find {"path": "repos/fineract-repo/fineract-loan", "pattern": "**/LoanStatus.java"} → completed；R18 find {"path": "repos/fineract-repo/fineract-loan", "pattern": "**/LoanEvent.java"} → completed；R18 grep {"pattern": "APPROVE|DISBURSE", "path": "repos/fineract-repo/fineract-loan/src/m → completed；R19 read {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R19 codemode {"code": "const out = [];\nout.push(await tools.find({ path: 'repos/fineract-rep → completed；R19 find {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R19 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R20 codemode {"code": "const out = [];\nout.push(await tools.grep({ pattern: 'SUBMITTED_AND_P → completed；R20 grep {"pattern": "SUBMITTED_AND_PENDING_APPROVAL\\(100|ACTIVE\\(300|CLOSED_OBLIGATION → completed；R20 find {"path": "repos/fineract-repo", "pattern": "**/LoanStatus.java"} → completed；R21 read {"limit": 60, "offset": 20, "path": "repos/fineract-repo/fineract-core/src/main/ → completed；R21 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentSer → completed；R22 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/CreditServi → completed；R22 codemode {"code": "const out = [];\n// Fineract: disburse 命令的入口与状态前置校验\nout.push(await to → completed；R22 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java", "pattern": "DISB → completed；R22 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R23 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R23 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R24 codemode {"code": "const out = [];\n// 定位 disburse 命令处理与状态校验\nout.push(await tools.grep({ → completed；R24 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java", "pattern": "LOAN_DIS → completed；R25 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R25 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java", "pattern": "publ → completed；R25 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java", "pattern": "validate → completed；R26 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R26 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java", "pattern": "validate → completed；R26 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java", "pattern": "comm → completed；R27 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R27 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R27 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R28 codemode {"code": "const out = [];\nout.push(await tools.find({ path: 'repos/fineract-rep → completed；R28 find {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R28 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R29 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R29 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R29 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java", "pattern": "publ → completed；R30 read {"limit": 75, "offset": 314, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R31 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R31 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R32 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R32 grep {"path": "repos/fineract-repo", "pattern": "boolean canDisburse", "context": 6,  → completed；R33 read {"limit": 30, "offset": 3521, "path": "repos/fineract-repo/fineract-provider/src → completed；R34 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R34 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R35 read {"limit": 70, "offset": 500, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R36 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R36 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java", "pattern": "disb → completed；R36 grep {"path": "repos/fineract-repo/fineract-command/src/main/java", "pattern": "publi → completed；R36 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R37 codemode {"code": "const out = [];\nout.push(await tools.find({ path: 'repos/fineract-rep → completed；R37 find {"path": "repos/fineract-repo", "pattern": "**/main/**/CommandWrapperBuilder.jav → completed；R38 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R38 grep {"path": "repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/co → completed；R39 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R39 grep {"path": "repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/co → completed；R39 read {"path": "repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/co → completed；R40 find {"limit": 50, "path": "repos/external-simulator-repo/src/main/java", "pattern":  → completed；R40 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/channel-serv → completed；R40 grep {"path": "repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelContro → completed；R40 grep {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentCon → completed；R41 read {"path": "repos/external-simulator-repo/src/main/java/com/rsd/simulator/ExtTrans → completed；R42 read {"limit": 70, "offset": 389, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R43 read {"limit": 28, "offset": 360, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R44 read {"limit": 30, "offset": 459, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R45 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R45 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R46 read {"limit": 12, "offset": 480, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R47 read {"limit": 20, "offset": 492, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R48 codemode {"code": "const out = [];\nout.push(await tools.find({ path: 'repos/fineract-rep → completed；R48 find {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R48 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R49 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R49 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R49 find {"path": "repos/fineract-repo/fineract-loan/src/main/java", "pattern": "**/LoanA → completed；R50 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R50 grep {"path": "repos/fineract-repo", "pattern": "LoanEvent.LOAN_CREATED", "context":  → completed；R51 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R51 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R52 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R52 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R52 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R53 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R53 grep {"path": "repos/fineract-repo", "pattern": "LOAN_CREATED", "context": 4, "limit" → completed；R54 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R54 grep {"path": "repos/fineract-repo", "pattern": "LOAN_CREATED", "context": 3, "limit" → completed；R55 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R55 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R55 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R56 read {"limit": 60, "path": "repos/fineract-repo/fineract-loan/src/main/java/org/apach → completed；R57 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R57 grep {"path": "repos/fineract-repo", "pattern": "loanLifecycleStateMachine", "context → completed；R58 codemode {"code": "const out = [];\n// 创建借据时初始状态如何落 100/200\nout.push(await tools.grep({  → completed；R58 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R59 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R59 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R59 find {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R59 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R60 codemode {"code": "const out = [];\nout.push(await tools.find({ path: 'repos/fineract-rep → completed；R60 find {"path": "repos/fineract-repo/fineract-provider/src/main/java", "pattern": "**/L → completed；R60 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java", "pattern": "loan → completed；R61 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R61 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R62 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R62 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R63 read {"limit": 70, "offset": 128, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R64 read {"limit": 18, "offset": 462, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R65 read {"limit": 25, "offset": 440, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R66 read {"limit": 25, "offset": 570, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R67 read {"limit": 18, "offset": 540, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R68 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R68 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R69 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R69 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R70 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R70 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R71 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R71 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R72 codemode {"code": "const out = [];\nout.push(await tools.find({ path: 'repos/fineract-rep → completed；R72 find {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R73 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R73 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R74 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R74 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R75 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R75 grep {"path": "repos/fineract-repo", "pattern": "updateLoanApplicationAttributesForAp → completed；R76 codemode {"code": "const out = [];\n// 谁触发 LOAN_APPROVED transition？搜索 approveLoanApplica → completed；R76 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java", "pattern": "appr → completed；R77 read {"limit": 80, "offset": 557, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R78 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R78 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R78 find {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R79 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R79 grep {"path": "repos/fineract-repo", "pattern": "assembleLoanApproval", "context": 6, → completed；R80 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R80 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R81 read {"limit": 75, "offset": 1502, "path": "repos/fineract-repo/fineract-provider/src → completed；R82 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R82 grep {"path": "repos/fineract-repo", "pattern": "public void validateDisbursement", " → completed；R83 read {"limit": 40, "offset": 123, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R84 read {"limit": 30, "offset": 163, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R85 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R85 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R85 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R86 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R86 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R87 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/mifos-web-re → completed；R87 read {"path": "repos/external-simulator-repo/src/main/java/com/rsd/simulator/Callback → completed；R87 grep {"path": "repos/mifos-web-repo/src/app", "pattern": "command=disburse|disburse\\ → completed；R87 grep {"path": "repos/mifos-web-repo/src/app/loans", "pattern": "APPROVE|DISBURSE", "c → completed；R88 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/mifos-web-re → completed；R88 grep {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-accounts-button-con → completed；R88 grep {"path": "repos/mifos-web-repo/src/app/loans/common-resolvers", "pattern": "comm → completed；R89 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/mifos-web-re → completed；R89 grep {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/dis → completed；R89 grep {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "pattern": "comm → completed；R90 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/mifos-web-re → completed；R90 grep {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "pattern": "comm → completed；R91 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/mifos-web-re → completed；R91 grep {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "pattern": "loan → completed；R92 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/mifos-web-re → completed；R92 grep {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "pattern": "exec → completed；R92 read {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/dis → completed；R93 read {"limit": 45, "offset": 106, "path": "repos/mifos-web-repo/src/app/loans/loans-v → completed；R94 read {"limit": 15, "offset": 151, "path": "repos/mifos-web-repo/src/app/loans/loans-v → completed；R95 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/mifos-web-re → completed；R95 grep {"path": "repos/mifos-web-repo/src/app/loans/loans.service.ts", "pattern": "loan → completed；R96 read {"limit": 12, "offset": 339, "path": "repos/mifos-web-repo/src/app/loans/loans.s → completed；R97 codemode {"code": "const g = async (path, pattern, limit = 30) => await tools.grep({ path → completed；R97 grep {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownO → completed；R97 grep {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownS → completed；R97 grep {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/fineract/FineractC → completed；R97 grep {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/CreditAppli → completed；R97 grep {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/CreditServi → completed；R98 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/loan-middle- → completed；R98 grep {"path": "repos/loan-middle-repo", "pattern": "approve|APPROVE", "context": 2, " → completed；R98 ls {"path": "repos/fineract-repo/custom"} → completed；R98 grep {"path": "repos/fineract-repo/gradle.properties", "pattern": "version", "context → completed；R99 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R99 grep {"path": "repos/fineract-repo/build.gradle", "pattern": "version =|applicationNa → completed；R99 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R100 ls {"path": "repos/fineract-repo/custom/acme"} → completed；R101 codemode {"code": "const out = [];\nout.push(await tools.find({ path: 'repos/fineract-rep → completed；R101 find {"path": "repos/fineract-repo/custom/acme", "pattern": "**/*.java"} → completed；R102 read {"limit": 30, "offset": 1315, "path": "repos/fineract-repo/fineract-provider/src → completed；R103 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R103 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R103 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R104 read {"limit": 28, "offset": 608, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R105 read {"limit": 40, "offset": 54, "path": "repos/loan-middle-repo/src/main/java/com/rs → completed；R106 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R106 grep {"path": "repos/fineract-repo", "pattern": "autoApprove|auto_approve|AUTO_APPROV → completed；R106 grep {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R107 read {"limit": 90, "offset": 488, "path": "repos/fineract-repo/fineract-loan/src/main → completed；R108 codemode {"code": "const out = [];\nout.push(await tools.grep({ path: 'repos/fineract-rep → completed；R108 grep {"path": "repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/po → completed；R108 grep {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R108 grep {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/fineract/FineractC → completed；R109 read {"limit": 10, "offset": 910, "path": "repos/fineract-repo/fineract-loan/src/main → completed

已核实完各层实现。以下按“三层状态体系 → 状态定义 → REST 动作触发的流转 → 流转校验 → 证据缺口”组织回答。

# 一、总体：一层申请，三层账户状态

一笔贷款（提款）的业务状态分散在三层，各层独立流转、独立校验：

| 层 | 状态主体 | 职责 |
| --- | --- | --- |
| 渠道层 | 渠道申请单展示状态 | 只做展示映射，合并中台多个内部状态为 `PROCESSING`，原始状态留底 |
| 中台层 | 提款业务单（含付款指令、入账两个子状态） | 业务编排与**业务终态的唯一裁判**，附额度预占/实占/释放 |
| Fineract 层 | 借据（m_loan） | 贷款账户生命周期（100→200→300→600），非法跳转被状态机拒绝 |

# 二、状态定义在哪里

**中台业务单** —— `repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownOrder.java`
- `:18` 业务单状态 `Status { PROCESSING, EXTERNAL_SUCCESS_BOOKING_PENDING, SUCCESS, FAILED, CANCELLED }`
- `:21` 付款指令状态 `PaymentStatus { TO_SUBMIT, PROCESSING, SUCCESS, FAILED, RESULT_UNCONFIRMED }`
- `:24` 入账状态 `BookingStatus { NONE, TO_BOOK, BOOKED, RETRY_NEEDED }`
- `:47` `terminal()` 终态判定（SUCCESS/FAILED/CANCELLED），是所有幂等分支的依据

**Fineract 借据** —— `repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:27-38`：`100 提交待审批 → 200 审批通过 → 300 生效(ACTIVE) → 600 义务结清 / 601 核销 / 602 重 scheduled / 700 超额`。

**渠道展示状态** —— 没有独立枚举：映射逻辑在 `repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelService.java:186-191` `displayOf()`（SUCCESS/FAILED 直传，其余一律 `PROCESSING`），结果落在 `ChannelApplicationRecord.displayStatus` 与 `middleRawStatus`（`ChannelApplicationRecord.java:17-18`）。

**外部模拟系统** —— `repos/external-simulator-repo/src/main/java/com/rsd/simulator/ExtTransaction.java:23-26`：外部流水 `PROCESSING → SUCCESS|FAILED`，场景枚举 `SYNC_SUCCESS/SYNC_REJECT/ASYNC_SUCCESS_DELAY/ASYNC_FAIL/NO_CALLBACK_NEED_QUERY`。

# 三、状态流转全貌与 REST 动作触发点

```
授信 CR-   PROCESSING ──外部审批回调/查单──▶ APPROVED/REJECTED ──▶ effectuate 建授信/改额度
                                        （冻结/解冻是运营操作，不走审批）

提款 DD-   PROCESSING ──付款成功──▶ EXTERNAL_SUCCESS_BOOKING_PENDING ──Fineract放款成功──▶ SUCCESS
                ├─付款失败──▶ FAILED（释放预占）
                └─入账失败──▶ TO_BOOK/RETRY_NEEDED（补偿任务重试）

Fineract借据  100 ──▶ 200 ──command=disburse──▶ 300(ACTIVE) ──还款/结清判定──▶ 600 或 700
```

## 1. 客户提交：`POST /api/drawdowns?channelId=`（ChannelController.java:28-30）

入口在 `ChannelService.applyDrawdown`（`ChannelService.java:83-99`）：先校验渠道规则（`PRODUCT_NOT_OPEN`/`CHANNEL_AMOUNT_LIMIT`/`CHANNEL_TERM_LIMIT`，`:85-95`），然后**代替客户串联两次中台调用**——先 `POST /middle/api/v1/drawdowns/quote`（生成 `QT-` 报价快照，30 分钟有效，状态 `UNUSED`，`DrawdownService.java:48-72`），再 `POST /middle/api/v1/drawdowns`。

中台 `DrawdownService.apply`（`DrawdownService.java:123-162`）一次性完成状态初始化与预占：
- 建单即 `PROCESSING` + `PaymentStatus.TO_SUBMIT` + `BookingStatus.NONE`（`:145-147`）
- 报价转 `USED`（`:152-153`）
- 逐层预占额度 `limitService.preOccupy`（`:156-157`）
- 调 Fineract `POST /loans` 创建借据（`FineractClient.java:55-73`），借据在 Fineract 侧落成 **100 提交状态**（`Loan.java:560` 构造器硬编码 `SUBMITTED_AND_PENDING_APPROVAL`），`loanAccountNo` 记回业务单（`:158-160`）
- 立即提交付款 `submitPayment`（`:161`，方法体 `:164-181`）：同步 `SUCCESS/REJECTED` 就地结算；受理中保持 `PROCESSING`；提交异常保留 `TO_SUBMIT` 等补偿

渠道侧生成 `CHN-` 单号保存映射，**首次返回固定 `displayStatus=PROCESSING`**（`ChannelService.java:97-99`）。

## 2. 外部结果回调：`POST /middle/api/v1/callbacks/external-result`（ExternalResultController.java:43-70）

- `type=PAYMENT` → `DrawdownService.onPaymentResult`（`DrawdownService.java:186-213`）：
  - 成功：`paymentStatus=SUCCESS` → 业务单进入显式可恢复状态 `EXTERNAL_SUCCESS_BOOKING_PENDING`，`bookingStatus=TO_BOOK`（`:198-200`），随后同步尝试 `book()`；
  - 失败：业务单 `FAILED` 并释放预占 `limitService.releasePreOccupied`（`:206-208`）。
- `type=APPROVAL` → `CreditService.onApprovalResult`（`CreditService.java:104-116`）：授信申请单 `PROCESSING → APPROVED/REJECTED`，`APPROVED` 触发 `effectuate`（`:118-145`）建授信/提额/降额并落 `restricted` 标志。

**关键语义**：回调成功≠业务成功——`EXTERNAL_SUCCESS_BOOKING_PENDING` 就是为“外部已付款、中台未入账”设的显式中间态。

## 3. 中台入账（放款）：`book()` 内调 Fineract `POST /loans/{id}?command=disburse`

`DrawdownService.book`（`DrawdownService.java:216-243`）：
- 幂等前置：`bookingStatus==BOOKED` 直接返回（`:218-220`）；调 Fineract 前先 `isLoanActive` 查借据是否已 300，防重复放款（`:222-227`）
- 执行 `disburseLoan`（`FineractClient.java:89-95`）→ Fineract `LoansApiResource.stateTransitions`（`LoansApiResource.java:608-631`，command 路由 `:1325-1326`）→ `LoanWritePlatformServiceJpaRepositoryImpl.disburseLoan`（`:314` 起）：`canDisburse` 状态机 dryTransition 校验（`:370`，实现 `:3521-3532`）→ 生成放款交易（`:393-399`）→ 重算还款计划（`:556-559`）→ `loanLifecycleStateMachine.transition(LOAN_DISBURSED, loan)`（`:596`）→ 借据 **200→300(ACTIVE)**
- 成功后中台收敛：`bookingStatus=BOOKED`、业务单 `SUCCESS`、逐层预占转实占 `confirmOccupy`（`DrawdownService.java:228-233`）；失败转 `RETRY_NEEDED`（`:236-239`）

状态机的转移校验在 `DefaultLoanLifecycleStateMachine.getNextStatus`（`DefaultLoanLifecycleStateMachine.java:110-216`）：`LOAN_DISBURSED` 只允许从 `200（APPROVED）`或 `600/700` 进入（`:134-139`），非法来源返回 null 即拒绝。

## 4. 补偿查单（定时任务，不是 REST）：CompensationTask.compensate（CompensationTask.java:53-89）

三条线把中间态推向终态：`TO_SUBMIT` 重提付款（`:57-58`）；`PROCESSING` 超时主动查单 `queryAndSettle`（`:60-67`，实现 `DrawdownService.java:246-253`）；`TO_BOOK/RETRY_NEEDED` 重试 `book`（`:69-77`）。查单与回调最终走同一个 `onPaymentResult` 通道，这是“重复回调/重复查单不产生新记录”的实现基础。

## 5. 结果查询：`GET /middle/api/v1/drawdowns/{bizOrderNo}`（DrawdownController.java:71-74）→ 渠道 `GET /api/drawdowns/{channelRefNo}`（ChannelController.java:33-36）

`ChannelService.drawdownDetail`（`ChannelService.java:111-121`）拉取中台原始状态，`displayOf` 映射为展示状态并**同时保存原始状态**（`:116-119`）——查询不触发状态变化，只做视图收敛。

## 6. 还款链（对照）：`POST /middle/api/v1/repayments`（RepaymentController.java:34-38）

`RepaymentService.apply`（`RepaymentService.java:50-70`）建单 `PROCESSING/TO_SUBMIT` → 扣款成功 `onDeductionResult`（`:85-104`）→ `book`（`:107-147`）调 Fineract `POST /loans/{id}/transactions?command=repayment`（`FineractClient.java:129-142`）入账，读取 `principalPortion/interestPortion/feePortion` 分配明细（`FineractClient.java:145-165`），**只按本金部分恢复额度**（`RepaymentService.java:135-141`）。Fineract 端入账后由 `determineAndTransition` 自动判定 300→600（全部结清）或→700（多缴）（`DefaultLoanLifecycleStateMachine.java:76-102` 与 `:283-307`）。

## 7. 运营通道（mifos-web）：借据层的 approve/disburse 按钮

`repos/mifos-web-repo/src/app/loans/loans-view/loan-accounts-button-config.ts:72-75`（Approve）与 `:91-94`（Disburse）按借据当前状态出按钮，最终经 `loans.service.ts` 的 `loanActionButtons`（`:339-342`）发 `POST /loans/{id}?command=approve|disburse`。Fineract 的 approve 命令在 `LoanScheduleAssembler.assembleLoanApproval`（`LoanScheduleAssembler.java:1502-1521`）内执行 `transition(LOAN_APPROVED, loan)`（`:1506`），状态机要求来源必须是 100（`DefaultLoanLifecycleStateMachine.java:124-127`）。

# 四、流转校验的实现位置汇总

| 校验类型 | 位置 |
| --- | --- |
| 渠道规则（金额上限/期限/开放开关） | `ChannelService.applyDrawdown`，`ChannelService.java:85-95` |
| 报价有效期 | `DrawdownService.java:124-127`（`QUOTE_EXPIRED`） |
| 产品金额/期限/还款方式 | `validateAmountAndTerm`，`DrawdownService.java:272-282` |
| 多层额度余量（申请时点重校） | `DrawdownService.java:130-134`（`LIMIT_INSUFFICIENT`） |
| 业务幂等（终态不重入） | `DrawdownOrder.terminal()`（`:47`）+ `onPaymentResult`/`book` 各分支 |
| Fineract 借据状态机 | `DefaultLoanLifecycleStateMachine.getNextStatus`（`:110-216`），disburse 前校验 `canDisburse`（`LoanWritePlatformServiceJpaRepositoryImpl.java:3521-3532`）、参数级校验 `validateDisbursement`（`LoanTransactionValidatorImpl.java:123`，其中“须已审批未放款”在 `:178-186`） |
| 展示状态合并（不丢原始状态） | `ChannelService.displayOf`（`:186-191`）+ `drawdownDetail` 落库（`:111-121`） |

# 五、证据缺口（如实标注，未核实）

1. **借据 100→200 的衔接缺口**：中台 `FineractClient.createLoan` 的 payload（`FineractClient.java:55-73`）只带 `submittedOnDate`，**没有 `approvedOnDate`**；而该类注释（`:21`）声称“创建时直接提交为经审批口径（submittedOnDate+approvedOnDate）”。按 Fineract 源码，创建后借据固定为 100（`Loan.java:560`），`command=disburse` 的校验要求 `loan.isApproved() && loan.isNotDisbursed()`（`LoanTransactionValidatorImpl.java:178-186`），状态机也不允许从 100 直接放款。我在 `loan-middle-repo` 全库未检索到任何调用 Fineract `approve` 命令的代码（`FineractClient` 中无该方法）。即：**中台如何让借据到达 200，在当前源码中没有实现证据**——这是注释与实现之间的不一致，建议作为待确认项。
2. **`CANCELLED` 无入口**：状态枚举与需求契约（“提交前可 CANCELLED”）都定义了该状态，但 loan-middle 源码中没有任何把业务单置为 `CANCELLED` 的端点或写点，当前代码不可达。
3. 需求与代码注释多次引用 `docs/接口契约-阶段2.md`、`docs/三系统边界设计.md`，本轮授权资料根中不含 `docs/` 目录，未能核对这些文档原文。


### none / 第 1 次

- 状态：completed；Run：`RUN-1ee61e6bf02848d0adc04457ed21bb54`；实际模型：zcode/glm-5.3-flash；Pi：1.5.0
- 未读取的关键文件：repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/service/CommandWrapperBuilder.java、repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/provider/CommandHandlerProvider.java、repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/LoanApplicationApprovalCommandHandler.java
- 最后关键文件轮次：6；之后工具 72 次、搜索 48 次、模型调用 46 次
- 读取原文未核实引用 25/35，其中搜索输出可见 8、读取文件但行区间未交付 0、其它未出现 0、无行号 2
- 未读候选（启发式）：repos/fineract-repo/fineract-e2e-tests-core/src/test/java/org/apache/fineract/test/data/LoanStatus.java、repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/group/api/GroupsApiResourceSwagger.java、repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanLifecycleStateMachine.java、repos/fineract-repo/fineract-provider/src/test/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachineTest.java、repos/fineract-repo/fineract-progressive-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/service/CapitalizedIncomeWritePlatformServiceImpl.java、repos/fineract-repo/fineract-progressive-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/starter/ProgressiveLoanAccountConfiguration.java、repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanAccountDomainServiceJpa.java、repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/service/LoanChargeWritePlatformServiceImpl.java、repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/service/adjustment/LoanAdjustmentServiceImpl.java、repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResourceSwagger.java
- 工具时序：R1 ls {"path": "requirements"} → completed；R1 ls {"path": "repos"} → completed；R1 grep {"pattern": "enum LoanStatus|LoanStatus\\s*\\{", "path": "repos/fineract-repo"} → completed；R2 read {"path": "requirements/接口与状态约定.md"} → completed；R2 read {"path": "requirements/产品规则.md"} → completed；R2 read {"path": "repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/po → completed；R3 grep {"path": "repos/fineract-repo", "pattern": "class DefaultLoanLifecycleStateMachi → completed；R3 grep {"path": "repos/fineract-repo", "pattern": "APPLY_FOR_LOAN|loanLifecycleStateMac → completed；R4 read {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R4 read {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R5 grep {"path": "repos/fineract-repo", "pattern": "class LoansApiResource", "glob": "** → completed；R5 grep {"path": "repos/fineract-repo", "pattern": "\"approve\"|\"disburse\"|\"reject\"| → completed；R6 read {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R7 read {"limit": 110, "offset": 1290, "path": "repos/fineract-repo/fineract-provider/sr → completed；R8 ls {"path": "repos/loan-middle-repo"} → completed；R8 grep {"path": "repos/loan-middle-repo", "pattern": "command=disburse|command=approve| → completed；R9 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/fineract/FineractC → completed；R9 find {"path": "repos/loan-middle-repo", "pattern": "**/*.java"} → completed；R10 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownO → completed；R10 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownS → completed；R11 find {"path": "repos/channel-service-repo", "pattern": "**/*.java"} → completed；R11 grep {"path": "repos/channel-service-repo", "pattern": "enum Status|PROCESSING|EXTERN → completed；R12 read {"path": "repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelServic → completed；R12 read {"path": "repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelApplic → completed；R13 read {"limit": 120, "offset": 500, "path": "repos/fineract-repo/fineract-provider/src → completed；R13 grep {"path": "repos/fineract-repo", "pattern": "notInApprovedState|not.in.approved|L → completed；R14 grep {"limit": 30, "path": "repos/fineract-repo", "pattern": "transition\\(LoanEvent\ → completed；R14 read {"limit": 60, "offset": 140, "path": "repos/fineract-repo/fineract-loan/src/main → error；R15 grep {"limit": 20, "path": "repos/fineract-repo", "pattern": "LOAN_CREATED\\)", "glob → completed；R15 find {"path": "repos/fineract-repo", "pattern": "**/LoanTransactionValidatorImpl.java → completed；R16 read {"limit": 80, "offset": 120, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R16 grep {"limit": 15, "path": "repos/fineract-repo", "pattern": "LoanEvent.LOAN_CREATED" → completed；R17 grep {"context": 6, "limit": 30, "path": "repos/fineract-repo", "pattern": "LOAN_CREA → completed；R18 grep {"limit": 25, "path": "repos/loan-middle-repo", "ignoreCase": true, "pattern": " → completed；R18 read {"limit": 80, "offset": 160, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R19 grep {"limit": 20, "path": "repos/fineract-repo", "pattern": "LOAN_CREATED, loan|tran → completed；R19 grep {"limit": 15, "path": "repos/fineract-repo", "pattern": "class LoanAssemblyUtil| → completed；R20 grep {"context": 5, "limit": 30, "path": "repos/fineract-repo", "pattern": "LOAN_CREA → completed；R20 grep {"limit": 20, "path": "repos/fineract-repo", "pattern": "stateMachine|LifecycleS → completed；R21 find {"path": "repos/fineract-repo", "pattern": "**/LoanAssembler.java"} → completed；R21 grep {"limit": 25, "path": "repos/fineract-repo", "pattern": "setLoanStatus|loanStatu → completed；R22 read {"limit": 40, "offset": 545, "path": "repos/fineract-repo/fineract-loan/src/main → completed；R22 grep {"context": 4, "limit": 15, "path": "repos/fineract-repo/fineract-loan/src/main/ → completed；R23 grep {"context": 10, "limit": 40, "path": "repos/fineract-repo/fineract-loan/src/main → completed；R24 grep {"limit": 20, "path": "repos/fineract-repo/fineract-loan/src/main/java/org/apach → completed；R25 grep {"context": 8, "limit": 30, "path": "repos/fineract-repo/fineract-loan/src/main/ → completed；R26 read {"limit": 80, "offset": 80, "path": "repos/fineract-repo/fineract-provider/src/m → completed；R27 grep {"context": 3, "limit": 20, "path": "repos/fineract-repo/fineract-loan/src/main/ → completed；R27 find {"path": "repos/fineract-repo", "pattern": "**/main/**/LoanAssemblerImpl.java"} → completed；R28 grep {"context": 10, "limit": 40, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R29 read {"limit": 100, "offset": 50, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R30 read {"limit": 75, "offset": 150, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R31 read {"limit": 60, "offset": 225, "path": "repos/fineract-repo/fineract-provider/src/ → completed；R32 read {"limit": 60, "offset": 1695, "path": "repos/fineract-repo/fineract-provider/src → completed；R32 grep {"context": 3, "limit": 40, "path": "repos/fineract-repo/fineract-provider/src/m → completed；R33 read {"limit": 55, "offset": 3280, "path": "repos/fineract-repo/fineract-provider/src → completed；R33 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownC → completed；R34 grep {"context": 2, "limit": 40, "path": "repos/channel-service-repo/src/main/java/co → completed；R34 grep {"context": 3, "limit": 30, "path": "repos/loan-middle-repo/src/main/java/com/rs → completed；R34 grep {"context": 3, "limit": 30, "path": "repos/loan-middle-repo/src/main/java/com/rs → completed；R35 read {"limit": 45, "offset": 70, "path": "repos/loan-middle-repo/src/main/java/com/rs → completed；R35 grep {"context": 4, "limit": 30, "path": "repos/loan-middle-repo/src/main/java/com/rs → completed；R36 read {"limit": 65, "offset": 3300, "path": "repos/fineract-repo/fineract-provider/src → completed；R37 grep {"limit": 10, "path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawd → completed；R37 grep {"limit": 10, "path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawd → completed；R37 grep {"limit": 10, "path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawd → completed；R37 grep {"limit": 5, "path": "repos/channel-service-repo/src/main/java/com/rsd/channel/C → completed；R38 grep {"context": 4, "limit": 30, "path": "repos/loan-middle-repo/src/main/java/com/rs → completed；R38 grep {"context": 2, "limit": 10, "path": "repos/fineract-repo/fineract-provider/src/m → completed；R39 read {"limit": 50, "offset": 114, "path": "repos/loan-middle-repo/src/main/java/com/r → completed；R39 grep {"context": 3, "limit": 10, "path": "repos/loan-middle-repo/src/main/java/com/rs → completed；R40 grep {"ignoreCase": true, "limit": 15, "path": "repos/mifos-web-repo", "pattern": "co → completed；R41 grep {"context": 5, "limit": 20, "path": "repos/mifos-web-repo/src/app/loans/loans-vi → completed；R42 grep {"context": 3, "limit": 20, "path": "repos/mifos-web-repo/src/app/loans/loans.se → completed；R43 grep {"context": 6, "limit": 15, "path": "repos/mifos-web-repo/src/app/loans/loans.se → completed；R44 grep {"context": 6, "limit": 30, "path": "repos/mifos-web-repo/src/app/loans/loans.se → completed；R45 grep {"context": 8, "limit": 25, "path": "repos/mifos-web-repo/src/app/loans/loans.se → completed；R45 grep {"context": 10, "limit": 20, "path": "repos/mifos-web-repo/src/app/loans/loans-v → completed；R46 read {"limit": 45, "offset": 110, "path": "repos/mifos-web-repo/src/app/loans/loans-v → completed；R46 grep {"context": 5, "limit": 15, "path": "repos/mifos-web-repo/src/app/loans/loans.se → completed；R47 grep {"context": 6, "limit": 15, "path": "repos/mifos-web-repo/src/app/loans/loans.se → completed；R48 grep {"context": 3, "limit": 10, "path": "repos/mifos-web-repo/src/app/loans/loans-vi → completed；R49 find {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions", " → completed；R50 ls {"path": "repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions"} → completed；R51 grep {"context": 4, "limit": 12, "path": "repos/mifos-web-repo/src/app/loans/loans-vi → completed

基于对需求文档和四个仓库源码的核实，这笔贷款的状态流转全景如下。

## 一、总体结构：三层状态模型

一笔贷款从提交到放款，实际由三层状态协同描述，各自有独立的定义和校验代码：

| 层 | 载体 | 状态定义位置 |
| --- | --- | --- |
| 账户层（Fineract 借据） | Loan 实体 | `repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:24` |
| 业务单层（中台提款单/还款单） | DrawdownOrder / RepaymentOrder | `repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownOrder.java:18-25` |
| 展示层（渠道申请单） | ChannelApplicationRecord | `repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelApplicationRecord.java:15-24` |

## 二、Fineract 借据状态定义与状态机（账户层）

**状态定义**：`LoanStatus.java:24-38` 定义了 100=SUBMITTED_AND_PENDING_APPROVAL（提交待审批）、200=APPROVED（审批通过）、300=ACTIVE（生效/已放款）、400=撤回、500=拒绝、600=义务履行结清、601=核销结清、602=重组结清、700=超额还款（OVERPAID）。

**流转校验（状态机）**：`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java`
- 入口方法：`transition()`（:63，先更新汇总再迁移）、`determineAndTransition()`（:71，根据余额自动判定）、`dryTransition()`（:58，试算不落库）。
- 迁移规则在 `getNextStatus()`（:110 起）逐一校验来源状态，例如 LOAN_APPROVED 仅允许来自 100（:121-124）；LOAN_DISBURSED 仅允许来自 200/600（:133-136）；REPAID_IN_FULL 仅允许来自 300/700（:160-163）。非法来源时 `newState=null`，`internalTransition()`（:81）不执行任何变更——这就是"非法跳转被拒绝"的实现。同一文件 :227 起的 `determineTransition*` 系列负责还款后自动判定结清（600）/超额（700）。
- 事件枚举 `LoanEvent.java:24`（LOAN_CREATED、LOAN_APPROVED、LOAN_DISBURSED、REPAID_IN_FULL 等）。

**初始状态**：借据创建即固定为 100，`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/Loan.java:560`（`this.loanStatus = LoanStatus.SUBMITTED_AND_PENDING_APPROVAL`）。

## 三、REST 入口 → 动作 → 状态变化的触发链

**1. 创建借据（提交，100）**
- 入口：`POST /fineract-provider/api/v1/loans`（`LoansApiResource`，命令通过 `?command=` 分发）。
- 链路：`LoansApiResource` → `LoanApplicationWritePlatformServiceJpaRepositoryImpl.submitApplication()`（:132 起，调用 `loanAssembler.assembleFrom(command)`）→ Loan 构造置 100（`Loan.java:560`），发布 LoanCreatedBusinessEvent。

**2. 审批（100→200）**
- 入口：`POST /loans/{loanId}?command=approve`。REST 分发在 `repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:1319-1357`（`stateTransitions` 私有方法按 command 参数构造 CommandWrapper），公共端点声明在 :627。
- 链路：命令经 `commandsSourceWritePlatformService.logCommandSource()` 到审批服务，最终在 `LoanScheduleAssembler.java:1507` 触发 `loanLifecycleStateMachine.transition(LoanEvent.LOAN_APPROVED, loan)`——状态机校验当前为 100 才置 200。
- mifos-web 前端对应：`repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/approve-loan/approve-loan.component.ts:127` 调 `loanActionButtons(loanId, 'approve', data)`，该方法在 `repos/mifos-web-repo/src/app/loans/loans.service.ts:339-342` 发出 `POST /loans/{loanId}?command=approve`。

**3. 放款（200→300）**
- 入口：`POST /loans/{loanId}?command=disburse`（同一 `stateTransitions` 入口，`LoansApiResource.java:1325`）。
- 前置校验：`repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/serialization/LoanTransactionValidatorImpl.java:176-181`——单笔借据必须"已审批且未放款"（`loan.isApproved() && loan.isNotDisbursed()`，其中 `isApproved()` 按状态 200 判定，`Loan.java:906-908`），否则报错拒绝。
- 状态变化：`LoanWritePlatformServiceJpaRepositoryImpl.disburseLoan()`（:537 起，重算还款计划、生成放款交易）末尾 :596 `transition(LoanEvent.LOAN_DISBURSED, loan)` → 状态机校验来源为 200 后置 300。前端对应 `disburse.component.ts:157`。

**4. 还款与结清（300→600/700）**
- 入口：`POST /loans/{loanId}/transactions?command=repayment`（`LoanTransactionsApiResource.java:566`）。
- 状态变化：`LoanWritePlatformServiceJpaRepositoryImpl.java:1733-1735`——若超额转 OVERPAID，否则 LOAN_REPAYMENT_OR_WAIVER；:3311-3313 当还清全部欠款时 `transition(LoanEvent.REPAID_IN_FULL, loan)` 置 600。注意：结清**不是独立命令**，而是还款入账后按余额自动判定。

## 四、中台业务单状态流转（业务层）——"提交到放款"的实际编排

**提款单**状态定义在 `DrawdownOrder.java:18-25`：业务状态 `PROCESSING → EXTERNAL_SUCCESS_BOOKING_PENDING → SUCCESS | FAILED`（提交前可 CANCELLED），外加付款指令状态（TO_SUBMIT/PROCESSING/SUCCESS/FAILED/RESULT_UNCONFIRMED）和入账状态（NONE/TO_BOOK/BOOKED/RETRY_NEEDED）三轨并行。编排逻辑在 `DrawdownService.java`：

1. **申请受理**：渠道 `POST /middle/api/v1/drawdowns`（`DrawdownController.java:66`）→ `DrawdownService.apply()`（:123）——校验报价有效性与额度、逐层预占 → `fineract.createLoan()`（`FineractClient.java:73`，触发 Fineract 状态 100）→ 提交付款。单据此时为 PROCESSING。
2. **外部结果确认**：回调 `POST /middle/api/v1/callbacks/external-result`（`ExternalResultController.java:55-64`）与补偿查单（`CompensationTask.java:68`）走同一入口 `onPaymentResult()`（`DrawdownService.java:189`）——付款 SUCCESS 则置 EXTERNAL_SUCCESS_BOOKING_PENDING + TO_BOOK 并就地尝试入账；FAILED 则释放预占、置 FAILED。
3. **入账（真正的"放款"）**：`book()`（`DrawdownService.java:218`）——先 `isLoanActive()` 查借据状态防重，未生效才调 `fineract.disburseLoan()`（`FineractClient.java:90`，即 Fineract 的 `command=disburse`，驱动借据 200→300），随后逐层预占转实占、单据置 SUCCESS。失败转 RETRY_NEEDED，由补偿任务重试（`CompensationTask.java:74-80`）。这就是需求"外部付款成功≠放款完成"的实现：Fineract 借据 ACTIVE + 实占完成才算 SUCCESS。
4. **还款单**：`RepaymentService.apply()`（:56，PROCESSING）→ 外部扣款 → `book()`（:114，`fineract.repayLoan()` 触发 Fineract 还款交易与结清判定，读取本金/利息/费用分配明细并按本金恢复额度）→ SUCCESS；扣款失败则 FAILED、无额度变化。

## 五、渠道展示状态（展示层）

渠道库不定义新状态机，只做映射：`ChannelService.displayOf()`（`repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelService.java:195-203`）把中台 PROCESSING/EXTERNAL_SUCCESS_BOOKING_PENDING 统一对外展示为 `PROCESSING`，SUCCESS/FAILED 直传；原始状态保存在 `ChannelApplicationRecord.middleRawStatus`（`ChannelApplicationRecord.java:22-23`）。每次查询 `GET /api/drawdowns/{channelRefNo}`（`ChannelController.java:33`）都会刷新该映射。

## 六、完整时序（一笔贷款从提交到放款）

```
客户提交  POST /api/drawdowns (channel) → 中台 apply → 预占额度 + Fineract POST /loans (借据=100) → 提款单 PROCESSING
外部付款  成功回调/查单 → 提款单 EXTERNAL_SUCCESS_BOOKING_PENDING + TO_BOOK
中台入账  book() → Fineract command=disburse (借据 200→300) + 预占转实占 → 提款单 SUCCESS
后续还款  中台 apply → 外部扣款 → Fineract command=repayment → 借据 300→600(结清)/700(超额) → 额度按本金恢复
```

## 七、一个需要指出的实现疑点（证据缺口）

`FineractClient.java:19-21` 注释声称"创建时直接提交为经审批口径（submittedOnDate+approvedOnDate），无需单独审批命令"，但：

1. `createLoan()` 的实际 payload（`FineractClient.java:73-84`）**并不包含 `approvedOnDate`**，只有 `submittedOnDate`；
2. Fineract 创建链路（`Loan.java:560`、`LoanAssemblerImpl`）不读取该字段，借据固定落在 100；
3. 中台全库检索不到任何 `command=approve` 调用，`book()` 只调 `command=disburse`。

而 Fineract 放款前置校验（`LoanTransactionValidatorImpl.java:176-181`）要求借据处于 200 才允许 disburse。按当前源码逻辑推断，中台触发的放款会被 Fineract 以"not in approved state"拒绝，入账将永远停留在 RETRY_NEEDED。授权范围内没有找到 Fineract 侧的定制补丁或产品级跳过审批的配置证据，**中台与 Fineract 之间是否缺少审批命令调用，属于未核实的疑点**，建议进一步确认（例如 Fineract 产品配置是否允许免审批放款，或中台是否应补调 `command=approve`）。


### none / 第 2 次

- 状态：completed；Run：`RUN-9ea4a2a780924e4bba56e259cb0e1423`；实际模型：zcode/glm-5.3-flash；Pi：1.5.0
- 未读取的关键文件：repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/service/CommandWrapperBuilder.java、repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/provider/CommandHandlerProvider.java、repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/LoanApplicationApprovalCommandHandler.java
- 最后关键文件轮次：10；之后工具 61 次、搜索 39 次、模型调用 16 次
- 读取原文未核实引用 39/54，其中搜索输出可见 13、读取文件但行区间未交付 0、其它未出现 0、无行号 2
- 未读候选（启发式）：repos/fineract-repo/fineract-e2e-tests-core/src/test/java/org/apache/fineract/test/data/LoanStatus.java、repos/fineract-repo/fineract-provider/src/test/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachineTest.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/CreditController.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentController.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/fineract/FineractProperties.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownOrderRepository.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownQuote.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownQuoteRepository.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentOrderRepository.java、repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/CreditApplicationOrder.java
- 工具时序：R1 codemode {"code": "const dirs = ['requirements', 'repos/channel-service-repo', 'repos/ext → completed；R1 ls {"path": "requirements", "limit": 60} → completed；R1 ls {"path": "repos/channel-service-repo", "limit": 60} → completed；R1 ls {"path": "repos/external-simulator-repo", "limit": 60} → completed；R1 ls {"path": "repos/fineract-repo", "limit": 60} → completed；R1 ls {"path": "repos/loan-middle-repo", "limit": 60} → completed；R1 ls {"path": "repos/mifos-web-repo", "limit": 60} → completed；R2 read {"path": "requirements/接口与状态约定.md"} → completed；R2 read {"path": "requirements/产品规则.md"} → completed；R2 read {"path": "requirements/系统职责.md"} → completed；R3 codemode {"code": "const jobs = [\n  ['LoanStatus enum', () => tools.grep({pattern: 'enum → completed；R3 grep {"pattern": "enum LoanStatus", "path": "repos/fineract-repo", "glob": "*.java",  → completed；R3 grep {"pattern": "class DefaultLoanLifecycleStateMachine", "path": "repos/fineract-re → completed；R3 grep {"pattern": "enum LoanEvent", "path": "repos/fineract-repo", "glob": "*.java", " → completed；R3 find {"pattern": "**/LoanApiResource.java", "path": "repos/fineract-repo", "limit": 1 → completed；R3 find {"pattern": "**/fineract-adapter/**/*.java", "path": "repos/loan-middle-repo", " → completed；R3 find {"pattern": "**/*Controller.java", "path": "repos/channel-service-repo", "limit" → completed；R3 find {"pattern": "**/*Controller.java", "path": "repos/loan-middle-repo", "limit": 30 → completed；R4 codemode {"code": "const jobs = [\n  ['fineract loan api resources', () => tools.find({pa → completed；R4 find {"pattern": "**/LoanApiResource*.java", "path": "repos/fineract-repo", "limit":  → completed；R4 ls {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle", "limit": 100} → completed；R4 ls {"path": "repos/channel-service-repo/src/main/java/com/rsd/channel", "limit": 10 → completed；R4 read {"path": "repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/po → completed；R4 read {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R5 codemode {"code": "const jobs = [\n  ['fineract dir in middle', () => tools.find({pattern → completed；R5 find {"pattern": "**/*.java", "path": "repos/loan-middle-repo/src/main/java/com/rsd/m → completed；R5 find {"pattern": "**/*.java", "path": "repos/loan-middle-repo/src/main/java/com/rsd/m → completed；R5 find {"pattern": "**/*.java", "path": "repos/loan-middle-repo/src/main/java/com/rsd/m → completed；R5 find {"pattern": "**/*.java", "path": "repos/loan-middle-repo/src/main/java/com/rsd/m → completed；R5 find {"pattern": "**/*.java", "path": "repos/loan-middle-repo/src/main/java/com/rsd/m → completed；R5 find {"pattern": "**/*.java", "path": "repos/loan-middle-repo/src/main/java/com/rsd/m → completed；R5 grep {"pattern": "loanLifecycleStateMachine|LoanLifecycleStateMachine", "path": "repo → completed；R6 codemode {"code": "const jobs = [\n  ['loan api resource', () => tools.grep({pattern: '@R → completed；R6 grep {"pattern": "@RequestMapping.*loans|/loans", "path": "repos/fineract-repo", "glo → completed；R6 ls {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R6 grep {"pattern": "LOAN_DISBURSED|LoanEvent.LOAN_DISBURSED", "path": "repos/fineract-r → completed；R6 grep {"pattern": "REPAID_IN_FULL|determineAndTransition", "path": "repos/fineract-rep → completed；R7 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownS → completed；R7 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/fineract/FineractC → completed；R8 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownO → completed；R8 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownC → completed；R8 read {"path": "repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelServic → completed；R9 codemode {"code": "const jobs = [\n  ['LoansApiResource file', () => tools.find({pattern: → completed；R9 find {"pattern": "**/LoansApiResource.java", "path": "repos/fineract-repo", "limit":  → completed；R9 find {"pattern": "**/LoanTransactionsApiResource.java", "path": "repos/fineract-repo" → completed；R9 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentSer → completed；R9 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentOrd → completed；R9 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/external/ExternalR → completed；R10 codemode {"code": "const jobs = [\n  ['LoansApiResource head', () => tools.read({path: 'r → completed；R10 read {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R10 grep {"pattern": "disburse|LOAN_APPROVED|approve", "path": "repos/fineract-repo/finer → completed；R10 grep {"pattern": "LoanLifecycleStateMachine|getStatic approvedTransition|loanLifecycl → completed；R10 read {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R11 codemode {"code": "const jobs = [\n  ['@Path line', () => tools.grep({pattern: '@Path\\\\ → completed；R11 grep {"pattern": "@Path\\(\"/v1/loans\"\\)", "path": "repos/fineract-repo/fineract-pr → completed；R11 grep {"pattern": "\"approve\"|\"disburse\"|isDisburse|action.equals|CommandWrapperBui → completed；R11 grep {"pattern": "LOAN_APPROVED|approveLoanApplication", "path": "repos/fineract-repo → completed；R11 read {"path": "repos/fineract-repo/fineract-provider/src/main/java/org/apache/finerac → completed；R11 read {"path": "repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelContro → completed；R12 codemode {"code": "const jobs = [\n  ['state machine created in assembler', () => tools.g → completed；R12 grep {"pattern": "getStatic|new DefaultLoanLifecycleStateMachine|loanLifecycleStateMa → completed；R12 grep {"pattern": "approveLoanApplication|approveApplication", "path": "repos/fineract → completed；R12 grep {"pattern": "DISBURSE_LOAN|APPROVE_LOAN|LOAN_DISBURSAL", "path": "repos/fineract → completed；R12 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/compensation/Compe → completed；R13 codemode {"code": "const jobs = [\n  ['assembler wiring', () => tools.grep({pattern: 'Loa → completed；R13 grep {"pattern": "LoanLifecycleStateMachine|getStatic|from(LoanStatus", "path": "repo → completed；R13 grep {"pattern": "assembleLoanApproval|LOAN_APPROVED", "path": "repos/fineract-repo/f → completed；R13 grep {"pattern": "LOAN_CREATED|loanLifecycleStateMachine.transition", "path": "repos/ → completed；R13 read {"path": "repos/loan-middle-repo/src/main/java/com/rsd/middle/external/Simulator → completed；R14 codemode {"code": "const jobs = [\n  ['assembler approval', () => tools.grep({pattern: 'a → completed；R14 grep {"pattern": "assembleLoanApproval|LOAN_APPROVED|loanLifecycleStateMachine", "pat → completed；R14 read {"path": "repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/po → completed；R14 find {"pattern": "**/*.java", "path": "repos/external-simulator-repo/src/main/java",  → completed；R15 read {"limit": 100, "offset": 1, "path": "repos/loan-middle-repo/src/main/java/com/rs → completed；R16 codemode {"code": "const jobs = [\n  ['simulator endpoints', () => tools.grep({pattern: ' → completed；R16 grep {"pattern": "RequestMapping|PostMapping|GetMapping|PutMapping", "path": "repos/e → completed；R16 grep {"pattern": "command=disburse|disburse|approve", "path": "repos/mifos-web-repo/s → completed；R17 codemode {"code": "const jobs = [\n  ['disburseLoan method start', () => tools.grep({patt → completed；R17 grep {"pattern": "public CommandProcessingResult disburseLoan|InvalidLoanStateTransit → completed；R17 grep {"pattern": "approvedOnDate|approveApplication", "path": "repos/fineract-repo/fi → completed；R18 codemode {"code": "const jobs = [\n  ['disburseLoan guard', () => tools.grep({literal: 'I → completed；R18 grep {"literal": "InvalidLoanStateTransitionException", "path": "repos/fineract-repo/ → error；R18 grep {"literal": "LOAN_CREATED", "path": "repos/fineract-repo/fineract-provider/src/m → error；R19 codemode {"code": "const jobs = [\n  ['disburseLoan guard', () => tools.grep({pattern: 'I → completed；R19 grep {"pattern": "InvalidLoanStateTransitionException", "path": "repos/fineract-repo/ → completed；R19 grep {"pattern": "LOAN_CREATED", "path": "repos/fineract-repo/fineract-provider/src/m → completed；R20 codemode {"code": "const jobs = [\n  ['LOAN_CREATED usage', () => tools.grep({pattern: 'L → completed；R20 grep {"pattern": "LOAN_CREATED", "path": "repos/fineract-repo/fineract-provider/src/m → completed；R20 grep {"pattern": "LoanStatus.SUBMITTED_AND_PENDING_APPROVAL", "path": "repos/fineract → completed；R21 codemode {"code": "const jobs = [\n  ['LOAN_CREATED anywhere', () => tools.grep({pattern: → completed；R21 grep {"pattern": "LOAN_CREATED", "path": "repos/fineract-repo", "glob": "**/main/**/* → completed；R21 grep {"pattern": "LoanStatus.SUBMITTED_AND_PENDING_APPROVAL", "path": "repos/fineract → completed；R22 codemode {"code": "const jobs = [\n  ['DrawdownService key lines', () => tools.grep({patt → completed；R22 grep {"pattern": "public DrawdownOrder apply|public void submitPayment|public void on → completed；R22 grep {"pattern": "public RepaymentOrder apply|public void submitDeduction|public void → completed；R22 grep {"pattern": "public BigDecimal availableForDrawdown|public void preOccupy|public → completed；R22 grep {"pattern": "public String createLoan|public boolean isLoanActive|public void di → completed；R22 grep {"pattern": "RequestMapping|PostMapping|GetMapping", "path": "repos/loan-middle- → completed；R22 grep {"pattern": "RequestMapping|PostMapping|case \"PAYMENT\"|case \"DEDUCTION\"", "p → completed；R22 grep {"pattern": "public Map<String, Object> applyDrawdown|private String displayOf|d → completed；R22 grep {"pattern": "public void compensate|Scheduled", "path": "repos/loan-middle-repo/ → completed；R23 codemode {"code": "const jobs = [\n  ['DrawdownService lines', () => tools.grep({pattern: → completed；R23 grep {"pattern": "public DrawdownOrder apply|public void submitPayment|public void on → completed；R23 grep {"pattern": "public RepaymentOrder apply|public void submitDeduction|public void → completed；R24 codemode {"code": "const jobs = [\n  ['DrawdownService lines', () => tools.grep({pattern: → completed；R24 grep {"pattern": "public DrawdownOrder apply|public void submitPayment|public void on → completed；R24 grep {"pattern": "public RepaymentOrder apply|public void submitDeduction|public void → completed；R25 codemode {"code": "const jobs = [\n  ['ChannelController endpoints', () => tools.grep({pa → completed；R25 grep {"pattern": "@RequestMapping|@PostMapping|@GetMapping|public Map<String, Object> → completed；R25 grep {"pattern": "@RequestMapping|@PostMapping|@GetMapping|public Map<String, Object> → completed；R25 grep {"pattern": "public void onApprovalResult|public CreditApplicationOrder apply|se → completed；R25 grep {"pattern": "enum Status|enum ApprovalStatus", "path": "repos/loan-middle-repo/s → completed；R25 grep {"pattern": "审批命令本设计不需要|创建时直接提交为经审批口径", "path": "repos/loan-middle-repo/src/main → completed；R25 grep {"pattern": "@Path|command=repayment|\"repayment\"", "path": "repos/fineract-rep → completed

# 贷款账户状态流转全景

本项目里“一笔贷款”的状态分**两层**：融商贷业务层（渠道申请单 → 中台提款/还款业务单）和 Fineract 借据层（`m_loan` 账户）。中台是业务终态的唯一裁判，Fineract 借据状态只反映借据本体生命周期。

```
客户前端            渠道 channel-service         中台 loan-middle                  外部模拟器            Fineract
   │ POST /api/drawdowns │                        │                                │                   │
   ├───────────────────►│ 渠道规则校验+映射        │                                │                   │
   │                    ├──POST /middle/api/v1/drawdowns──►│                       │                   │
   │                    │  displayStatus=PROCESSING│ 额度校验→逐层预占→创建借据──────┼──────────────────►│ 100 提交
   │                    │                         │ 提交付款 POST /sim/api/v1/payments─►│                   │
   │                    │                         │ ◄──回调 /callbacks/external-result──┤ 付款成功           │
   │                    │                         │ EXTERNAL_SUCCESS_BOOKING_PENDING │                   │
   │                    │                         │ 入账 POST /loans/{id}?command=disburse──────────────►│ 300 生效
   │                    │ ◄──GET 查询(展示状态合并)─│ SUCCESS + 逐层实占               │                   │
```

## 一、状态定义在代码哪里

**中台提款业务单（三组并行状态）** — `repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownOrder.java`
- `Status`: `PROCESSING / EXTERNAL_SUCCESS_BOOKING_PENDING / SUCCESS / FAILED / CANCELLED`（`DrawdownOrder.java:19`，终态判定 `terminal()` 在 `:35`）
- `PaymentStatus`（外部付款指令）: `TO_SUBMIT / PROCESSING / SUCCESS / FAILED / RESULT_UNCONFIRMED`（`:23`）
- `BookingStatus`（中台入账）: `NONE / TO_BOOK / BOOKED / RETRY_NEEDED`（`:26`）

**中台还款订单** — `repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentOrder.java:13-17`：`Status = PROCESSING/SUCCESS/FAILED`，`DeductionStatus = TO_SUBMIT/PROCESSING/SUCCESS/FAILED/RESULT_UNCONFIRMED`。

**中台授信申请** — `CreditApplicationOrder.java:17`：`PROCESSING / APPROVED / REJECTED`。

**渠道展示状态（无枚举，映射规则）** — `repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelService.java:198-203`：中台 `SUCCESS/FAILED` 直传，其余（含 `EXTERNAL_SUCCESS_BOOKING_PENDING`）统一展示 `PROCESSING`，原始状态保留在申请单 `middleRawStatus`（`drawdownDetail`，`ChannelService.java:127-137`）。

**Fineract 借据状态** — `repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:24`：`100 提交待审批 / 200 审批通过 / 300 生效(ACTIVE) / 400 客户撤销 / 500 拒绝 / 600 义务履行结清 / 601 核销 / 602 重置结清 / 700 超额(OVERPAID)`。事件枚举在同模块 `domain/LoanEvent.java:24`（`LOAN_CREATED/LOAN_APPROVED/LOAN_DISBURSED/REPAID_IN_FULL/...`）。

## 二、流转校验在代码哪里

**Fineract 层（唯一的状态机）** — `repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java:36`
- 转移合法性集中在 `getNextStatus()`（`:111` 起）：`LOAN_APPROVED` 仅允许从 **100→200**（`:119-123`）；`LOAN_DISBURSED` 仅允许从 **200→300**（`:134-139`）；`REPAID_IN_FULL` 仅允许 **300/700→600**（`:161-164`）；`LOAN_REJECTED` 仅 **100→500**。`getNextStatus` 返回 null 时 `internalTransition`（`:79`）不落任何变更——非法跳转即被拒绝。
- 结清/超额不由命令触发，而是每次入账后 `determineAndTransition()`（`:55`）按余额自动判定：全部义务履行完→600，多缴→700（`determineTransitionFromActive`，`:352-366`）。
- 动作前的预检（dry-run）在 `LoanApplicationTransitionValidator.java:155`（拒绝）与 `:216`（撤销），用 `dryTransition` 校验当前状态是否允许该事件。
- 业务服务里的触发点：审批 `LoanScheduleAssembler.java:1507`（`transition(LOAN_APPROVED)`）、放款 `LoanWritePlatformServiceJpaRepositoryImpl.java:596`（`transition(LOAN_DISBURSED)`）、还款后自动判定 `LoanAccountDomainServiceJpa.java:877`。

**中台层（业务单状态守卫，内联在各编排方法）** — `repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownService.java`
- 申请校验：报价过期 `:125-128`、按版本快照校验金额/期限 `:129-130`、申请时点多层额度重校验 `:131-134`（额度口径在 `LimitService.availableForDrawdown`，`LimitService.java:74`，冻结为 0、四层取最小）。
- 结果确认幂等：终态直接忽略 `:188-190`；付款成功重复回调 `:197-199`。
- 入账幂等：已 `BOOKED` 跳过 `:219-221`；入账前先查借据是否已 ACTIVE，防止重复放款 `:223-225`。
- 还款侧对应守卫在 `RepaymentService.java:92-95`（终态幂等）、`:114-117`（成功幂等）。

**渠道层（不做业务状态校验）** — 只做渠道开放规则（`ChannelService.java:77-91`）与展示映射，业务状态合法性完全由中台裁决。

## 三、每次 REST 动作如何触发状态变化

**① 提交（申请）**
1. `POST /api/drawdowns?channelId=`（`ChannelController.java:28`）→ 渠道校验开放规则后，内部先 `quote` 再 `applyDrawdown` 调中台（`ChannelService.java:96-104`），落渠道申请单 `displayStatus=PROCESSING`。
2. `POST /middle/api/v1/drawdowns`（`DrawdownController.java:66`）→ `DrawdownService.apply`（`DrawdownService.java:123`）：校验通过后业务单置 **PROCESSING + TO_SUBMIT + NONE**（`:145-147`）→ `limitService.preOccupy` 逐层预占（`:156`，`LimitService.java:100`）→ `fineract.createLoan` 经 `POST /fineract-provider/api/v1/loans` 创建借据（`FineractClient.java:55-80`；借据初始为 **100 提交态**，`Loan.java:560`）→ `submitPayment` 调 `POST /sim/api/v1/payments`（`SimulatorController.java:35`）。同步成功就地进入结果确认，受理中则停在 PROCESSING 等回调/查单（`DrawdownService.java:166-176`）。

**② 外部付款结果（异步，两条收敛通道）**
- 回调：模拟器 `POST /middle/api/v1/callbacks/external-result`（`ExternalResultController.java:55`）→ `case "PAYMENT"`（`:60`）→ `onPaymentResult`（`DrawdownService.java:189`）：成功则付款态=SUCCESS、业务单 **PROCESSING → EXTERNAL_SUCCESS_BOOKING_PENDING**、入账态置 TO_BOOK（`:198-200`）并同步尝试入账；失败则 **→FAILED** 并 `releasePreOccupied` 逐层释放预占（`:206-208`，`LimitService.java:163`）。
- 查单：回调未送达时由补偿任务对超时 PROCESSING 主动 `GET /sim/api/v1/payments/{extSerialNo}`（`CompensationTask.java:64-71` → `queryAndSettle`，`DrawdownService.java:246`），结果走与回调同一处理函数。
- 提交失败（TO_SUBMIT）由补偿任务重提（`CompensationTask.java:61-62`）。

**③ 放款（入账）**
`book()`（`DrawdownService.java:218`）调用 `POST /loans/{id}?command=disburse`（`FineractClient.java:89-97`）→ Fineract `LoansApiResource`（`@Path("/v1/loans")`，`LoansApiResource.java:188`）经命令总线走到 `LoanWritePlatformServiceJpaRepositoryImpl.java:596` 执行 `transition(LOAN_DISBURSED)`，状态机校验 **200→300**，借据变 **ACTIVE(300)**。成功后业务单 **EXTERNAL_SUCCESS_BOOKING_PENDING → SUCCESS**、入账态=BOOKED、`confirmOccupy` 预占转实占（`DrawdownService.java:228-233`，`LimitService.java:131`）；失败置 RETRY_NEEDED 由补偿任务重试（`:236-238`、`CompensationTask.java:73-86`）。

**④ 审批（借据层命令，本项目链路通常不单独调用）**
Fineract 原生入口是 `POST /loans/{id}?command=approve` → `approveApplication`（`LoanApplicationWritePlatformServiceJpaRepositoryImpl.java:557`）→ `transition(LOAN_APPROVED)`（`LoanScheduleAssembler.java:1507`），即 **100→200**。但融商贷链路中“审批”发生在中台（授信申请走外部模拟审批，`CreditService.java:104-115` 按回调结果置 APPROVED/REJECTED），`FineractClient.java:20-21` 注释明确“审批命令本设计不需要单独调用，创建时直接提交为经审批口径”。

**⑤ 还款与结清**
- `POST /api/repayments`（渠道，`ChannelController.java:38`）→ `POST /middle/api/v1/repayments`（`RepaymentController.java:34`）→ 订单 **PROCESSING/TO_SUBMIT** → `POST /sim/api/v1/deductions` 扣款（`RepaymentService.java:74-83`）→ 回调/查单确认（`onDeductionResult`，`:92-110`；扣款失败订单 **→FAILED** 且无任何额度变化）→ `book()` 调 `POST /loans/{id}/transactions?command=repayment`（`FineractClient.java:131-141`）入账，读回本金/利息/费用分配明细（`:120-125`），订单 **→SUCCESS**，并按本金部分 `restoreByPrincipal` 逐层恢复额度（`:133`，`LimitService.java:191`）。
- 每笔还款交易落库后 Fineract 自动 `determineAndTransition`（`LoanAccountDomainServiceJpa.java:877`）：余额清零且费用结清 → **300→600 结清**；多缴 → **→700 超额**（`DefaultLoanLifecycleStateMachine.java:352-366`）。提前结清金额经 `GET /loans/{id}?command=calculatePrepayment` 试算（`FineractClient.java:104-113`）。

## 四、一个值得注意的证据缺口

`FineractClient.java:20-21` 注释称创建借据时“直接提交为经审批口径（submittedOnDate+approvedOnDate）”，但 `createLoan` 的实际 payload（`FineractClient.java:56-79`）**只含 `submittedOnDate`，未见 `approvedOnDate`**；而状态机要求 `LOAN_DISBURSED` 只能从 **200 APPROVED** 发起（`DefaultLoanLifecycleStateMachine.java:134-135`）。按此推断，若借据停留在 100 态，中台的 disburse 调用会被 Fineract 拒绝并转入 RETRY_NEEDED 重试。源码内未找到打通该环节的显式逻辑（如审批命令调用或 Fineract 侧豁免），此点**未核实闭环**，建议作为后续排查点：要么补 `approvedOnDate`，要么链路中存在本调查未覆盖的审批调用。

