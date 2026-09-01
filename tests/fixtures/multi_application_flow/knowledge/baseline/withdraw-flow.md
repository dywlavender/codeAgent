# 提款业务基线

## 提款申请主流程

1. 用户在 H5 发起提款申请。
2. 渠道系统接收并转交提款申请。
3. 贷款中台完成提款申请处理。

### 调查入口

- 提款 H5 | PAGE | WithdrawApply.vue
- 渠道服务 | CONTROLLER | ChannelWithdrawController
- 贷款中台 | CONTROLLER | MiddleWithdrawController
