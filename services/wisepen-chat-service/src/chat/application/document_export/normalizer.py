from dataclasses import dataclass

from .errors import InvalidSourceFormatError


@dataclass(frozen=True, slots=True)
class ContentNormalizer:
    def normalize(self, *, content: str, source_format: str) -> str:
        if source_format == "markdown":
            return content

        if source_format == "plain_text":
            return self._plain_text_to_markdown(content)

        raise InvalidSourceFormatError(source_format)

    def _plain_text_to_markdown(self, content: str) -> str:
        # V1: plain text is returned as-is. Future versions may escape
        # Markdown-special characters to prevent plain text from being
        # misinterpreted as Markdown syntax.
        return content
