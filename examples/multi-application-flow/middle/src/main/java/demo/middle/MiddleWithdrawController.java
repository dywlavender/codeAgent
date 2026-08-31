package demo.middle;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/withdraw")
public class MiddleWithdrawController {
    private final WithdrawService service;

    public MiddleWithdrawController(WithdrawService service) {
        this.service = service;
    }

    @PostMapping("/apply")
    public void apply() {
        service.apply();
    }
}
