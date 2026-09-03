package demo.middle.service;

import demo.middle.model.ContractSignRequest;
import java.util.Map;
import org.springframework.stereotype.Service;

@Service
public class ContractService {
    public Object signContract(ContractSignRequest request) {
        return Map.of(
                "contractId", "CT-10001",
                "status", "SIGNED",
                "customerId", request.customerId
        );
    }
}
