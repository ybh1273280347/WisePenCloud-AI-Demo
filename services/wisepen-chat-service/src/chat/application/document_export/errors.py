class DocumentExportError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class UnsupportedExportFormatError(DocumentExportError):
    def __init__(self, target_format: str):
        self.target_format = target_format
        super().__init__(f"Unsupported export format: {target_format}")


class EmptyExportContentError(DocumentExportError):
    def __init__(self, message: str = "Export content is empty."):
        super().__init__(message)


class ExportRenderError(DocumentExportError):
    def __init__(self, message: str):
        super().__init__(message)


class ExportTimeoutError(DocumentExportError):
    def __init__(self, message: str):
        super().__init__(message)


class ExportDependencyMissingError(DocumentExportError):
    def __init__(self, dependency: str):
        self.dependency = dependency
        super().__init__(f"Required export dependency is missing: {dependency}")


class ExportOutputError(DocumentExportError):
    def __init__(self, message: str):
        super().__init__(message)


class InvalidSourceFormatError(DocumentExportError):
    def __init__(self, source_format: str):
        self.source_format = source_format
        super().__init__(f"Invalid source format: {source_format}")


class DuplicateRendererFormatError(DocumentExportError):
    def __init__(self, target_format: str):
        self.target_format = target_format
        super().__init__(f"Duplicate renderer target format: {target_format}")
