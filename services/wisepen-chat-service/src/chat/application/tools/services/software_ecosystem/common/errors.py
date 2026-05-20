from __future__ import annotations

from typing import Mapping, Optional


class SoftwareEcosystemError(RuntimeError):
    pass


class UnsupportedEcosystemError(SoftwareEcosystemError):
    pass


class PackageNotFoundError(SoftwareEcosystemError):
    pass


class PackageVersionNotFoundError(SoftwareEcosystemError):
    pass


class ProviderUnavailableError(SoftwareEcosystemError):
    pass


class ProviderSchemaError(SoftwareEcosystemError):
    pass


class InvalidSoftwareEcosystemQueryError(SoftwareEcosystemError):
    pass


class SoftwareEcosystemHttpError(ProviderUnavailableError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        headers: Optional[Mapping[str, str]] = None,
        body_preview: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.body_preview = body_preview

