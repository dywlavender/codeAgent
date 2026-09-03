package demo.channel.client;

import demo.channel.model.ContractSignRequest;
import demo.channel.model.TaxAuthorizationRequest;
import demo.channel.model.WithdrawApplyRequest;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;

@FeignClient(name = "loan-middle")
@RequestMapping("/middle/withdraw")
public interface LoanMiddleClient {
    @GetMapping("/limit")
    Object queryLimit(@RequestParam String customerId);

    @GetMapping("/coupons")
    Object queryCoupons(@RequestParam String customerId, @RequestParam long amount);

    @GetMapping("/repayment-methods")
    Object queryRepaymentMethods(@RequestParam String customerId);

    @GetMapping("/repayment-dates")
    Object queryRepaymentDates(@RequestParam String customerId);

    @GetMapping("/guarantee-companies")
    Object queryGuaranteeCompanies(@RequestParam String customerId);

    @GetMapping("/bank-cards")
    Object queryBankCards(@RequestParam String customerId);

    @PostMapping("/contracts/sign")
    Object signContract(@RequestBody ContractSignRequest request);

    @PostMapping("/tax-authorizations")
    Object authorizeTax(@RequestBody TaxAuthorizationRequest request);

    @PostMapping("/apply")
    Object applyWithdraw(@RequestBody WithdrawApplyRequest request);
}
