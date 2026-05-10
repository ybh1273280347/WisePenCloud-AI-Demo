from typing import Optional

from playwright.async_api import Page

from .protocol import InterventionSignal

class UserInterventionDetector:
    async def detect(self, page: Page) -> Optional[InterventionSignal]:
        url = page.url.lower()

        for indicator in AUTH_PAGE_INDICATORS:
            if indicator in url:
                return InterventionSignal(
                    type="auth_page",
                    confidence=0.9,
                    reason=f"URL contains auth indicator: {indicator}",
                    evidence={"url": page.url},
                )

        return None