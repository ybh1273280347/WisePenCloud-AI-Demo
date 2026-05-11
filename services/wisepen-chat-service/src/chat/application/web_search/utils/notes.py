from typing import List, Optional


def add_note(notes: Optional[List[str]], note: str) -> None:
    if notes is None:
        return

    normalized = " ".join(note.strip().split())
    if not normalized:
        return

    existing = {" ".join(item.strip().split()) for item in notes}
    if normalized in existing:
        return

    notes.append(normalized)
