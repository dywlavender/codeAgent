package demo.middle;

import java.util.List;
import java.util.Map;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/internal/orders")
class Middle {
    private final BookingGateway gateway;
    Middle(BookingGateway gateway) { this.gateway = gateway; }

    @GetMapping("/{id}")
    Map<String, Object> detail(String id) {
        Order order = new Order();
        return Map.of("status", order.getStatus(),
                "payments", List.of(Map.of("status", order.getPaymentStatus())),
                "booking", new Booking(order.getBookingStatus(), 2));
    }

    void onResult(Order order, String result) {
        if ("OK".equals(result)) {
            order.setPaymentStatus("SUCCESS");
            order.setStatus("BOOKING_PENDING");
            order.setBookingStatus("TO_BOOK");
            book(order);
        } else {
            order.setPaymentStatus("FAILED");
            order.setStatus("FAILED");
        }
    }

    void book(Order order) {
        try {
            gateway.book(order);
            order.setBookingStatus("BOOKED");
            order.setStatus("SUCCESS");
        } catch (RuntimeException exception) {
            order.setBookingStatus("RETRY_NEEDED");
        }
    }
}

record Booking(String status, int retryCount) {}

interface BookingGateway { void book(Order order); }
