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


class InvalidDocumentRefError(DocumentConvertError):
    def __init__(self, message: str = "文档文件引用无效，已记录为内部处理异常。"):
        super().__init__(message)


class UnreadableDocumentRefError(DocumentConvertError):
    def __init__(self, message: str = "文档服务当前无法读取该文件引用，已记录为内部处理异常。"):
        super().__init__(message)


class UnsupportedDocumentFormatError(DocumentConvertError):
    def __init__(self, message: str = "当前暂不支持该文档格式。"):
        super().__init__(message)


class UnsupportedDocumentRouteError(DocumentConvertError):
    def __init__(self, message: str = "当前暂不支持该格式到目标格式的转换。"):
        super().__init__(message)


class DocumentDecodeError(DocumentConvertError):
    def __init__(self, message: str = "文档内容解码失败。"):
        super().__init__(message)


class DocumentInternalError(DocumentConvertError):
    def __init__(self, message: str = "文档服务内部异常，已记录用于排查。"):
        super().__init__(message)


class DocumentUserActionRequiredError(DocumentConvertError):
    def __init__(self, message: str):
        super().__init__(message)


class DocumentParseError(DocumentConvertError):
    def __init__(self, message: str = "文档解析失败。"):
        super().__init__(message)


class DocumentExportError(DocumentConvertError):
    def __init__(self, message: str = "文档导出失败。"):
        super().__init__(message)


class DocumentDownloadRefError(DocumentConvertError):
    def __init__(self, message: str = "文档下载引用生成失败。"):
        super().__init__(message)
