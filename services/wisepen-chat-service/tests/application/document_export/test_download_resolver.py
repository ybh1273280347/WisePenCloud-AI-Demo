from pathlib import Path

import pytest

from chat.application.document_export.download_reference import (
    build_download_ref,
    build_download_url,
)
from chat.application.document_export.download_resolver import (
    DocumentDownloadResolver,
)
from chat.application.document_export.errors import ExportOutputError


def test_build_download_url_encodes_ref() -> None:
    download_ref = build_download_ref(
        user_id="user",
        session_id="6a0706059d072e874322e57e",
        storage_file_name="0123456789abcdef0123456789abcdef-复旦大学计算机_信息概要.md",
    )

    assert download_ref == (
        "user/6a0706059d072e874322e57e/"
        "0123456789abcdef0123456789abcdef-复旦大学计算机_信息概要.md"
    )
    assert build_download_url(download_ref=download_ref).startswith(
        "/api/document-export/download?ref=user%2F6a0706059d072e874322e57e%2F"
    )


def test_resolver_accepts_valid_ref_with_user_session_scope(tmp_path: Path) -> None:
    resolver = DocumentDownloadResolver(output_root=tmp_path)

    resolved = resolver.resolve(
        download_ref=(
            "user/session/"
            "0123456789abcdef0123456789abcdef-复旦大学计算机_信息概要.md"
        ),
        user_id="user",
    )

    assert resolved.user_id == "user"
    assert resolved.session_id == "session"
    assert resolved.file_name == "复旦大学计算机_信息概要.md"
    assert resolved.file_path == (
        tmp_path
        / "user"
        / "session"
        / "outputs"
        / "0123456789abcdef0123456789abcdef-复旦大学计算机_信息概要.md"
    ).resolve(strict=False)


def test_resolver_rejects_other_user(tmp_path: Path) -> None:
    resolver = DocumentDownloadResolver(output_root=tmp_path)

    with pytest.raises(ExportOutputError):
        resolver.resolve(
            download_ref=(
                "user-b/session/"
                "0123456789abcdef0123456789abcdef-output.md"
            ),
            user_id="user-a",
        )


@pytest.mark.parametrize(
    "download_ref",
    [
        "",
        "abc",
        "abc/",
        "/file.md",
        "a/b/c/d.md",
        "../a/b.md",
        "a/../b.md",
        "a/b/../c.md",
        "a\\b\\c.md",
        "user/session/bad:name.md",
    ],
)
def test_resolver_rejects_invalid_refs(
    tmp_path: Path,
    download_ref: str,
) -> None:
    resolver = DocumentDownloadResolver(output_root=tmp_path)

    with pytest.raises(ExportOutputError):
        resolver.resolve(download_ref=download_ref, user_id="user")
