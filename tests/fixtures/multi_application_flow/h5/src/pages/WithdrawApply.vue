<template>
  <button @click="submitWithdraw">提交提款申请</button>
</template>

<script setup lang="ts">
async function submitWithdraw() {
  await request.post("/api/withdraw/apply", { amount: 1000 })
}
</script>
