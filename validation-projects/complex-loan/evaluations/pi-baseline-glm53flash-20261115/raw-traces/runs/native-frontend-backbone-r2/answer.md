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