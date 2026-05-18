from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    url: str
    media_type: str
    filename: str
    content: bytes
