package demo.application;

public class ApplyService {
    public void generate(Apply apply) {
        // 申请阶段根据产品方案确定还款方式。
        apply.setRepayType("A");
    }
}

