import { createRouter, createWebHistory } from "vue-router";
import WithdrawApply from "../pages/WithdrawApply.vue";

export default createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/withdraw/apply",
      name: "withdraw-apply",
      component: WithdrawApply,
    },
  ],
});
