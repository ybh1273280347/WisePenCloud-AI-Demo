class WebSearchError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class SearchProviderError(WebSearchError):
    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(f"Search provider {provider} error: {message}")


class SearchTimeoutError(WebSearchError):
    def __init__(self, provider: str, query: str, timeout: float):
        self.provider = provider
        self.query = query
        self.timeout = timeout
        super().__init__(
            f"Search timed out: provider={provider}, query={query!r}, timeout={timeout}s"
        )


class SearchRateLimitError(WebSearchError):
    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(f"Search rate limited: provider={provider}, {message}")


class EmptySearchResultError(WebSearchError):
    def __init__(self, provider: str, query: str):
        self.provider = provider
        self.query = query
        super().__init__(
            f"Search returned empty results: provider={provider}, query={query!r}"
        )


class CustomSearchProviderUnavailableError(WebSearchError):
    def __init__(
        self,
        *,
        provider: str,
        public_code: str,
        status: str,
        last_error_code: str,
        message: str,
    ):
        self.provider = provider
        self.public_code = public_code
        self.status = status
        self.last_error_code = last_error_code
        super().__init__(message)


class SearchProviderTransientError(WebSearchError):
    def __init__(self, message: str):
        super().__init__(message)
