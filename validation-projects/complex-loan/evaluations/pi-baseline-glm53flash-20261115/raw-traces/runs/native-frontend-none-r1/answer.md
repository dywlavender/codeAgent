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