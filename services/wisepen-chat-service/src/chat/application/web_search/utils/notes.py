from typing import List, Optional


def add_note(notes: Optional[List[str]], note: str) -> None:
    if notes is None:
        return

    note = note.strip()
    if not note:
        return

    notes.append(note)
