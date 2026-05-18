class WebFetchError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class WebFetchTimeoutError(WebFetchError):
    def __init__(self, url: str, timeout: float):
        self.url = url
        self.timeout = timeout
        super().__init__(f"Web fetch timed out: url={url!r}, timeout={timeout}s")


class WebFetchNetworkError(WebFetchError):
    def __init__(self, message: str):
        super().__init__(message)


class WebFetchContentError(WebFetchError):
    def __init__(self, message: str):
        super().__init__(message)


class FetchProviderError(WebFetchError):
    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(f"Fetch provider {provider} error: {message}")


class UnsupportedMediaError(WebFetchError):
    def __init__(self, url: str, media_type: str):
        self.url = url
        self.media_type = media_type
        super().__init__(
            f"该 URL 返回的是 {media_type} 等媒体资源，不适合作为网页正文抓取。"
        )
