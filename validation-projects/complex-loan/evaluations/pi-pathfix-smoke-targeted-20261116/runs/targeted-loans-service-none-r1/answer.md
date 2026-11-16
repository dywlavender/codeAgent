`createLoansAccount` 调用的是 HTTP `POST` 方法，端点为 `/loans`，并将传入的 `loanAccount` 作为请求体发送：

```ts
createLoansAccount(loanAccount: any): Observable<any> {
  return this.http.post('/loans', loanAccount);
}
```

依据：[repos/mifos-web-repo/src/app/loans/loans.service.ts:460](repos/mifos-web-repo/src/app/loans/loans.service.ts:460) 至 [repos/mifos-web-repo/src/app/loans/loans.service.ts:461](repos/mifos-web-repo/src/app/loans/loans.service.ts:461)。