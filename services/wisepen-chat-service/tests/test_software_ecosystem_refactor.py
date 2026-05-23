from __future__ import annotations

import asyncio

import pytest

from chat.application.tools.services.software_ecosystem.common.errors import (
    InvalidSoftwareEcosystemQueryError,
    PackageVersionNotFoundError,
    UnsupportedEcosystemError,
)
from chat.application.tools.services.software_ecosystem.common.normalization import (
    normalize_package_name,
    package_entity_id,
)
from chat.application.tools.services.software_ecosystem.community.models import (
    CommunityDiscussionSignal,
)
from chat.application.tools.services.software_ecosystem.community.ranking import (
    rank_community_discussions,
)
from chat.application.tools.services.software_ecosystem.open_source.discovery.models import (
    OpenSourceProjectCandidate,
)
from chat.application.tools.services.software_ecosystem.open_source.hydration.service import (
    OpenSourceProjectHydrationService,
)
from chat.application.tools.services.software_ecosystem.open_source.github.models import (
    GitHubIssueResult,
    GitHubReleaseResult,
    GitHubRepositoryResult,
)
from chat.application.tools.services.software_ecosystem.packages.discovery.models import (
    PackageCandidate,
)
from chat.application.tools.services.software_ecosystem.packages.discovery.ranking import (
    rank_package_candidates,
)
from chat.application.tools.services.software_ecosystem.packages.hydration.cache import (
    latest_pointer_cache,
    package_profile_cache,
)
from chat.application.tools.services.software_ecosystem.packages.hydration.models import (
    PackageHydrationSignals,
    PackageProfile,
)
from chat.application.tools.services.software_ecosystem.packages.hydration.service import (
    PackageHydrationService,
)
from chat.application.tools.services.software_ecosystem.providers.deps_dev.mapper import (
    extract_versions,
    summarize_dependency_graph,
)
from chat.application.tools.services.software_ecosystem.providers.hacker_news.mapper import (
    map_hacker_news_hit,
)
from chat.application.tools.services.software_ecosystem.providers.npm.mapper import (
    map_npm_metadata,
)
from chat.application.tools.services.software_ecosystem.providers.pypi.mapper import (
    map_pypi_metadata,
)
from chat.application.tools.services.software_ecosystem.research.mapper import (
    map_community_discussion_candidate,
    map_open_source_project_candidate,
    map_package_candidate,
)
from chat.application.tools.services.software_ecosystem.research.ranking import (
    rank_software_ecosystem_candidates,
)
from chat.application.tools.services.software_ecosystem.research.service import (
    SoftwareEcosystemResearchService,
)
from chat.application.tools.vertical_search.software_ecosystem_research_tool import (
    _TOOL_SCHEMA,
)


def test_package_candidate_normalization_and_entity_id() -> None:
    assert normalize_package_name("pypi", "My_Package") == "my-package"
    assert normalize_package_name("npm", "@Types/Node") == "@types/node"
    assert package_entity_id("npm", "@types/node") == "pkg:npm:@types/node"


