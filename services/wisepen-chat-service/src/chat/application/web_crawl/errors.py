from __future__ import annotations


class WebCrawlError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class CrawlConfigurationError(WebCrawlError):
    def __init__(self, message: str):
        super().__init__(message)


class CrawlInputError(WebCrawlError):
    def __init__(self, message: str):
        super().__init__(message)
