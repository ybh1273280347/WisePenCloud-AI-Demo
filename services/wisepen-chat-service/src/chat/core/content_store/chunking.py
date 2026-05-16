from typing import List, Optional

from langchain_text_splitters.character import RecursiveCharacterTextSplitter

from .models import ContentChunk

_PLAIN_TEXT_SEPARATORS = ["\n\n", "\n", " ", ""]
_MARKDOWN_SEPARATORS = [
    "\n\n# ",
    "\n\n## ",
    "\n\n### ",
    "\n\n#### ",
    "\n\n```",
    "\n\n---",
    "\n\n",
    "\n",
    "。",
    ". ",
    " ",
    "",
]
_MARKDOWN_CONTENT_TYPE = "text/markdown"


def create_content_chunks(
    text: str, chunk_size: int, *, content_type: Optional[str] = None
) -> List[ContentChunk]:
    if not text:
        return []

    splitter = RecursiveCharacterTextSplitter(
        # chunk_overlap 固定为 0：基于偏移量窗口的持续读取依赖于无重叠的分块。
        # 如果未来 RAG 需要重叠分块，请创建专用的 RagChunker 而非修改此函数。
        chunk_size=chunk_size,
        chunk_overlap=0,
        separators=_separators_for_content_type(content_type),
        length_function=len,
        add_start_index=True,
        strip_whitespace=False,
    )

    documents = [
        document
        for document in splitter.create_documents([text])
        if document.page_content
    ]

    chunks: List[ContentChunk] = []
    previous_end = 0

    for index, document in enumerate(documents):
        piece = document.page_content
        start = document.metadata.get("start_index")

        if not isinstance(start, int) or start < 0:
            raise ValueError(
                "Missing valid chunk start_index metadata: "
                f"chunk_index={index}, previous_end={previous_end}, "
                f"piece_length={len(piece)}, piece_preview={piece[:80]!r}"
            )

        end = start + len(piece)

        if end > len(text):
            raise ValueError(
                f"Chunk end_offset exceeds text length: "
                f"chunk_index={index}, end_offset={end}, text_length={len(text)}"
            )

        if text[start:end] != piece:
            raise ValueError(
                "Chunk text does not match source text at start_index: "
                f"chunk_index={index}, start={start}, end={end}, "
                f"piece_preview={piece[:80]!r}"
            )

        if end <= start:
            raise ValueError(
                f"Chunk end_offset must be greater than start_offset: "
                f"chunk_index={index}, start={start}, end={end}"
            )

        if start != previous_end:
            raise ValueError(
                f"Chunk offsets must be contiguous and monotonic: "
                f"chunk_index={index}, start={start}, previous_end={previous_end}"
            )

        chunks.append(
            ContentChunk(
                index=index,
                start_offset=start,
                end_offset=end,
            )
        )

        previous_end = end

    return chunks


def _separators_for_content_type(content_type: Optional[str]) -> List[str]:
    if content_type == _MARKDOWN_CONTENT_TYPE:
        return _MARKDOWN_SEPARATORS

    return _PLAIN_TEXT_SEPARATORS


def find_chunk_by_offset(
    chunks: List[ContentChunk], offset: int
) -> Optional[ContentChunk]:
    offset = max(0, offset)

    for chunk in chunks:
        if chunk.end_offset > offset:
            return chunk

    return None
