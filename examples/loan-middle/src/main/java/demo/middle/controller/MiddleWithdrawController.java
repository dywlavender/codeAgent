package demo.middle.controller;

import demo.middle.model.ContractSignRequest;
import demo.middle.model.TaxAuthorizationRequest;
import demo.middle.model.WithdrawApplyRequest;
import demo.middle.service.ContractService;
import demo.middle.service.TaxAuthorizationService;
import demo.middle.service.WithdrawApplicationService;
import demo.middle.service.WithdrawQueryService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/middle/withdraw")
public class MiddleWithdrawController {
    private final WithdrawQueryService queryService;
    private final ContractService contractService;
    private final TaxAuthorizationService taxAuthorizationService;
    private final WithdrawApplicationService withdrawApplicationService;

    public MiddleWithdrawController(
            WithdrawQueryService queryService,
            ContractService contractService,
            TaxAuthorizationService taxAuthorizationService,
            WithdrawApplicationService withdrawApplicationService) {
        this.queryService = queryService;
        this.contractService = contractService;
        this.taxAuthorizationService = taxAuthorizationService;
        this.withdrawApplicationService = withdrawApplicationService;
    }

    @GetMapping("/limit")
    public Object queryLimit(@RequestParam String customerId) {
        return queryService.queryLimit(customerId);
    }

    @GetMapping("/coupons")
    public Object queryCoupons(@RequestParam String customerId, @RequestParam long amount) {
        return queryService.queryCoupons(customerId, amount);
    }

    @GetMapping("/repayment-methods")
    public Object queryRepaymentMethods(@RequestParam String customerId) {
        return queryService.queryRepaymentMethods(customerId);
    }

    @GetMapping("/repayment-dates")
    public Object queryRepaymentDates(@RequestParam String customerId) {
        return queryService.queryRepaymentDates(customerId);
    }

    @GetMapping("/guarantee-companies")
    public Object queryGuaranteeCompanies(@RequestParam String customerId) {
        return queryService.queryGuaranteeCompanies(customerId);
    }

    @GetMapping("/bank-cards")
    public Object queryBankCards(@RequestParam String customerId) {
        return queryService.queryBankCards(customerId);
    }

    @PostMapping("/contracts/sign")
    public Object signContract(@RequestBody ContractSignRequest request) {
        return contractService.signContract(request);
    }

    @PostMapping("/tax-authorizations")
    public Object authorizeTax(@RequestBody TaxAuthorizationRequest request) {
        return taxAuthorizationService.authorize(request);
    }

    @PostMapping("/apply")
    public Object applyWithdraw(@RequestBody WithdrawApplyRequest request) {
        return withdrawApplicationService.apply(request);
    }
}
