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


def test_build_download_url_encodes_slash_and_chinese_name() -> None:
    download_ref = build_download_ref(
        session_id="6a0706059d072e874322e57e",
        file_name="复旦大学计算机_信息概要.md",
    )

    assert build_download_url(download_ref=download_ref) == (
        "/api/document-export/download?ref="
        "6a0706059d072e874322e57e%2F"
        "%E5%A4%8D%E6%97%A6%E5%A4%A7%E5%AD%A6"
        "%E8%AE%A1%E7%AE%97%E6%9C%BA_"
        "%E4%BF%A1%E6%81%AF%E6%A6%82%E8%A6%81.md"
    )


def test_resolver_accepts_valid_ref_with_chinese_file_name(tmp_path: Path) -> None:
    resolver = DocumentDownloadResolver(output_root=tmp_path)

    resolved = resolver.resolve(download_ref="session/复旦大学计算机_信息概要.md")

    assert resolved.session_id == "session"
    assert resolved.file_name == "复旦大学计算机_信息概要.md"
    assert resolved.file_path == (
        tmp_path / "session" / "复旦大学计算机_信息概要.md"
    ).resolve(strict=False)


@pytest.mark.parametrize(
    "download_ref",
    [
        "",
        "abc",
        "abc/",
        "/file.md",
        "a/b/c.md",
        "../a.md",
        "a/../b.md",
        "a/%2e%2e/b.md",
        "a\\b.md",
        "session/bad:name.md",
    ],
)
def test_resolver_rejects_invalid_refs(
    tmp_path: Path,
    download_ref: str,
) -> None:
    resolver = DocumentDownloadResolver(output_root=tmp_path)

    with pytest.raises(ExportOutputError):
        resolver.resolve(download_ref=download_ref)
