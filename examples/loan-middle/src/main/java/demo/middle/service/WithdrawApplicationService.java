package demo.middle.service;

import demo.middle.model.WithdrawApplyRequest;
import java.util.Map;
import org.springframework.stereotype.Service;

@Service
public class WithdrawApplicationService {
    public Object apply(WithdrawApplyRequest request) {
        validatePreconditions(request);
        return Map.of(
                "withdrawApplicationId", "WD-10001",
                "status", "ACCEPTED",
                "customerId", request.customerId,
                "amount", request.amount
        );
    }

    private void validatePreconditions(WithdrawApplyRequest request) {
        if (request.contractId == null || request.taxAuthorizationId == null) {
            throw new IllegalArgumentException("合同签订和纳税授权必须在提款申请前完成");
        }
        if (request.bankCardId == null || request.repaymentMethod == null || request.repaymentDay == null) {
            throw new IllegalArgumentException("提款申请缺少银行卡或还款参数");
        }
    }
}
