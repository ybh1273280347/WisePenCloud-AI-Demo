from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx

from chat.core.config.app_settings import settings
from common.logger import log_event

from .models import CurrentWeather, DailyWeather, GeoLocation, WeatherResult


class WeatherApiError(RuntimeError):
    pass


class OpenMeteoClient:
    def __init__(
        self,
        timeout: float = 10.0,
        *,
        geocoding_url: Optional[str] = None,
        forecast_url: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._timeout = timeout
        self._geocoding_url = (
            geocoding_url or settings.OPEN_METEO_GEOCODING_URL
        ).rstrip("/")
        self._forecast_url = (forecast_url or settings.OPEN_METEO_FORECAST_URL).rstrip(
            "/"
        )
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def geocode(self, location: str, language: str = "zh") -> GeoLocation:
        params = {
            "name": location,
            "count": 1,
            "language": language,
            "format": "json",
        }

        try:
            client = await self._get_client()
            response = await client.get(self._geocoding_url, params=params)
        except httpx.HTTPError as e:
            raise WeatherApiError(f"Open-Meteo geocoding request failed: {e}") from e

        if response.status_code >= 400:
            raise WeatherApiError(
                f"Open-Meteo geocoding failed: HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as e:
            raise WeatherApiError("Open-Meteo geocoding returned invalid JSON") from e

        results = payload.get("results") if isinstance(payload, dict) else None
        if not results:
            raise WeatherApiError(f"Location not found: {location}")

        item = results[0]
        return GeoLocation(
            name=str(item.get("name") or location),
            latitude=float(item["latitude"]),
            longitude=float(item["longitude"]),
            country=item.get("country"),
            admin1=item.get("admin1"),
            timezone=item.get("timezone"),
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
    ) -> WeatherResult:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "forecast_days": forecast_days,
            "current": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "precipitation",
                    "rain",
                    "showers",
                    "snowfall",
                    "weather_code",
                    "wind_speed_10m",
                    "wind_direction_10m",
                ]
            ),
            "daily": ",".join(
                [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_sum",
                    "precipitation_probability_max",
                    "wind_speed_10m_max",
                ]
            ),
        }

        try:
            client = await self._get_client()
            response = await client.get(self._forecast_url, params=params)
        except httpx.HTTPError as e:
            raise WeatherApiError(f"Open-Meteo forecast request failed: {e}") from e

        if response.status_code >= 400:
            raise WeatherApiError(
                f"Open-Meteo forecast failed: HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as e:
            raise WeatherApiError("Open-Meteo forecast returned invalid JSON") from e

        if not isinstance(payload, dict):
            raise WeatherApiError(
                f"Open-Meteo forecast returned invalid response type: {type(payload).__name__}"
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

        return WeatherResult(
            location=location,
            timezone=resolved_timezone,
            current=_parse_current(payload.get("current")),
            daily=_parse_daily(payload.get("daily") or {}),
        )

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("OpenMeteoClient 关闭", closed=client is not None)

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


def _parse_current(data: Optional[dict[str, Any]]) -> Optional[CurrentWeather]:
    if not data:
        return None

    return CurrentWeather(
        time=str(data.get("time") or ""),
        temperature_2m=_optional_float(data.get("temperature_2m")),
        relative_humidity_2m=_optional_float(data.get("relative_humidity_2m")),
        apparent_temperature=_optional_float(data.get("apparent_temperature")),
        precipitation=_optional_float(data.get("precipitation")),
        rain=_optional_float(data.get("rain")),
        showers=_optional_float(data.get("showers")),
        snowfall=_optional_float(data.get("snowfall")),
        weather_code=_optional_int(data.get("weather_code")),
        wind_speed_10m=_optional_float(data.get("wind_speed_10m")),
        wind_direction_10m=_optional_float(data.get("wind_direction_10m")),
    )


def _parse_daily(data: Dict[str, Any]) -> List[DailyWeather]:
    dates = data.get("time") or []
    result: List[DailyWeather] = []

    for index, date in enumerate(dates):
        result.append(
            DailyWeather(
                date=str(date),
                weather_code=_get_optional_int(data, "weather_code", index),
                temperature_2m_max=_get_optional_float(
                    data,
                    "temperature_2m_max",
                    index,
                ),
                temperature_2m_min=_get_optional_float(
                    data,
                    "temperature_2m_min",
                    index,
                ),
                precipitation_sum=_get_optional_float(
                    data,
                    "precipitation_sum",
                    index,
                ),
                precipitation_probability_max=_get_optional_float(
                    data,
                    "precipitation_probability_max",
                    index,
                ),
                wind_speed_10m_max=_get_optional_float(
                    data,
                    "wind_speed_10m_max",
                    index,
                ),
            )
        )

    return result


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    return int(value)


def _get_optional_float(
    data: Dict[str, Any],
    key: str,
    index: int,
) -> Optional[float]:
    values = data.get(key)
    if not isinstance(values, list) or index >= len(values):
        return None
    return _optional_float(values[index])


def _get_optional_int(data: Dict[str, Any], key: str, index: int) -> Optional[int]:
    values = data.get(key)
    if not isinstance(values, list) or index >= len(values):
        return None
    return _optional_int(values[index])