def test_targets_validation_and_hydration_depth_validation() -> None:
    service = SoftwareEcosystemResearchService(
        open_source_project_discovery=_EmptyOpenSourceDiscovery(),
        open_source_project_hydration=_UnusedOpenSourceHydration(),
        package_discovery=_EmptyPackageDiscovery(),
        package_hydration=_UnusedPackageHydration(),
        community_discussion=_EmptyCommunityService(),
    )

    with pytest.raises(InvalidSoftwareEcosystemQueryError):
        asyncio.run(
            service.research(
                query=" ",
                targets=["package"],
                ecosystems=["pypi"],
                languages=None,
                sort="relevance",
                limit=1,
                min_stars=None,
                package_hydration_depth="light",
            )
        )
    with pytest.raises(InvalidSoftwareEcosystemQueryError):
        asyncio.run(
            service.research(
                query="pdf parser",
                targets=["unknown"],
                ecosystems=["pypi"],
                languages=None,
                sort="relevance",
                limit=1,
                min_stars=None,
                package_hydration_depth="light",
            )
        )
    with pytest.raises(UnsupportedEcosystemError):
        asyncio.run(
            service.research(
                query="pdf parser",
                targets=["package"],
                ecosystems=["rubygems"],
                languages=None,
                sort="relevance",
                limit=1,
                min_stars=None,
                package_hydration_depth="light",
            )
        )
    with pytest.raises(InvalidSoftwareEcosystemQueryError):
        asyncio.run(
            service.research(
                query="pdf parser",
                targets=["package"],
                ecosystems=["pypi"],
                languages=None,
                sort="relevance",
                limit=True,
                min_stars=None,
                package_hydration_depth="light",
            )
        )
    with pytest.raises(InvalidSoftwareEcosystemQueryError):
        asyncio.run(
            service.research(
                query="pdf parser",
                targets=["open_source_project"],
                ecosystems=None,
                languages=["Python"],
                sort="relevance",
                limit=1,
                min_stars=True,
                package_hydration_depth="light",
            )
        )
    with pytest.raises(InvalidSoftwareEcosystemQueryError):
        asyncio.run(
            service.research(
                query="pdf parser",
                targets=["package"],
                ecosystems=["pypi"],
                languages=None,
                sort="relevance",
                limit=1,
                min_stars=None,
                package_hydration_depth="full",
            )
        )


def test_schema_uses_package_hydration_depth() -> None:
    properties = _TOOL_SCHEMA["properties"]
    assert properties["package_hydration_depth"]["enum"] == ["light", "standard", "deep"]
    assert (
        properties["min_stars"]["description"]
        == "Minimum GitHub stars for open-source project search. Only applies when targets includes open_source_project. Suggested model defaults: null for general project discovery, 100 for mature projects, 500 for high-star projects, and 5000 for top/headline projects."
    )


def test_target_dispatch_package_only_uses_depth() -> None:
    package_discovery = _PackageDiscovery([_candidate("pypi", "pdfplumber", "PDF table extraction")])
    package_hydration = _PackageHydration()
    service = SoftwareEcosystemResearchService(
        open_source_project_discovery=_EmptyOpenSourceDiscovery(),
        open_source_project_hydration=_UnusedOpenSourceHydration(),
        package_discovery=package_discovery,
        package_hydration=package_hydration,
        community_discussion=_EmptyCommunityService(),
    )

    result = asyncio.run(
        service.research(
            query="pdf table extraction",
            targets=["package"],
            ecosystems=["pypi"],
            languages=None,
            sort="relevance",
            limit=2,
            min_stars=None,
            package_hydration_depth="deep",
        )
    )

    assert package_discovery.calls == 1
    assert package_hydration.depths == ["deep"]
    assert [item.name for item in result.recommended_packages] == ["pdfplumber"]
    assert result.recommended_projects == []
    assert result.community_discussions == []


def test_target_dispatch_open_source_only_ignores_package_depth() -> None:
    project_discovery = _OpenSourceDiscovery([_project("owner/rag", stars=5000)])
    project_hydration = _OpenSourceHydration()
    package_hydration = _UnusedPackageHydration()
    service = SoftwareEcosystemResearchService(
        open_source_project_discovery=project_discovery,
        open_source_project_hydration=project_hydration,
        package_discovery=_EmptyPackageDiscovery(),
        package_hydration=package_hydration,
        community_discussion=_EmptyCommunityService(),
    )

    result = asyncio.run(
        service.research(
            query="rag framework",
            targets=["open_source_project"],
            ecosystems=None,
            languages=["Python"],
            sort="stars",
            limit=1,
            min_stars=100,
            package_hydration_depth="light",
        )
    )

    assert project_discovery.calls == 1
    assert project_discovery.last_min_stars == 100
    assert project_hydration.repos == ["owner/rag"]
    assert result.recommended_projects[0].full_name == "owner/rag"
    assert result.recommended_packages == []


