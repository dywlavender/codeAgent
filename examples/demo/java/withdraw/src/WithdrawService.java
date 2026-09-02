package demo.withdraw;

public class WithdrawService {
    public void validate(Apply apply) {
        // 提款工程与申请工程没有直接方法调用。
        if (!"A".equals(apply.getRepayType())) {
            throw new IllegalArgumentException("提款只能使用申请确定的还款方式");
        }
    }
}
