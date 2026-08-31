package demo.channel;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/withdraw")
public class ChannelWithdrawController {
    private final LoanMiddleClient client;

    public ChannelWithdrawController(LoanMiddleClient client) {
        this.client = client;
    }

    @PostMapping("/apply")
    public void apply() {
        client.apply();
    }
}
