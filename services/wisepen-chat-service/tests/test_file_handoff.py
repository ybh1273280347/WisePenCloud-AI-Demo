import os
import time
from pathlib import Path

import pytest

from chat.application.tools.common.file_handoff import (
    FileHandoffInvalidSuffixError,
    FileHandoffWriteError,
    TemporaryFileHandoffStore,
)
from chat.application.tools.services.document_file import (
    DocumentTempFileResolver,
    InvalidDocumentRefError,
    UnreadableDocumentRefError,
)


def test_write_bytes_generates_user_session_scoped_file_ref(tmp_path: Path) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=3600)

    result = store.write_bytes(
        user_id="user/../x",
        session_id="session/../s",
        filename="../report.exe.pdf",
        content=b"pdf",
        canonical_suffix=".pdf",
        content_type="application/pdf",
    )

    assert result.local_path.is_file()
    assert result.local_path.read_bytes() == b"pdf"
    assert result.local_path.suffix == ".pdf"
    assert result.local_path.parent.parent.name == "x"
    assert result.local_path.parent.name == "s"
    assert len(result.local_path.name.split("-", 1)[0]) == 32
    assert "exe" not in result.local_path.name
    resolved = DocumentTempFileResolver(temp_root=tmp_path).resolve(
        file_ref=result.file_ref,
        user_id="user/../x",
        session_id="session/../s",
    )
    assert resolved.path == result.local_path.resolve(strict=False)


def test_copy_file_copies_source_and_overrides_spoofed_suffix(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"docx")
    store = TemporaryFileHandoffStore(root_dir=tmp_path / "handoff", ttl_seconds=3600)

    result = store.copy_file(
        user_id="user",
        session_id="session",
        source_path=source,
        filename="report.pdf",
        canonical_suffix=".docx",
    )

    assert result.local_path.read_bytes() == b"docx"
    assert result.local_path.suffix == ".docx"
    assert result.local_path.name.endswith("-report.docx")


@pytest.mark.parametrize("suffix", [".zip", "", "pdf"])
def test_invalid_suffix_raises(tmp_path: Path, suffix: str) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=3600)

    with pytest.raises(FileHandoffInvalidSuffixError):
        store.write_bytes(
            user_id="user",
            session_id="session",
            filename="report.pdf",
            content=b"pdf",
            canonical_suffix=suffix,
        )


@pytest.mark.parametrize("suffix", [".md", ".markdown", ".txt"])
def test_text_suffixes_are_valid_document_handoffs(tmp_path: Path, suffix: str) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=3600)

    result = store.write_bytes(
        user_id="user",
        session_id="session",
        filename=f"notes{suffix}",
        content=b"# notes",
        canonical_suffix=suffix,
    )

    assert result.local_path.suffix == suffix


def test_cleanup_expired_deletes_old_session_dir(tmp_path: Path) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=1, grace_seconds=1)
    result = store.write_bytes(
        user_id="user",
        session_id="session",
        filename="report.pdf",
        content=b"pdf",
        canonical_suffix=".pdf",
    )
    old = time.time() - 10
    os.utime(result.local_path, (old, old))
    os.utime(result.local_path.parent, (old, old))

    store.cleanup_expired()

    assert not result.local_path.parent.exists()


def test_cleanup_skips_in_progress_session(tmp_path: Path) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=1, grace_seconds=1)
    result = store.write_bytes(
        user_id="user",
        session_id="session",
        filename="report.pdf",
        content=b"pdf",
        canonical_suffix=".pdf",
    )
    marker = result.local_path.parent / ".in_progress"
    marker.mkdir()
    (marker / "active.lock").write_text("", encoding="utf-8")
    old = time.time() - 10
    os.utime(result.local_path, (old, old))
    os.utime(result.local_path.parent, (old, old))

    store.cleanup_expired()

    assert result.local_path.exists()


def test_resolver_rejects_other_user_and_session(tmp_path: Path) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=3600)
    result = store.write_bytes(
        user_id="user-a",
        session_id="session-a",
        filename="notes.md",
        content=b"# notes",
        canonical_suffix=".md",
    )
    resolver = DocumentTempFileResolver(temp_root=tmp_path)

    with pytest.raises(InvalidDocumentRefError):
        resolver.resolve(
            file_ref=result.file_ref,
            user_id="user-b",
            session_id="session-a",
        )

    with pytest.raises(InvalidDocumentRefError):
        resolver.resolve(
            file_ref=result.file_ref,
            user_id="user-a",
            session_id="session-b",
        )


def test_resolver_rejects_missing_file_and_directory(tmp_path: Path) -> None:
    (tmp_path / "user" / "session").mkdir(parents=True)
    resolver = DocumentTempFileResolver(temp_root=tmp_path)

    with pytest.raises(UnreadableDocumentRefError):
        resolver.resolve(
            file_ref=str(tmp_path / "user" / "session" / "missing.md"),
            user_id="user",
            session_id="session",
        )

    with pytest.raises(UnreadableDocumentRefError):
        resolver.resolve(
            file_ref=str(tmp_path / "user" / "session"),
            user_id="user",
            session_id="session",
        )


def test_resolver_rejects_path_traversal_and_symlink_escape(tmp_path: Path) -> None:
    session_root = tmp_path / "user" / "session"
    session_root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("# outside", encoding="utf-8")
    link = session_root / "link.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink is unavailable on this platform")

    resolver = DocumentTempFileResolver(temp_root=tmp_path)

    with pytest.raises(InvalidDocumentRefError):
        resolver.resolve(
            file_ref="../other.md",
            user_id="user",
            session_id="session",
        )

    with pytest.raises(InvalidDocumentRefError):
        resolver.resolve(
            file_ref=str(link),
            user_id="user",
            session_id="session",
        )


def test_missing_user_or_session_rejected(tmp_path: Path) -> None:
    store = TemporaryFileHandoffStore(root_dir=tmp_path, ttl_seconds=3600)

    with pytest.raises(FileHandoffWriteError):
        store.write_bytes(
            user_id="",
            session_id="session",
            filename="report.pdf",
            content=b"pdf",
            canonical_suffix=".pdf",
        )
