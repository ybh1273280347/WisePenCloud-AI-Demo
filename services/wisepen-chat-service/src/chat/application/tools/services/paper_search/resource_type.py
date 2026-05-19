from __future__ import annotations

from typing import Optional

from .models import PaperResultType, ScholarlyResourceType


def normalize_resource_type(value: Optional[str]) -> ScholarlyResourceType:
    if not value:
        return ScholarlyResourceType.UNKNOWN

    text = value.strip().lower().replace("-", "_").replace(" ", "_")

    if text in {"journal_article", "article_journal", "article"}:
        return ScholarlyResourceType.JOURNAL_ARTICLE
    if text in {"proceedings_article", "paper_conference", "conference_paper"}:
        return ScholarlyResourceType.PROCEEDINGS_ARTICLE
    if text in {"posted_content", "preprint"}:
        return ScholarlyResourceType.PREPRINT
    if text in {"book_chapter", "chapter"}:
        return ScholarlyResourceType.BOOK_CHAPTER
    if text in {"dataset", "data"}:
        return ScholarlyResourceType.DATASET
    if text == "software":
        return ScholarlyResourceType.SOFTWARE
    if text in {"report", "report_component"}:
        return ScholarlyResourceType.REPORT
    if text in {"dissertation", "thesis"}:
        return ScholarlyResourceType.THESIS

    return ScholarlyResourceType.UNKNOWN


def result_type_from_resource(resource_type: ScholarlyResourceType) -> PaperResultType:
    if resource_type in {
        ScholarlyResourceType.JOURNAL_ARTICLE,
        ScholarlyResourceType.PROCEEDINGS_ARTICLE,
        ScholarlyResourceType.PREPRINT,
        ScholarlyResourceType.BOOK_CHAPTER,
        ScholarlyResourceType.THESIS,
    }:
        return PaperResultType.PAPER

    if resource_type in {
        ScholarlyResourceType.DATASET,
        ScholarlyResourceType.SOFTWARE,
        ScholarlyResourceType.REPORT,
    }:
        return PaperResultType.SCHOLARLY_RESOURCE

    return PaperResultType.RESEARCH_PAPER_CANDIDATE