def test_target_dispatch_community_only() -> None:
    community = _CommunityService([_discussion("Zod schema validation discussion")])
    service = SoftwareEcosystemResearchService(
        open_source_project_discovery=_EmptyOpenSourceDiscovery(),
        open_source_project_hydration=_UnusedOpenSourceHydration(),
        package_discovery=_EmptyPackageDiscovery(),
        package_hydration=_UnusedPackageHydration(),
        community_discussion=community,
    )

    result = asyncio.run(
        service.research(
            query="schema validation",
            targets=["community_discussion"],
            ecosystems=None,
            languages=None,
            sort="relevance",
            limit=3,
            min_stars=None,
            package_hydration_depth="light",
        )
    )

    assert community.calls == 1
    assert result.recommended_projects == []
    assert result.recommended_packages == []
    assert result.community_discussions[0].title.startswith("Zod")


def test_multi_target_dispatch_returns_projects_packages_and_community() -> None:
    service = SoftwareEcosystemResearchService(
        open_source_project_discovery=_OpenSourceDiscovery([_project("owner/rag", stars=5000)]),
        open_source_project_hydration=_OpenSourceHydration(),
        package_discovery=_PackageDiscovery([_candidate("pypi", "pdfplumber", "PDF table extraction")]),
        package_hydration=_PackageHydration(),
        community_discussion=_CommunityService([_discussion("PDF table extraction discussion")]),
    )

    result = asyncio.run(
        service.research(
            query="pdf table extraction",
            targets=["open_source_project", "package", "community_discussion"],
            ecosystems=["pypi"],
            languages=["Python"],
            sort="popularity",
            limit=3,
            min_stars=None,
            package_hydration_depth="standard",
        )
    )

    assert result.recommended_projects
    assert result.recommended_packages
    assert result.community_discussions


def test_candidate_mapping_for_three_entity_types() -> None:
    project = map_open_source_project_candidate(_project("owner/rag", stars=123))
    package = map_package_candidate(_candidate("npm", "zod", "schema validation"))
    discussion = map_community_discussion_candidate(_discussion("Zod 4 released"))

    assert project.id == "repo:github:owner/rag"
    assert project.candidate_type == "open_source_project"
    assert project.metrics["stars"] == 123.0
    assert package.id == "pkg:npm:zod"
    assert package.candidate_type == "package"
    assert discussion.id.startswith("community:hacker_news:")
    assert discussion.candidate_type == "community_discussion"


def test_unified_candidate_deduplication_and_sorting() -> None:
    low = map_open_source_project_candidate(_project("owner/rag", stars=100))
    high = map_open_source_project_candidate(_project("owner/rag", stars=5000))
    ranked = rank_software_ecosystem_candidates(
        query="unrelated",
        targets=["open_source_project"],
        sort="stars",
        candidates=[low, high],
    )
    assert len(ranked) == 1
    assert ranked[0].metrics["stars"] == 5000.0

    recent = map_open_source_project_candidate(
        _project("owner/recent", stars=1, pushed_at="2026-01-01T00:00:00Z")
    )
    old = map_open_source_project_candidate(
        _project("owner/old", stars=100000, pushed_at="2019-01-01T00:00:00Z")
    )
    ranked = rank_software_ecosystem_candidates(
        query="unrelated",
        targets=["open_source_project"],
        sort="recent_activity",
        candidates=[old, recent],
    )
    assert ranked[0].title == "owner/recent"


def test_package_candidate_deduplication_and_ranking() -> None:
    package_name = "came" + "lot-py"
    weak = _candidate("pypi", "came" + "lot_py", "generic extraction", source="github", raw_score=1)
    strong = _candidate("pypi", package_name, "PDF table extraction", source="ecosystems", raw_score=10)
    other = _candidate("pypi", "black", "Python formatter", source="ecosystems", raw_score=100)
    ranked = rank_package_candidates("pdf table extraction", [weak, other, strong])
    assert ranked[0].name == package_name
    assert [item.name for item in ranked].count(package_name) == 1


