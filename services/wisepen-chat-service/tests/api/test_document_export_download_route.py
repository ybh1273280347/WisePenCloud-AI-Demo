from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chat.api.endpoints import document_export


class FakeSessionRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def get_by_id_and_user(self, session_id: str, user_id: str):
        self.calls.append((session_id, user_id))
        return object()


def test_download_route_returns_generated_file_with_chinese_filename(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_root = tmp_path / "generated"
    file_path = output_root / "session" / "复旦大学计算机_信息概要.md"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("# hello", encoding="utf-8")
    repo = FakeSessionRepository()
    client = _client(tmp_path=output_root, repo=repo, monkeypatch=monkeypatch)

    response = client.get(
        "/api/document-export/download",
        params={"ref": "session/复旦大学计算机_信息概要.md"},
    )

    assert response.status_code == 200
    assert response.content == b"# hello"
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''"
        f"{quote('复旦大学计算机_信息概要.md', safe='')}"
    )
    assert repo.calls == [("session", "user")]


def test_download_route_rejects_invalid_ref_before_session_lookup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = FakeSessionRepository()
    client = _client(tmp_path=tmp_path, repo=repo, monkeypatch=monkeypatch)

    response = client.get("/api/document-export/download", params={"ref": "a/b/c.md"})

    assert response.status_code == 400
    assert repo.calls == []


def test_download_route_returns_404_for_missing_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = FakeSessionRepository()
    client = _client(tmp_path=tmp_path, repo=repo, monkeypatch=monkeypatch)

    response = client.get(
        "/api/document-export/download",
        params={"ref": "session/missing.md"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Generated document not found."
    assert repo.calls == [("session", "user")]


def _client(tmp_path: Path, repo: FakeSessionRepository, monkeypatch) -> TestClient:
    monkeypatch.setattr(document_export, "document_export_output_path", lambda: tmp_path)
    app = FastAPI()
    app.dependency_overrides[document_export.current_user_id] = lambda: "user"
    app.dependency_overrides[document_export.get_session_repo] = lambda: repo
    app.include_router(document_export.router)
    return TestClient(app)
