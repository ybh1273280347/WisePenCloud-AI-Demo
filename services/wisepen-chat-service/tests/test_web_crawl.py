from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT.parent / "wisepen-common" / "src"))

from chat.application.algorithms.url import canonicalize_url
from chat.application.web_crawl import CrawlRequest, WebCrawlService
from chat.application.web_crawl.formatting import format_crawl_result
from chat.application.web_crawl.frontier import CrawlFrontier
from chat.application.web_crawl.link_extractor import (
    extract_markdown_links,
    extract_markdown_title,
)
from chat.application.web_crawl.models import (
    CrawlItemKind,
    CrawlResultItem,
    CrawlSkipReason,
)
from chat.application.web_crawl.politeness import PerHostPoliteness
from chat.application.web_crawl.robots import RobotsDecision
from chat.application.web_fetch.fetch_coordinator import FetchResultItem
from chat.application.web_fetch.models import FetchedDocument
from chat.application.web_crawl import service as service_module


def test_markdown_link_extraction() -> None:
    markdown = "# Docs\nSee [Authentication API](https://example.com/auth) and https://example.com/raw."
    links = extract_markdown_links(markdown)

    assert extract_markdown_title(markdown) == "Docs"
    assert len(links) == 2
    assert links[0].anchor_text == "Authentication API"
    assert links[0].url == "https://example.com/auth"
    assert links[1].anchor_text == ""
    assert links[1].url == "https://example.com/raw"
    assert "Authentication API" in links[0].surrounding_text


def test_canonicalize_url_supports_base_url() -> None:
    assert (
        canonicalize_url("/api/auth?utm_source=x&b=2&a=1", base_url="https://www.Example.com/docs/")
        == "https://example.com/api/auth?a=1&b=2"
    )


def test_frontier_external_budgets() -> None:
    frontier = CrawlFrontier(
        seed_urls=["https://docs.example.com/"],
        max_pages=4,
        max_depth=2,
    )
    ok1, reason1 = frontier.add_candidate(_frontier_external("https://external.example.org/a"))
    ok2, reason2 = frontier.add_candidate(_frontier_external("https://external.example.org/b"))
    ok3, reason3 = frontier.add_candidate(_frontier_external("https://external.example.org/c"))
    ok4, reason4 = frontier.add_candidate(_frontier_external("https://another.example.org/a"))

    assert ok1 and reason1 is None
    assert ok2 and reason2 is None
    assert not ok3
    assert reason3 == CrawlSkipReason.EXTERNAL_BUDGET_EXCEEDED.value
    assert not ok4
    assert reason4 == CrawlSkipReason.EXTERNAL_BUDGET_EXCEEDED.value


async def test_crawl_fetches_relevant_links_and_skips_blocked_path() -> None:
    seed = "https://docs.example.com/"
    auth = "https://docs.example.com/api/auth"
    external = "https://external.example.org/auth-reference"
    coordinator = FakeFetchCoordinator(
        {
            seed: FetchResultItem(
                url=seed,
                success=True,
                content=(
                    "# Docs\n"
                    "[Authentication API](/api/auth)\n"
                    "[Pricing](/pricing)\n"
                    "[Login](/login)\n"
                    "https://external.example.org/auth-reference"
                ),
            ),
            auth: FetchResultItem(url=auth, success=True, content="# Authentication\nAPI auth token."),
            external: FetchResultItem(url=external, success=True, content="# Auth reference\nExternal auth."),
        }
    )
    service = _service(coordinator)

    result = await service.crawl(
        CrawlRequest(
            session_id="s1",
            seed_urls=[seed],
            objective="api authentication",
            max_depth=1,
            max_pages=4,
        )
    )
    urls = [item.url for item in result.items]
    skipped = [item for item in result.items if item.kind == CrawlItemKind.SKIPPED.value]

    assert coordinator.calls[0] == [seed]
    assert any(auth in call for call in coordinator.calls[1:])
    assert seed in urls
    assert auth in urls
    assert any(item.skip_reason == CrawlSkipReason.BLOCKED_PATH.value for item in skipped)
    assert result.fetched_pages >= 2


