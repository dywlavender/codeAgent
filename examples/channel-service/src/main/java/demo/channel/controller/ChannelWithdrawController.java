package demo.channel.controller;

import demo.channel.model.ContractSignRequest;
import demo.channel.model.TaxAuthorizationRequest;
import demo.channel.model.WithdrawApplyRequest;
import demo.channel.service.ChannelWithdrawService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/withdraw")
public class ChannelWithdrawController {
    private final ChannelWithdrawService service;

    public ChannelWithdrawController(ChannelWithdrawService service) {
        this.service = service;
    }

    @GetMapping("/limit")
    public Object queryLimit(@RequestParam String customerId) {
        return service.queryLimit(customerId);
    }

    @GetMapping("/coupons")
    public Object queryCoupons(@RequestParam String customerId, @RequestParam long amount) {
        return service.queryCoupons(customerId, amount);
    }

    @GetMapping("/repayment-methods")
    public Object queryRepaymentMethods(@RequestParam String customerId) {
        return service.queryRepaymentMethods(customerId);
    }

    @GetMapping("/repayment-dates")
    public Object queryRepaymentDates(@RequestParam String customerId) {
        return service.queryRepaymentDates(customerId);
    }

    @GetMapping("/guarantee-companies")
    public Object queryGuaranteeCompanies(@RequestParam String customerId) {
        return service.queryGuaranteeCompanies(customerId);
    }

    @GetMapping("/bank-cards")
    public Object queryBankCards(@RequestParam String customerId) {
        return service.queryBankCards(customerId);
    }

    @PostMapping("/contracts/sign")
    public Object signContract(@RequestBody ContractSignRequest request) {
        return service.signContract(request);
    }

    @PostMapping("/tax-authorizations")
    public Object authorizeTax(@RequestBody TaxAuthorizationRequest request) {
        return service.authorizeTax(request);
    }

    @PostMapping("/apply")
    public Object applyWithdraw(@RequestBody WithdrawApplyRequest request) {
        return service.applyWithdraw(request);
    }
}
