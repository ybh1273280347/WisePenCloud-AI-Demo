class DocumentConvertError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class EmptyParsedMarkdownError(DocumentConvertError):
    def __init__(
        self, message: str = "Document parser returned empty Markdown content."
    ):
        super().__init__(message)


class FileConvertError(DocumentConvertError):
    def __init__(self, message: str):
        super().__init__(message)


class SourceFileNotFoundError(DocumentConvertError):
    def __init__(self, file_ref: str):
        self.file_ref = file_ref
        super().__init__(f"Source file not found: {file_ref}")