async def test_max_depth_controls_second_layer_fetching() -> None:
    seed = "https://docs.example.com/"
    auth = "https://docs.example.com/api/auth"
    token = "https://docs.example.com/api/token"
    mapping = {
        seed: FetchResultItem(url=seed, success=True, content="# Docs\n[Authentication API](/api/auth)"),
        auth: FetchResultItem(url=auth, success=True, content="# Authentication\n[Token API](/api/token)"),
        token: FetchResultItem(url=token, success=True, content="# Token\nToken auth details."),
    }

    shallow = await _service(FakeFetchCoordinator(mapping)).crawl(
        CrawlRequest(
            session_id="s1",
            seed_urls=[seed],
            objective="api authentication token",
            max_depth=1,
            max_pages=5,
        )
    )
    deep = await _service(FakeFetchCoordinator(mapping)).crawl(
        CrawlRequest(
            session_id="s1",
            seed_urls=[seed],
            objective="api authentication token",
            max_depth=2,
            max_pages=5,
        )
    )

    assert token not in [item.url for item in shallow.items]
    assert token in [item.url for item in deep.items]


async def test_document_result_outputs_file_ref_only() -> None:
    seed = "https://docs.example.com/"
    pdf = "https://docs.example.com/paper.pdf"
    coordinator = FakeFetchCoordinator(
        {
            seed: FetchResultItem(url=seed, success=True, content="# Docs\n[Paper](/paper.pdf)"),
            pdf: FetchResultItem(
                url=pdf,
                success=True,
                document=FetchedDocument(
                    url=pdf,
                    media_type="application/pdf",
                    filename="paper.pdf",
                    content=b"%PDF",
                ),
            ),
        }
    )

    result = await _service(coordinator).crawl(
        CrawlRequest(
            session_id="s1",
            seed_urls=[seed],
            objective="paper document",
            max_depth=1,
            max_pages=4,
        )
    )
    formatted = format_crawl_result(result)

    assert "Document parse required:" in formatted
    assert "file_ref:" not in formatted
    assert "download_ref" not in formatted
    assert any(item.kind == CrawlItemKind.DOCUMENT.value and item.file_ref == "handoff://paper.pdf" for item in result.items)


async def test_robots_and_rate_limit_failures_do_not_block_other_hosts() -> None:
    seed = "https://docs.example.com/"
    denied = "https://docs.example.com/private"
    limited = "https://limited.example.org/a"
    limited_second = "https://limited.example.org/b"
    other = "https://other.example.org/open"
    coordinator = FakeFetchCoordinator(
        {
            seed: FetchResultItem(
                url=seed,
                success=True,
                content=(
                    "# Docs\n"
                    "[Denied private](/private)\n"
                    "[Alpha rate limit](https://limited.example.org/a)\n"
                    "[Beta rate limit](https://limited.example.org/b)\n"
                    "[Other](https://other.example.org/open)"
                ),
            ),
            limited: FetchResultItem(url=limited, success=False, error="HTTP 429 rate limit"),
            limited_second: FetchResultItem(url=limited_second, success=True, content="# should not fetch"),
            other: FetchResultItem(url=other, success=True, content="# Other"),
        }
    )
    service = _service(
        coordinator,
        robots_policy=FakeRobotsPolicy(
            decisions={
                denied: RobotsDecision(False, reason="robots_disallowed"),
            }
        ),
    )

    result = await service.crawl(
        CrawlRequest(
            session_id="s1",
            seed_urls=[seed],
            objective="denied private alpha beta other",
            max_depth=1,
            max_pages=6,
        )
    )
    skipped = [item for item in result.items if item.kind in {CrawlItemKind.SKIPPED.value, CrawlItemKind.ERROR.value}]

    assert any(item.url == denied and item.skip_reason == CrawlSkipReason.ROBOTS_DISALLOWED.value for item in skipped)
    assert any(item.url == limited and item.kind == CrawlItemKind.ERROR.value for item in skipped)
    assert any(item.url == limited_second and item.skip_reason == CrawlSkipReason.RATE_LIMITED.value for item in skipped)
    assert any(item.url == other and item.kind == CrawlItemKind.PAGE.value for item in result.items)