def test_package_hydration_depths() -> None:
    package_profile_cache.clear()
    latest_pointer_cache.clear()
    light_deps = _FakeDepsDevClient()
    light_pypi = _FakePyPIClient()
    service = PackageHydrationService(
        deps_dev_client=light_deps,
        pypi_client=light_pypi,
        npm_client=_FakeNpmClient(),
        github_service=_FakeGitHubService(),
    )
    light = asyncio.run(
        service.hydrate(
            ecosystem="pypi",
            package_name="pdfplumber-light",
            version=None,
            package_hydration_depth="light",
        )
    )
    assert light.selected_version == "0.11.0"
    assert light.summary is None
    assert light_deps.get_version_calls == 0
    assert light_deps.requirements_calls == 0
    assert light_deps.dependencies_calls == 0
    assert light_pypi.calls == 0

    package_profile_cache.clear()
    standard_deps = _FakeDepsDevClient()
    standard_pypi = _FakePyPIClient()
    service = PackageHydrationService(
        deps_dev_client=standard_deps,
        pypi_client=standard_pypi,
        npm_client=_FakeNpmClient(),
        github_service=_FakeGitHubService(),
    )
    standard = asyncio.run(
        service.hydrate(
            ecosystem="pypi",
            package_name="pdfplumber-standard",
            version=None,
            package_hydration_depth="standard",
        )
    )
    assert standard.summary == "PDF table extraction"
    assert standard.direct_dependencies_count == 1
    assert standard.transitive_dependencies_count is None
    assert standard_deps.get_version_calls == 1
    assert standard_deps.requirements_calls == 1
    assert standard_deps.dependencies_calls == 0

    package_profile_cache.clear()
    deep_deps = _FakeDepsDevClient()
    service = PackageHydrationService(
        deps_dev_client=deep_deps,
        pypi_client=_FakePyPIClient(),
        npm_client=_FakeNpmClient(),
        github_service=_FakeGitHubService(),
    )
    deep = asyncio.run(
        service.hydrate(
            ecosystem="pypi",
            package_name="pdfplumber-deep",
            version="0.11.0",
            package_hydration_depth="deep",
        )
    )
    assert deep.direct_dependencies_count == 1
    assert deep.transitive_dependencies_count == 1
    assert deep_deps.dependencies_calls == 1


def test_missing_requested_version_error() -> None:
    service = PackageHydrationService(
        deps_dev_client=_FakeDepsDevClient(),
        pypi_client=_FakePyPIClient(),
        npm_client=_FakeNpmClient(),
        github_service=_FakeGitHubService(),
    )
    with pytest.raises(PackageVersionNotFoundError):
        asyncio.run(
            service.hydrate(
                ecosystem="pypi",
                package_name="pdfplumber",
                version="9.9.9",
                package_hydration_depth="standard",
            )
        )


def test_open_source_project_hydration() -> None:
    github = _HydrationGitHubService()
    service = OpenSourceProjectHydrationService(github)
    profile = asyncio.run(
        service.hydrate(
            owner="owner",
            repo="rag",
            include_readme=True,
            include_releases=True,
            include_issues=True,
        )
    )

    assert profile.full_name == "owner/rag"
    assert profile.readme_preview == "README content"
    assert profile.recent_releases == ["v1.0.0"]
    assert profile.issue_discussion_count == 7
    assert profile.stars == 5000


