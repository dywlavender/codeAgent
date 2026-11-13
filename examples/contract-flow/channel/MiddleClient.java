package demo.channel;

import java.util.Map;
import org.springframework.web.client.RestClient;

class MiddleClient {
    private final RestClient http;
    private final MiddleProperties props;
    MiddleClient(MiddleProperties props) {
        this.props = props;
        this.http = RestClient.builder().baseUrl(props.baseUrl()).build();
    }
    Map<String, Object> detail(String id) {
        return http.get().uri("/internal/orders/{id}", id).retrieve().body(Map.class);
    }
}
