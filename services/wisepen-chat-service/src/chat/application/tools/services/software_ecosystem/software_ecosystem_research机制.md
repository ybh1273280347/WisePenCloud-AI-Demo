# `software_ecosystem_research` 工具机制说明

## 1. 工具定位

`software_ecosystem_research` 是软件生态调研工具，用于围绕一个技术问题同时检索和分析三类同级实体：

```text
open_source_project
package
community_discussion
```

它不从自然语言 query 中猜测意图，后端只根据结构化入参 `targets`、`ecosystems`、`languages`、`sort`、`min_stars`、`package_hydration_depth` 执行检索、水合和排序。

## 2. 工具入参

```text
query: str
targets: List[open_source_project | package | community_discussion]
ecosystems: Optional[List[npm | pypi]]
languages: Optional[List[str]]
sort: relevance | stars | recent_activity | maintenance | popularity
limit: int
min_stars: Optional[int]
package_hydration_depth: light | standard | deep
```

参数语义：

```text
query:
软件生态调研问题。

targets:
本次要检索的实体类型。三类实体可单独查，也可组合查。

ecosystems:
仅 package target 生效。当前支持 npm、pypi。

languages:
仅 open_source_project target 生效，用于 GitHub repository search 的 language 过滤。

sort:
控制统一候选排序倾向。

limit:
每类召回和最终输出限制，必须是整数。

min_stars:
仅 open_source_project target 生效。
schema 描述为：
Minimum GitHub stars for open-source project search. Only applies when targets includes open_source_project. Suggested model defaults: null for general project discovery, 100 for mature projects, 500 for high-star projects, and 5000 for top/headline projects.

package_hydration_depth:
仅 package target 生效。targets 不包含 package 时可以传 light，后端不会执行 package hydration。
```

严格校验规则：

```text
不把字符串 bool 转成 bool。
不把字符串数字转成 int。
不把非法 enum 静默修正。
不从 query 文本推断 target。
```

## 3. 顶层编排流程

入口：

```text
software_ecosystem/research/service.py
SoftwareEcosystemResearchService.research
```

主流程：

```text
1. validate structured inputs
2. 按 targets 并发调度 discovery service
3. 将各类 discovery 结果映射为统一 SoftwareEcosystemCandidate
4. 对统一候选池去重和排序
5. 对 open_source_project 候选执行 GitHub 水合
6. 对 package 候选按 package_hydration_depth 执行包水合
7. community_discussion 不做重水合，直接返回
8. 组装 SoftwareEcosystemResearchResult
```

## 4. 三类实体 Discovery

### 4.1 Open Source Project

路径：

```text
software_ecosystem/open_source/discovery/service.py
```

数据源：

```text
GitHub repository search
```

机制：

```text
1. 根据 query 构造 GitHub 搜索语句。
2. languages 不为空时追加 language:{language}。
3. min_stars 不为空时追加 stars:>={min_stars}。
4. 根据 sort 选择 GitHub search sort：
   - stars / popularity -> stars
   - recent_activity / maintenance -> updated
   - relevance -> GitHub best match
5. 映射为 OpenSourceProjectCandidate。
6. 按 full_name 去重。
```

候选主要字段：

```text
full_name
html_url
description
language
stars
forks
open_issues
default_branch
updated_at
pushed_at
license_name
archived
```

### 4.2 Package

路径：

```text
software_ecosystem/packages/discovery/service.py
```

数据源：

```text
ecosyste.ms packages
npm registry search
GitHub repository search
```

机制：

```text
1. 按 ecosystem 召回包候选。
2. npm 额外使用 npm registry search。
3. GitHub repository search 用作补充召回，按语言推断 npm / pypi。
4. 映射为 PackageCandidate。
5. 按 pkg:{ecosystem}:{normalized_name} 去重。
6. 使用 package candidate ranking 排序。
```

候选主要字段：

```text
ecosystem
name
normalized_name
summary
repository_url
homepage_url
source
raw_score
```

### 4.3 Community Discussion

路径：

```text
software_ecosystem/community/service.py
```

数据源：

```text
Hacker News Algolia
```

机制：

```text
1. 用 query 查询 HN Algolia story。
2. 映射为 CommunityDiscussionSignal。
3. 用 title BM25、points、comments_count、recency 排序。
4. 在顶层作为 community_discussion 类型候选参与统一排序。
```

字段：

```text
source
title
url
published_at
points
comments_count
summary
```

## 5. 统一候选模型

路径：

```text
software_ecosystem/research/models.py
SoftwareEcosystemCandidate
```

统一候选字段：

```text
id
candidate_type
source
title
url
summary
ecosystem
package_name
repository
language
raw_score
matched_terms
metrics
```

ID 规则：

```text
open_source_project:
repo:github:{owner}/{repo}

package:
pkg:{ecosystem}:{normalized_name}

community_discussion:
community:{source}:{stable_hash(url)}
```

