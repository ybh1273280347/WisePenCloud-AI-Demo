from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Tuple

from ..cache.doi_hydration_cache import DOIHydrationCache
from ..models import DOIMetadataRecord
from .crossref_doi_resolver import CrossrefDOIResolver
from .datacite_doi_resolver import DataCiteDOIResolver
from .doi_content_negotiation_resolver import DOIContentNegotiationResolver


class DOIHydrationRouter:
    def __init__(
        self,
        crossref_resolver: CrossrefDOIResolver,
        datacite_resolver: DataCiteDOIResolver,
        content_negotiation_resolver: DOIContentNegotiationResolver,
        cache: DOIHydrationCache,
        *,
        max_concurrency: int,
    ) -> None:
        self._crossref_resolver = crossref_resolver
        self._datacite_resolver = datacite_resolver
        self._content_negotiation_resolver = content_negotiation_resolver
        self._cache = cache
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def hydrate_many(
        self,
        dois: List[str],
    ) -> Tuple[Dict[str, DOIMetadataRecord], Dict[str, str]]:
        unique_dois = sorted(set(dois))
        tasks = [
            asyncio.create_task(self._hydrate_one_with_cache(doi))
            for doi in unique_dois
        ]

        records: Dict[str, DOIMetadataRecord] = {}
        failures: Dict[str, str] = {}

        for task in asyncio.as_completed(tasks):
            doi, record, error_code = await task
            if record is not None:
                records[doi] = record
            else:
                failures[doi] = error_code

        return records, failures

    async def _hydrate_one_with_cache(
        self,
        doi: str,
    ) -> Tuple[str, Optional[DOIMetadataRecord], str]:
        cached = self._cache.get(doi)
        if cached is not None:
            return doi, cached, ""

        negative = self._cache.get_negative(doi)
        if negative is not None:
            return doi, None, negative

        async with self._semaphore:
            record, error_code = await self._hydrate_one_serial_fallback(doi)

        if record is not None:
            self._cache.set(doi, record)
        else:
            self._cache.set_negative(doi, error_code)

        return doi, record, error_code

    async def _hydrate_one_serial_fallback(
        self,
        doi: str,
    ) -> Tuple[Optional[DOIMetadataRecord], str]:
        record = await self._crossref_resolver.resolve(doi)
        if record is not None:
            return record, ""

        record = await self._datacite_resolver.resolve(doi)
        if record is not None:
            return record, ""

        record = await self._content_negotiation_resolver.resolve(doi)
        if record is not None:
            return record, ""

        return None, "doi_metadata_unresolved"
