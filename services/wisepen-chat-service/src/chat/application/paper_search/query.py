from __future__ import annotations


def normalize_query(query: str) -> str:
    return " ".join(str(query or "").split()).strip()