映射位置：

```text
software_ecosystem/research/mapper.py
```

## 6. 统一 Ranking

路径：

```text
software_ecosystem/research/ranking.py
```

统一排序函数：

```text
rank_software_ecosystem_candidates
```

排序信号：

```text
metadata_bm25
source_original
target_priority
popularity
maintenance
recent_activity
community_attention
```

sort 行为：

```text
relevance:
提高 metadata_bm25 权重。

stars:
提高 open_source_project.metrics.stars 权重。

recent_activity:
提高 pushed_at、updated_at、published_at 等近期活跃信号。

maintenance:
提高 maintenance 信号，并降低 archived / stale 候选。

popularity:
提高 stars、forks、package raw_score、community points/comments 等热度信号。
```

公共算法依赖：

```python
from chat.application.algorithms.ranking import (
    FieldedDocument,
    RankedList,
    rank_fielded_bm25,
    weighted_rrf,
)
```

公共 ranking 算法仍保留在 `chat.application.algorithms.ranking`，`software_ecosystem` 只定义业务字段权重、业务 scorer 和组合策略。

## 7. Open Source Project 水合

路径：

```text
software_ecosystem/open_source/hydration/service.py
```

水合服务：

```text
OpenSourceProjectHydrationService.hydrate
```

调用内容：

```text
get_repository:
始终调用，水合仓库基本画像。

get_readme:
include_readme=True 时调用，保留 readme_preview。

get_releases:
include_releases=True 时调用，保留最近 release tag。

search_issues:
include_issues=True 时调用，查询 repo:{owner}/{repo} is:issue，按 comments 排序，统计 issue_discussion_count。
```

顶层 research 的默认策略：

```text
include_readme = true
include_releases = sort in ["recent_activity", "maintenance"]
include_issues = sort in ["recent_activity", "maintenance"]
```

输出模型：

```text
OpenSourceProjectProfile
```

主要字段：

```text
full_name
html_url
description
language
stars
forks
open_issues
license_name
archived
default_branch
updated_at
pushed_at
readme_preview
recent_releases
issue_discussion_count
maintenance_score
popularity_score
activity_score
relevance_score
evidence
```

## 8. Package 水合深度

路径：

```text
software_ecosystem/packages/hydration/service.py
```

入参：

```text
package_hydration_depth: light | standard | deep
```

### 8.1 light

只做基础 package metadata hydration：

```text
1. deps.dev get_package
2. 提取 versions
3. 选择 selected_version
4. 计算 latest_version / recent_versions
```

不会调用：

```text
deps.dev get_version
deps.dev requirements
deps.dev dependencies
npm / PyPI registry
GitHub repository metadata
```

### 8.2 standard

在 light 基础上额外水合：

```text
1. deps.dev get_version
2. registry metadata
   - npm registry
   - PyPI registry
3. recent versions
4. requirements_count
5. 从 package metadata 中提取 GitHub repo URL，并补充 GitHub repository metadata
```

不会调用：

```text
deps.dev dependency graph
```

### 8.3 deep

在 standard 基础上额外水合：

```text
deps.dev dependency graph
```

用于输出：

```text
direct_dependencies_count
transitive_dependencies_count
dependency_complexity_score
```

输出模型：

```text
PackageProfile
```

## 9. Community Discussion 输出

Community discussion 不做二次重水合。

顶层流程中：

```text
1. discovery 阶段已经拿到 CommunityDiscussionSignal。
2. 映射成 SoftwareEcosystemCandidate 参与统一 ranking。
3. 最终从 ranked candidate 中还原为 CommunityDiscussionSignal 返回。
```

## 10. 最终输出

路径：

```text
software_ecosystem/research/models.py
SoftwareEcosystemResearchResult
```

字段：

```text
query
targets
recommended_projects
recommended_packages
community_discussions
summary
recommendations
caveats
evidence
```

语义：

```text
recommended_projects:
open_source_project 水合结果。

recommended_packages:
package 水合结果。

community_discussions:
community_discussion 结果。

summary:
整体命中摘要。

recommendations:
按实体类型生成的建议摘要。

caveats:
provider 失败、deprecated、archived、advisory 等风险提示。

evidence:
关键水合证据。
```

## 11. 缓存

当前缓存包括：

```text
open_source_project_query_cache
open_source_project_profile_cache
package_profile_cache
latest_pointer_cache
community_query_cache
software_ecosystem_candidate_cache
```

缓存 key 根据 query、target、sort、limit、ecosystem、package、repo 等结构化字段生成。

## 12. 生命周期与注册

工具注册位置：

```text
chat/container_providers/tools.py
software_ecosystem_research_tool
```

关闭资源位置：

```text
chat/main.py
SoftwareEcosystemResearchTool.close
```

旧工具入口已删除：

```text
github_search
package_intelligence
code_search Python package
```

