SUPPORTED_EXPORT_FORMATS = frozenset(
    {
        "markdown",
        "html",
        "pdf",
        "docx",
        "txt",
    }
)

CONTENT_TYPES = {
    "markdown": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain; charset=utf-8",
}

FILE_EXTENSIONS = {
    "markdown": ".md",
    "html": ".html",
    "pdf": ".pdf",
    "docx": ".docx",
    "txt": ".txt",
}

SOURCE_FORMATS = frozenset(
    {
        "markdown",
        "plain_text",
    }
)
