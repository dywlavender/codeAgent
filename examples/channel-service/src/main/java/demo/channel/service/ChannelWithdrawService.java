package demo.channel.service;

import demo.channel.client.LoanMiddleClient;
import demo.channel.model.ContractSignRequest;
import demo.channel.model.TaxAuthorizationRequest;
import demo.channel.model.WithdrawApplyRequest;
import org.springframework.stereotype.Service;

@Service
public class ChannelWithdrawService {
    private final LoanMiddleClient loanMiddleClient;

    public ChannelWithdrawService(LoanMiddleClient loanMiddleClient) {
        this.loanMiddleClient = loanMiddleClient;
    }

    public Object queryLimit(String customerId) {
        return loanMiddleClient.queryLimit(customerId);
    }

    public Object queryCoupons(String customerId, long amount) {
        return loanMiddleClient.queryCoupons(customerId, amount);
    }

    public Object queryRepaymentMethods(String customerId) {
        return loanMiddleClient.queryRepaymentMethods(customerId);
    }

    public Object queryRepaymentDates(String customerId) {
        return loanMiddleClient.queryRepaymentDates(customerId);
    }

    public Object queryGuaranteeCompanies(String customerId) {
        return loanMiddleClient.queryGuaranteeCompanies(customerId);
    }

    public Object queryBankCards(String customerId) {
        return loanMiddleClient.queryBankCards(customerId);
    }

    public Object signContract(ContractSignRequest request) {
        return loanMiddleClient.signContract(request);
    }

    public Object authorizeTax(TaxAuthorizationRequest request) {
        return loanMiddleClient.authorizeTax(request);
    }

    public Object applyWithdraw(WithdrawApplyRequest request) {
        return loanMiddleClient.applyWithdraw(request);
    }
}
