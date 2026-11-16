这三个方法的 HTTP 调用如下，均位于 `repos/mifos-web-repo/src/app/loans/loans.service.ts`：

- **`createLoansAccount`**：`POST /loans`，请求体为 `loanAccount`。见 [loans.service.ts](repos/mifos-web-repo/src/app/loans/loans.service.ts:460)。
- **`getLoansAccountTemplateResource`**：`GET /loans/template`。请求参数包括 `activeOnly=true`、`staffInSelectedOfficeOnly=true`，可选 `productId`；另按 `isGroup` 设置 `groupId` 与 `templateType=group`，或 `clientId` 与 `templateType=individual`。见 [loans.service.ts](repos/mifos-web-repo/src/app/loans/loans.service.ts:427) 和 [loans.service.ts](repos/mifos-web-repo/src/app/loans/loans.service.ts:433)。
- **`submitLoanActionButton`**：`POST /loans/${loanId}/transactions`，请求体为 `data`，并通过查询参数传入 `command`。见 [loans.service.ts](repos/mifos-web-repo/src/app/loans/loans.service.ts:238) 和 [loans.service.ts](repos/mifos-web-repo/src/app/loans/loans.service.ts:240)。

## 目标覆盖
- [已核实] `createLoansAccount` 的 HTTP 方法与端点：`POST /loans`。依据：[loans.service.ts](repos/mifos-web-repo/src/app/loans/loans.service.ts:460)。
- [已核实] `getLoansAccountTemplateResource` 的 HTTP 方法与端点：`GET /loans/template`。依据：[loans.service.ts](repos/mifos-web-repo/src/app/loans/loans.service.ts:427)、[loans.service.ts](repos/mifos-web-repo/src/app/loans/loans.service.ts:433)。
- [已核实] `submitLoanActionButton` 的 HTTP 方法与端点：`POST /loans/${loanId}/transactions`，带 `command` 查询参数。依据：[loans.service.ts](repos/mifos-web-repo/src/app/loans/loans.service.ts:238)、[loans.service.ts](repos/mifos-web-repo/src/app/loans/loans.service.ts:240)。