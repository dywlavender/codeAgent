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