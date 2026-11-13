package demo.channel;

import java.util.Map;
import org.springframework.web.bind.annotation.*;

/** 渠道订单详情：展示状态与完整结算明细分别返回。 */
@RestController
@RequestMapping("/orders")
class Channel {
    private final MiddleClient middle;
    Channel(MiddleClient middle) { this.middle = middle; }

    @GetMapping("/{id}")
    Map<String, Object> detail(String id) {
        Map<String, Object> data = middle.detail(id);
        String status = String.valueOf(data.get("status"));
        return Map.of("displayStatus", display("ignored", status), "detail", data);
    }

    String display(String unused, String state) {
        return switch (state) {
            case "SUCCESS", "FAILED" -> state;
            default -> "PROCESSING";
        };
    }
}
