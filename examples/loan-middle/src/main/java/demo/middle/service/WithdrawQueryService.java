package demo.middle.service;

import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Service;

@Service
public class WithdrawQueryService {
    public Object queryLimit(String customerId) {
        return Map.of("customerId", customerId, "availableLimit", 100000, "currency", "CNY");
    }

    public Object queryCoupons(String customerId, long amount) {
        return List.of(
                Map.of("couponId", "CP-100", "name", "提款立减券", "discountAmount", 100),
                Map.of("couponId", "CP-200", "name", "利率优惠券", "discountRate", "0.2%")
        );
    }

    public Object queryRepaymentMethods(String customerId) {
        return List.of("EQUAL_INSTALLMENT", "INTEREST_FIRST");
    }

    public Object queryRepaymentDates(String customerId) {
        return List.of(5, 10, 15, 20, 25);
    }

    public Object queryGuaranteeCompanies(String customerId) {
        return List.of(
                Map.of("companyId", "G-001", "companyName", "华东担保有限公司"),
                Map.of("companyId", "G-002", "companyName", "城市融资担保有限公司")
        );
    }

    public Object queryBankCards(String customerId) {
        return List.of(
                Map.of("bankCardId", "CARD-001", "bankName", "示例银行", "cardNo", "6222****8888")
        );
    }
}