def test_provider_mappers() -> None:
    versions = extract_versions(
        {
            "versions": [
                {
                    "versionKey": {"version": "1.0.0"},
                    "publishedAt": "2026-01-01T00:00:00Z",
                    "isDefault": True,
                }
            ]
        }
    )
    assert versions[0].version == "1.0.0"

    graph = summarize_dependency_graph(
        {
            "nodes": [
                {"versionKey": {"name": "root", "version": "1"}},
                {"versionKey": {"name": "dep", "version": "2"}},
            ],
            "edges": [{"fromNode": 0, "toNode": 1}],
        }
    )
    assert graph.direct_dependencies_count == 1
    assert graph.sample_dependencies == ["dep@2"]

    npm = map_npm_metadata(
        {
            "description": "schema validation",
            "dist-tags": {"latest": "1.0.0"},
            "versions": {
                "1.0.0": {
                    "license": "MIT",
                    "dependencies": {"dep": "^1.0.0"},
                    "repository": {"url": "https://github.com/colinhacks/zod"},
                }
            },
        },
        selected_version="1.0.0",
    )
    assert npm.repository_url == "https://github.com/colinhacks/zod"
    assert npm.declared_dependencies == {"dep": "^1.0.0"}

    pypi = map_pypi_metadata(
        {
            "info": {
                "summary": "PDF tables",
                "project_urls": {"Source": "https://github.com/jsvine/pdfplumber"},
                "requires_dist": ["dep>=1"],
            },
            "vulnerabilities": [],
        }
    )
    assert pypi.repository_url == "https://github.com/jsvine/pdfplumber"

    hn = map_hacker_news_hit(
        {
            "title": "Zod 4 released",
            "url": "https://example.com",
            "points": 10,
            "num_comments": 4,
        }
    )
    assert hn is not None
    assert hn.points == 10


def test_community_ranking() -> None:
    signals = [
        _discussion("Generic package", points=1, comments=0, published_at="2020-01-01T00:00:00Z"),
        _discussion(
            "Zod schema validation discussion",
            points=100,
            comments=25,
            summary="TypeScript validation",
            published_at="2026-01-01T00:00:00Z",
        ),
    ]
    assert rank_community_discussions("schema validation", signals)[0].title.startswith("Zod")


def _candidate(
    ecosystem: str,
    name: str,
    summary: str,
    *,
    source: str = "ecosystems",
    raw_score: float = 10.0,
) -> PackageCandidate:
    return PackageCandidate(
        ecosystem=ecosystem,
        name=name,
        normalized_name=normalize_package_name(ecosystem, name),
        summary=summary,
        repository_url="https://github.com/example/repo",
        homepage_url=None,
        source=source,
        raw_score=raw_score,
        matched_terms=[],
    )


def _project(
    full_name: str,
    *,
    stars: int,
    pushed_at: str = "2026-01-01T00:00:00Z",
) -> OpenSourceProjectCandidate:
    return OpenSourceProjectCandidate(
        full_name=full_name,
        html_url=f"https://github.com/{full_name}",
        description="RAG framework",
        language="Python",
        stars=stars,
        forks=100,
        open_issues=5,
        default_branch="main",
        updated_at=pushed_at,
        pushed_at=pushed_at,
        license_name="MIT",
        archived=False,
        source="github",
        raw_score=float(stars),
        matched_terms=[],
    )


def _discussion(
    title: str,
    *,
    points: int = 10,
    comments: int = 4,
    summary: str | None = None,
    published_at: str = "2026-01-01T00:00:00Z",
) -> CommunityDiscussionSignal:
    return CommunityDiscussionSignal(
        source="hacker_news",
        title=title,
        url=f"https://example.com/{title.replace(' ', '-').lower()}",
        published_at=published_at,
        points=points,
        comments_count=comments,
        summary=summary,
        matched_terms=[],
    )


