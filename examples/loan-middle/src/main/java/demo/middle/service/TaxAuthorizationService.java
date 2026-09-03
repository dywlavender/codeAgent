package demo.middle.service;

import demo.middle.model.TaxAuthorizationRequest;
import java.util.Map;
import org.springframework.stereotype.Service;

@Service
public class TaxAuthorizationService {
    public Object authorize(TaxAuthorizationRequest request) {
        return Map.of(
                "taxAuthorizationId", "TA-10001",
                "status", "AUTHORIZED",
                "customerId", request.customerId
        );
    }
}
