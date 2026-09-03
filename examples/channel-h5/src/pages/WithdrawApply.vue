<template>
  <main>
    <h1>提款申请</h1>

    <button @click="queryLimit">1. 查询额度</button>
    <button @click="queryCoupons">2. 查询优惠券</button>
    <button @click="queryRepaymentMethods">3. 查询还款方式</button>
    <button @click="queryRepaymentDates">4. 查询还款日</button>
    <button @click="queryGuaranteeCompanies">5. 查询担保公司</button>
    <button @click="queryBankCards">6. 查询绑定卡</button>
    <button @click="signContract">7. 合同签订</button>
    <button @click="authorizeTax">8. 纳税授权</button>
    <button @click="submitWithdraw">9. 发起提款</button>
  </main>
</template>

<script setup lang="ts">
import request from "../utils/request";

const customerId = "CUST-10001";
const amount = 50000;

async function queryLimit() {
  return request.get("/api/withdraw/limit", { params: { customerId } });
}

async function queryCoupons() {
  return request.get("/api/withdraw/coupons", { params: { customerId, amount } });
}

async function queryRepaymentMethods() {
  return request.get("/api/withdraw/repayment-methods", { params: { customerId } });
}

async function queryRepaymentDates() {
  return request.get("/api/withdraw/repayment-dates", { params: { customerId } });
}

async function queryGuaranteeCompanies() {
  return request.get("/api/withdraw/guarantee-companies", { params: { customerId } });
}

async function queryBankCards() {
  return request.get("/api/withdraw/bank-cards", { params: { customerId } });
}

async function signContract() {
  return request.post("/api/withdraw/contracts/sign", {
    customerId,
    amount,
    couponId: "CP-100",
    repaymentMethod: "EQUAL_INSTALLMENT",
    repaymentDay: 15,
    guaranteeCompanyId: "G-001",
    bankCardId: "CARD-001",
  });
}

async function authorizeTax() {
  return request.post("/api/withdraw/tax-authorizations", {
    customerId,
    authorizationType: "TAX_DATA",
  });
}

async function submitWithdraw() {
  return request.post("/api/withdraw/apply", {
    customerId,
    amount,
    couponId: "CP-100",
    repaymentMethod: "EQUAL_INSTALLMENT",
    repaymentDay: 15,
    guaranteeCompanyId: "G-001",
    bankCardId: "CARD-001",
    contractId: "CT-10001",
    taxAuthorizationId: "TA-10001",
  });
}
</script>
