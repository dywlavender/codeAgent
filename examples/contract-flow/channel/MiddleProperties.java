package demo.channel;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "middle")
record MiddleProperties(String baseUrl) {}
