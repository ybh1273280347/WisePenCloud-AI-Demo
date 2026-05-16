from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx
from chat.application.weather import OpenMeteoClient as OpenMeteoGeocodeClient
from chat.core.config.app_settings import settings
from common.logger import log_event

from .models import AirQualityResult, CurrentAirQuality, GeoLocation, HourlyAirQuality


class AirQualityApiError(RuntimeError):
    pass


class OpenMeteoAirQualityClient:
    _CURRENT_FIELDS = (
        "european_aqi",
        "us_aqi",
        "pm10",
        "pm2_5",
        "carbon_monoxide",
        "nitrogen_dioxide",
        "sulphur_dioxide",
        "ozone",
        "uv_index",
    )
    _HOURLY_FIELDS = _CURRENT_FIELDS

    def __init__(
        self,
        timeout: float = 10.0,
        *,
        air_quality_url: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._timeout = timeout
        self._air_quality_url = (
            air_quality_url or settings.OPEN_METEO_AIR_QUALITY_URL
        ).rstrip("/")
        self._transport = transport
        self._geocode_client = OpenMeteoGeocodeClient(
            timeout=timeout,
            transport=transport,
        )
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def geocode(self, location: str, language: str = "zh") -> GeoLocation:
        try:
            geo = await self._geocode_client.geocode(
                location=location, language=language
            )
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise AirQualityApiError(str(e)) from e
            raise

        return GeoLocation(
            name=geo.name,
            latitude=geo.latitude,
            longitude=geo.longitude,
            country=geo.country,
            admin1=geo.admin1,
            timezone=geo.timezone,
        )

    async def forecast(
        self,
        *,
        latitude: float,
        longitude: float,
        location_name: str,
        country: Optional[str],
        admin1: Optional[str],
        timezone: str,
        forecast_days: int,
    ) -> AirQualityResult:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "forecast_days": forecast_days,
            "current": ",".join(self._CURRENT_FIELDS),
            "hourly": ",".join(self._HOURLY_FIELDS),
        }

        try:
            client = await self._get_client()
            response = await client.get(self._air_quality_url, params=params)
        except httpx.HTTPError as e:
            raise AirQualityApiError(
                f"Open-Meteo air quality request failed: {e}"
            ) from e

        if response.status_code >= 400:
            raise AirQualityApiError(
                f"Open-Meteo air quality failed: HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as e:
            raise AirQualityApiError(
                "Open-Meteo air quality returned invalid JSON"
            ) from e

        if not isinstance(payload, dict):
            raise AirQualityApiError(
                "Open-Meteo air quality returned invalid response type"
            )

        resolved_timezone = str(payload.get("timezone") or timezone)
        location = GeoLocation(
            name=location_name,
            latitude=latitude,
            longitude=longitude,
            country=country,
            admin1=admin1,
            timezone=resolved_timezone,
        )

        return AirQualityResult(
            location=location,
            timezone=resolved_timezone,
            current=_parse_current(payload.get("current")),
            hourly=_parse_hourly(payload.get("hourly") or {}),
        )

    async def close(self) -> None:
        await self._geocode_client.close()
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("OpenMeteoAirQualityClient 关闭", closed=client is not None)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                return self._client

            self._client = self._create_client()
            return self._client

    def _create_client(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(
            timeout=self._timeout,
            connect=min(3.0, self._timeout),
            read=self._timeout,
            write=min(3.0, self._timeout),
            pool=min(3.0, self._timeout),
        )
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)


def _parse_current(data: Optional[Dict[str, Any]]) -> Optional[CurrentAirQuality]:
    if not data:
        return None

    return CurrentAirQuality(
        time=str(data.get("time") or ""),
        european_aqi=_optional_float(data.get("european_aqi")),
        us_aqi=_optional_float(data.get("us_aqi")),
        pm10=_optional_float(data.get("pm10")),
        pm2_5=_optional_float(data.get("pm2_5")),
        carbon_monoxide=_optional_float(data.get("carbon_monoxide")),
        nitrogen_dioxide=_optional_float(data.get("nitrogen_dioxide")),
        sulphur_dioxide=_optional_float(data.get("sulphur_dioxide")),
        ozone=_optional_float(data.get("ozone")),
        uv_index=_optional_float(data.get("uv_index")),
    )


def _parse_hourly(data: Dict[str, Any]) -> List[HourlyAirQuality]:
    times = data.get("time") or []
    items: List[HourlyAirQuality] = []

    for index, time in enumerate(times):
        items.append(
            HourlyAirQuality(
                time=str(time),
                european_aqi=_get_optional_float(data, "european_aqi", index),
                us_aqi=_get_optional_float(data, "us_aqi", index),
                pm10=_get_optional_float(data, "pm10", index),
                pm2_5=_get_optional_float(data, "pm2_5", index),
                uv_index=_get_optional_float(data, "uv_index", index),
            )
        )

    return items


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _get_optional_float(data: Dict[str, Any], key: str, index: int) -> Optional[float]:
    values = data.get(key)
    if not isinstance(values, list) or index >= len(values):
        return None
    return _optional_float(values[index])
