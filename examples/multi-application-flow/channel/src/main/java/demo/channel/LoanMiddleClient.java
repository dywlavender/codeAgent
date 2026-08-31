package demo.channel;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;

@FeignClient(name = "loan-middle")
@RequestMapping("/withdraw")
public interface LoanMiddleClient {
    @PostMapping("/apply")
    void apply();
}
