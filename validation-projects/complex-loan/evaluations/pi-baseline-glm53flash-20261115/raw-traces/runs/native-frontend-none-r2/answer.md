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