def _profile(name: str) -> PackageProfile:
    return PackageProfile(
        ecosystem="pypi",
        name=name,
        normalized_name=normalize_package_name("pypi", name),
        selected_version="1.0.0",
        latest_version="1.0.0",
        published_at="2026-01-01T00:00:00Z",
        summary="PDF table extraction",
        description_preview="PDF table extraction",
        homepage_url=None,
        repository_url="https://github.com/example/project",
        license="MIT",
        deprecated=False,
        deprecated_reason=None,
        direct_dependencies_count=1,
        transitive_dependencies_count=None,
        recent_versions=["1.0.0"],
        repository_stars=1000,
        repository_forks=100,
        repository_open_issues=5,
        repository_pushed_at="2026-01-01T00:00:00Z",
        repository_archived=False,
        maintenance_score=0.9,
        popularity_score=0.8,
        dependency_complexity_score=1.0,
        ecosystem_score=1.0,
        evidence=["registry metadata loaded"],
        signals=PackageHydrationSignals(
            available_versions_count=1,
            advisories_count=0,
            requirements_count=1,
            licenses=["MIT"],
        ),
    )


class _EmptyOpenSourceDiscovery:
    async def search(self, **kwargs):
        return []

    async def close(self) -> None:
        return None


class _OpenSourceDiscovery:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = 0
        self.last_min_stars = None

    async def search(self, **kwargs):
        self.calls += 1
        self.last_min_stars = kwargs.get("min_stars")
        return self.candidates

    async def close(self) -> None:
        return None


class _UnusedOpenSourceHydration:
    async def hydrate(self, **kwargs):
        raise AssertionError("open source hydration should not be used")

    async def close(self) -> None:
        return None


class _OpenSourceHydration:
    def __init__(self):
        self.repos: list[str] = []

    async def hydrate(self, *, owner: str, repo: str, **kwargs):
        self.repos.append(f"{owner}/{repo}")
        return _open_source_profile(f"{owner}/{repo}")

    async def close(self) -> None:
        return None


class _EmptyPackageDiscovery:
    async def search(self, **kwargs):
        return []

    async def close(self) -> None:
        return None


class _PackageDiscovery:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = 0

    async def search(self, **kwargs):
        self.calls += 1
        return self.candidates

    async def close(self) -> None:
        return None


class _UnusedPackageHydration:
    async def hydrate(self, **kwargs):
        raise AssertionError("package hydration should not be used")

    async def close(self) -> None:
        return None


class _PackageHydration:
    def __init__(self):
        self.depths: list[str] = []

    async def hydrate(self, *, package_name: str, package_hydration_depth: str, **kwargs):
        self.depths.append(package_hydration_depth)
        return _profile(package_name)

    async def close(self) -> None:
        return None


class _EmptyCommunityService:
    async def search(self, **kwargs):
        return []

    async def close(self) -> None:
        return None


class _CommunityService:
    def __init__(self, signals):
        self.signals = signals
        self.calls = 0

    async def search(self, **kwargs):
        self.calls += 1
        return self.signals

    async def close(self) -> None:
        return None


class _FakeDepsDevClient:
    def __init__(self) -> None:
        self.get_version_calls = 0
        self.requirements_calls = 0
        self.dependencies_calls = 0

    async def get_package(self, *, system: str, name: str):
        return {
            "versions": [
                {
                    "versionKey": {"version": "0.10.0"},
                    "publishedAt": "2025-01-01T00:00:00Z",
                    "isDefault": False,
                },
                {
                    "versionKey": {"version": "0.11.0" if system == "PYPI" else "4.0.0"},
                    "publishedAt": "2026-01-01T00:00:00Z",
                    "isDefault": True,
                },
            ]
        }

    async def get_version(self, *, system: str, name: str, version: str):
        self.get_version_calls += 1
        return {
            "publishedAt": "2026-01-01T00:00:00Z",
            "licenses": ["MIT"],
            "links": [{"url": "https://github.com/example/project"}],
        }

    async def get_requirements(self, *, system: str, name: str, version: str):
        self.requirements_calls += 1
        return {"requirements": [{"name": "dep"}]}

    async def get_dependencies(self, *, system: str, name: str, version: str):
        self.dependencies_calls += 1
        return {
            "nodes": [
                {"versionKey": {"name": name, "version": version}},
                {"versionKey": {"name": "dep", "version": "1.0.0"}},
                {"versionKey": {"name": "transitive", "version": "2.0.0"}},
            ],
            "edges": [{"fromNode": 0, "toNode": 1}, {"fromNode": 1, "toNode": 2}],
        }

    async def close(self) -> None:
        return None


