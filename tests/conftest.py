from business_code_agent.platform.env import normalize_proxy_environment


# The CLI normalizes bracketed IPv6 NO_PROXY entries before creating HTTP
# clients. Tests construct clients directly, so apply the same compatibility
# normalization once for the whole suite.
normalize_proxy_environment()
