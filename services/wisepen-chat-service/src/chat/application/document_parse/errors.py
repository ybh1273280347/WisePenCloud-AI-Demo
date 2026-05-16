class DocumentParseError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class UnsupportedDocumentFormatError(DocumentParseError):
    def __init__(self, suffix: str, guidance: str = ""):
        self.suffix = suffix
        self.guidance = guidance
        msg = f"Unsupported document type: {suffix}"
        if guidance:
            msg = f"{msg}. {guidance}"
        super().__init__(msg)


class EmptyParsedContentError(DocumentParseError):
    def __init__(self, path: str):
        self.path = path
        super().__init__(f"No text extracted from document: {path}")


class DocumentParserDependencyError(DocumentParseError):
    def __init__(self, dependency: str, message: str):
        self.dependency = dependency
        super().__init__(f"Document parser dependency error: {dependency} - {message}")


class DocumentParserTimeoutError(DocumentParseError):
    def __init__(self, operation: str):
        self.operation = operation
        super().__init__(f"Document parser operation timed out: {operation}")


class OcrProcessingError(DocumentParseError):
    def __init__(self, message: str):
        super().__init__(message)
