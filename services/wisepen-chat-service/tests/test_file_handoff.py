import os
import time
from pathlib import Path

import pytest

from chat.application.document_parse.file_resolver import LocalDocumentFileResolver
from chat.application.file_handoff import (
    FileHandoffInvalidSuffixError,
    FileHandoffWriteError,
    TemporaryFileHandoffStore,
)


def test_write_bytes_generates_resolvable_file_ref(tmp_path: Path) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=3600)

    result = store.write_bytes(
        session_id="session/../x",
        filename="../report.exe.pdf",
        content=b"pdf",
        canonical_suffix=".pdf",
    )

    assert result.local_path.is_file()
    assert result.local_path.read_bytes() == b"pdf"
    assert result.local_path.suffix == ".pdf"
    assert result.local_path.name[:16].isalnum()
    assert result.local_path.name[16] == "-"
    assert "exe" not in result.local_path.name
    assert LocalDocumentFileResolver().resolve(result.file_ref).local_path == result.local_path.resolve(strict=False)


def test_copy_file_copies_source_and_overrides_spoofed_suffix(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"docx")
    store = TemporaryFileHandoffStore(root_dir=tmp_path / "handoff", ttl_seconds=3600)

    result = store.copy_file(
        session_id="session",
        source_path=source,
        filename="report.pdf",
        canonical_suffix=".docx",
    )

    assert result.local_path.read_bytes() == b"docx"
    assert result.local_path.suffix == ".docx"
    assert result.local_path.name.endswith("-report.docx")


@pytest.mark.parametrize("suffix", [".txt", ".zip", "", "pdf"])
def test_invalid_suffix_raises(tmp_path: Path, suffix: str) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=3600)

    with pytest.raises(FileHandoffInvalidSuffixError):
        store.write_bytes(
            session_id="session",
            filename="report.pdf",
            content=b"pdf",
            canonical_suffix=suffix,
        )


def test_cleanup_expired_deletes_old_files(tmp_path: Path) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=1)
    result = store.write_bytes(
        session_id="session",
        filename="report.pdf",
        content=b"pdf",
        canonical_suffix=".pdf",
    )
    old = time.time() - 10
    os.utime(result.local_path, (old, old))

    store.cleanup_expired()

    assert not result.local_path.exists()


def test_session_and_filename_do_not_traverse(tmp_path: Path) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=3600)
    result = store.write_bytes(
        session_id="../../session",
        filename="../../report.pdf",
        content=b"pdf",
        canonical_suffix=".pdf",
    )

    resolved_root = tmp_path.resolve(strict=False)
    resolved_path = result.local_path.resolve(strict=False)
    assert resolved_path.is_relative_to(resolved_root)
    assert result.local_path.parent.name == "session"


def test_copy_missing_source_raises_write_error(tmp_path: Path) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=3600)

    with pytest.raises(FileHandoffWriteError):
        store.copy_file(
            session_id="session",
            source_path=tmp_path / "missing.pdf",
            filename="missing.pdf",
            canonical_suffix=".pdf",
        )