class _FakePyPIClient:
    def __init__(self) -> None:
        self.calls = 0

    async def get_project(self, package_name: str):
        self.calls += 1
        return {
            "info": {
                "summary": "PDF table extraction",
                "description": "PDF table extraction package",
                "project_urls": {"Source": "https://github.com/example/project"},
                "license": "MIT",
                "requires_dist": ["dep>=1"],
            },
            "vulnerabilities": [],
        }

    async def close(self) -> None:
        return None


class _FakeNpmClient:
    async def get_package(self, package_name: str):
        return {
            "description": "schema validation",
            "dist-tags": {"latest": "4.0.0"},
            "versions": {
                "4.0.0": {
                    "description": "schema validation",
                    "license": "MIT",
                    "dependencies": {"dep": "^1.0.0"},
                    "repository": {"url": "https://github.com/example/project"},
                }
            },
        }

    async def close(self) -> None:
        return None


class _FakeGitHubService:
    async def get_repository(self, *, owner: str, repo: str):
        return _repo(owner, repo, stars=1000)

    async def close(self) -> None:
        return None


class _HydrationGitHubService:
    async def get_repository(self, *, owner: str, repo: str):
        return _repo(owner, repo, stars=5000)

    async def get_readme(self, *, owner: str, repo: str):
        return {"content_preview": "README content"}

    async def get_releases(self, *, owner: str, repo: str, limit: int):
        return [
            GitHubReleaseResult(
                name="v1",
                tag_name="v1.0.0",
                html_url="https://github.com/owner/rag/releases/v1",
                published_at="2026-01-01T00:00:00Z",
                prerelease=False,
                draft=False,
                body_preview=None,
            )
        ]

    async def search_issues(self, **kwargs):
        return (
            1,
            False,
            [
                GitHubIssueResult(
                    title="discussion",
                    html_url="https://github.com/owner/rag/issues/1",
                    repository_url="https://api.github.com/repos/owner/rag",
                    state="open",
                    comments=7,
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                    is_pull_request=False,
                    body_preview="discussion",
                )
            ],
        )

    async def close(self) -> None:
        return None


def _repo(owner: str, repo: str, *, stars: int) -> GitHubRepositoryResult:
    return GitHubRepositoryResult(
        full_name=f"{owner}/{repo}",
        html_url=f"https://github.com/{owner}/{repo}",
        description="RAG framework",
        language="Python",
        stars=stars,
        forks=100,
        open_issues=5,
        default_branch="main",
        updated_at="2026-01-01T00:00:00Z",
        pushed_at="2026-01-01T00:00:00Z",
        license_name="MIT",
        archived=False,
    )


def _open_source_profile(full_name: str):
    from chat.application.tools.services.software_ecosystem.open_source.hydration.models import (
        OpenSourceProjectProfile,
    )

    return OpenSourceProjectProfile(
        full_name=full_name,
        html_url=f"https://github.com/{full_name}",
        description="RAG framework",
        language="Python",
        stars=5000,
        forks=100,
        open_issues=5,
        license_name="MIT",
        archived=False,
        default_branch="main",
        updated_at="2026-01-01T00:00:00Z",
        pushed_at="2026-01-01T00:00:00Z",
        readme_preview="README",
        recent_releases=[],
        issue_discussion_count=0,
        maintenance_score=0.9,
        popularity_score=0.9,
        activity_score=0.9,
        relevance_score=0.0,
        evidence=["GitHub repository loaded"],
    )