def test_formatting_assistant_instructions() -> None:
    formatted = format_crawl_result(
        result=service_module.CrawlResult(
            objective="api authentication",
            seed_urls=["https://docs.example.com/"],
            items=[
                CrawlResultItem(
                    url="https://docs.example.com/",
                    kind=CrawlItemKind.PAGE.value,
                    depth=0,
                    success=True,
                    content_block="content_id: cnt_1",
                )
            ],
            fetched_pages=1,
            documents_found=0,
            skipped_count=0,
            max_depth=1,
            max_pages=2,
        )
    )
    assert "[Tool Result] web_crawl" in formatted
    assert "Assistant instructions:" in formatted
    assert "Use evidence_rank" in formatted


def test_service_does_not_import_fetchers_or_web_search() -> None:
    source = (_ROOT / "src" / "chat" / "application" / "web_crawl" / "service.py").read_text(encoding="utf-8")
    forbidden = [
        "StaticFetcher",
        "SteelFetcher",
        "LocalScriptFetcher",
        "ContentProcessor",
        "local_web_fetcher",
        "document_parse",
        "web_search",
    ]
    assert all(term not in source for term in forbidden)


def _frontier_external(url: str):
    return service_module.CrawlFrontierItem(
        url=url,
        depth=1,
        origin_host="docs.example.com",
        current_host=url.split("/")[2],
        source_url="https://docs.example.com/",
        is_external=True,
        external_depth=1,
    )


def _service(
    coordinator: "FakeFetchCoordinator",
    *,
    robots_policy: "FakeRobotsPolicy | None" = None,
) -> WebCrawlService:
    return WebCrawlService(
        fetch_coordinator=coordinator,
        file_handoff_store=FakeHandoffStore(),
        robots_policy=robots_policy or FakeRobotsPolicy(),
        politeness_min_interval_seconds=0.0,
    )


class FakeFetchCoordinator:
    def __init__(self, mapping: dict[str, FetchResultItem]):
        self.mapping = mapping
        self.calls: list[list[str]] = []

    async def fetch_many(self, urls: list[str]) -> list[FetchResultItem]:
        self.calls.append(list(urls))
        return [
            self.mapping.get(
                url,
                FetchResultItem(url=url, success=False, error="missing fake response"),
            )
            for url in urls
        ]


class FakeRobotsPolicy:
    def __init__(self, decisions: dict[str, RobotsDecision] | None = None):
        self.decisions = decisions or {}
        self.calls: list[tuple[str, bool]] = []

    async def can_fetch(self, *, url: str, is_seed_url: bool, user_agent: str = "WisePenBot") -> RobotsDecision:
        self.calls.append((url, is_seed_url))
        return self.decisions.get(url, RobotsDecision(True))


class FakeHandoffStore:
    def write_bytes(self, *, session_id: str, filename: str, content: bytes, canonical_suffix: str):
        return FakeHandoffResult(file_ref=f"handoff://{filename}")


@dataclass(frozen=True, slots=True)
class FakeHandoffResult:
    file_ref: str


async def _run_async_tests() -> None:
    await test_crawl_fetches_relevant_links_and_skips_blocked_path()
    await test_max_depth_controls_second_layer_fetching()
    await test_document_result_outputs_file_ref_only()
    await test_robots_and_rate_limit_failures_do_not_block_other_hosts()


def _patch_runtime_dependencies() -> None:
    service_module.validate_public_http_url = lambda url: url
    service_module.cache_and_format = (
        lambda **kwargs: f"content_id: cnt_{service_module.stable_hash(kwargs['source'])}\n{kwargs['text'][:80]}"
    )


def main() -> None:
    _patch_runtime_dependencies()
    test_markdown_link_extraction()
    test_canonicalize_url_supports_base_url()
    test_frontier_external_budgets()
    asyncio.run(_run_async_tests())
    test_formatting_assistant_instructions()
    test_service_does_not_import_fetchers_or_web_search()
    print("web_crawl tests passed")


if __name__ == "__main__":
    main()
