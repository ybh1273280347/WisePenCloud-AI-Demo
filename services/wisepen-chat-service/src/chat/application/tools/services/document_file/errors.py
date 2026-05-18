class DocumentFileError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class InvalidDocumentRefError(DocumentFileError):
    def __init__(self, message: str = "文档文件引用无效，已记录为内部处理异常。"):
        super().__init__(message)


class UnreadableDocumentRefError(DocumentFileError):
    def __init__(self, message: str = "文档服务当前无法读取该文件引用，已记录为内部处理异常。"):
        super().__init__(message)


class DocumentTempRootMissingError(UnreadableDocumentRefError):
    def __init__(self, message: str = "document temp root does not exist"):
        super().__init__(message)


class DocumentSessionRootMissingError(UnreadableDocumentRefError):
    def __init__(self, message: str = "document session root does not exist"):
        super().__init__(message)


class DocumentFileScopeError(InvalidDocumentRefError):
    def __init__(self, message: str = "document file_ref is outside current user/session scope"):
        super().__init__(message)


class DocumentFileSymlinkEscapeError(InvalidDocumentRefError):
    def __init__(self, message: str = "document file_ref symlink escapes current user/session scope"):
        super().__init__(message)


class DocumentFilePermissionError(UnreadableDocumentRefError):
    def __init__(self, message: str = "document file_ref is not readable"):
        super().__init__(message)
