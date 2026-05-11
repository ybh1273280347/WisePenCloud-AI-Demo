from typing import List, Optional

from langchain_text_splitters.character import RecursiveCharacterTextSplitter

from .models import ContentChunk


def create_content_chunks(text: str, chunk_size: int) -> List[ContentChunk]:
    if not text:
        return []

    splitter = RecursiveCharacterTextSplitter(
        # chunk_overlap 固定为 0：基于偏移量窗口的持续读取依赖于无重叠的分块。
        # 如果未来 RAG 需要重叠分块，请创建专用的 RagChunker 而非修改此函数。
        chunk_size=chunk_size,
        chunk_overlap=0,
        separators=["\n\n", "\n", " ", ""],
        length_function=len,
        strip_whitespace=False,
    )

    pieces = [piece for piece in splitter.split_text(text) if piece]

    chunks: List[ContentChunk] = []
    previous_end = 0

    for index, piece in enumerate(pieces):
        start = text.find(piece, previous_end)

        if start < 0:
            raise ValueError(
                "Unable to align chunk back to source text: "
                f"chunk_index={index}, previous_end={previous_end}, "
                f"piece_length={len(piece)}, piece_preview={piece[:80]!r}"
            )

        end = start + len(piece)

        if end > len(text):
            raise ValueError(
                f"Chunk end_offset exceeds text length: "
                f"chunk_index={index}, end_offset={end}, text_length={len(text)}"
            )

        if end <= start:
            raise ValueError(
                f"Chunk end_offset must be greater than start_offset: "
                f"chunk_index={index}, start={start}, end={end}"
            )

        if start < previous_end:
            raise ValueError(
                f"Chunk offsets must be non-overlapping and monotonic: "
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


def find_chunk_by_offset(chunks: List[ContentChunk], offset: int) -> Optional[ContentChunk]:
    offset = max(0, offset)

    for chunk in chunks:
        if chunk.end_offset > offset:
            return chunk

    return None
