from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.air_quality import (
    AirQualityApiError,
    AirQualityResult,
    OpenMeteoAirQualityClient,
)
from chat.application.tools.config import DEFAULT_TOOL_TIMEZONE
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error, log_event

_TOOL_DESCRIPTION = (
    "Gets current and short-term air quality from Open-Meteo. "
    "Use this tool for PM2.5, PM10, AQI, pollution, air quality, outdoor exercise, "
    "window opening, sensitive groups, pollen, UV, or whether it is suitable to stay outdoors.\n\n"
    "Forecast dates and times use Beijing time, Asia/Shanghai. "
    "Input can be a location name or latitude/longitude coordinates.\n\n"
    "This tool provides real external air-quality state. Do not answer air-quality questions from model memory."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "location": {
            "type": "string",
            "description": "City, region, or place name, such as 上海, 北京, Tokyo, San Francisco.",
        },
        "latitude": {
            "type": "number",
            "description": "Latitude. Must be used with longitude.",
        },
        "longitude": {
            "type": "number",
            "description": "Longitude. Must be used with latitude.",
        },
        "forecast_days": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "default": 1,
            "description": "Number of forecast days to return.",
        },
    },
    "additionalProperties": False,
}


class AirQualityTool(BaseTool):
    def __init__(self, client: Optional[OpenMeteoAirQualityClient] = None) -> None:
        self._client = client or OpenMeteoAirQualityClient()

    @property
    def name(self) -> str:
        return "air_quality"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        forecast_days = _coerce_forecast_days(kwargs.get("forecast_days", 1))
        if forecast_days is None or forecast_days < 1 or forecast_days > 5:
            return "[Tool Error] forecast_days must be between 1 and 5."

        location = kwargs.get("location")
        latitude = kwargs.get("latitude")
        longitude = kwargs.get("longitude")

        has_location = isinstance(location, str) and bool(location.strip())
        has_latitude = latitude is not None
        has_longitude = longitude is not None
        has_coordinates = has_latitude and has_longitude

        if has_latitude != has_longitude:
            return "[Tool Error] latitude and longitude must be provided together."

        if not has_location and not has_coordinates:
            return "[Tool Error] Provide either location or latitude + longitude."

        try:
            if has_coordinates:
                lat = float(latitude)
                lon = float(longitude)
                location_name = str(location).strip() if has_location else "coordinates"
                country = None
                admin1 = None
            else:
                geo = await self._client.geocode(str(location).strip())
                lat = geo.latitude
                lon = geo.longitude
                location_name = geo.name
                country = geo.country
                admin1 = geo.admin1

            timezone = DEFAULT_TOOL_TIMEZONE
            result = await self._client.forecast(
                latitude=lat,
                longitude=lon,
                location_name=location_name,
                country=country,
                admin1=admin1,
                timezone=str(timezone),
                forecast_days=forecast_days,
            )
        except ValueError:
            return "[Tool Error] latitude and longitude must be valid numbers."
        except AirQualityApiError as e:
            log_error(
                "air_quality tool",
                e,
                location=location,
                latitude=latitude,
                longitude=longitude,
            )
            if str(e).startswith("Location not found:"):
                return "[Tool Error] Location not found."
            return f"[Tool Error] Air quality API request failed: {e}"
        except Exception as e:
            log_error(
                "air_quality tool",
                e,
                location=location,
                latitude=latitude,
                longitude=longitude,
            )
            return f"[Tool Error] Air quality tool failed: {e}"

        log_event(
            "air_quality tool fetched forecast",
            location=result.location.name,
            latitude=result.location.latitude,
            longitude=result.location.longitude,
            timezone=result.timezone,
            forecast_days=forecast_days,
        )
        return _format_air_quality_result(result, forecast_days=forecast_days)

    async def close(self) -> None:
        await self._client.close()


def _coerce_forecast_days(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_air_quality_result(result: AirQualityResult, *, forecast_days: int) -> str:
    lines = [
        "[Tool Result] air_quality",
        "",
        "Location:",
        f"- name: {result.location.name}",
        f"- country: {result.location.country or 'unknown'}",
        f"- admin1: {result.location.admin1 or 'unknown'}",
        f"- latitude: {result.location.latitude}",
        f"- longitude: {result.location.longitude}",
        f"- timezone: {result.timezone}",
    ]

    if result.current is not None:
        current = result.current
        lines.extend(
            [
                "",
                "Current air quality:",
                f"- time: {current.time}",
                f"- european_aqi: {_format_number(current.european_aqi)}",
                f"- us_aqi: {_format_number(current.us_aqi)}",
                f"- pm2_5: {_format_unit(current.pm2_5, 'μg/m³')}",
                f"- pm10: {_format_unit(current.pm10, 'μg/m³')}",
                f"- ozone: {_format_unit(current.ozone, 'μg/m³')}",
                f"- nitrogen_dioxide: {_format_unit(current.nitrogen_dioxide, 'μg/m³')}",
                f"- sulphur_dioxide: {_format_unit(current.sulphur_dioxide, 'μg/m³')}",
                f"- carbon_monoxide: {_format_unit(current.carbon_monoxide, 'μg/m³')}",
                f"- uv_index: {_format_number(current.uv_index)}",
            ]
        )
    else:
        lines.extend(["", "Current air quality:", "- none"])

    max_entries = min(len(result.hourly), forecast_days * 24)
    lines.extend(["", "Hourly forecast:"])
    if max_entries > 0:
        for item in result.hourly[:max_entries]:
            lines.extend(
                [
                    f"- time: {item.time}",
                    f"  european_aqi: {_format_number(item.european_aqi)}",
                    f"  us_aqi: {_format_number(item.us_aqi)}",
                    f"  pm2_5: {_format_unit(item.pm2_5, 'μg/m³')}",
                    f"  pm10: {_format_unit(item.pm10, 'μg/m³')}",
                    f"  uv_index: {_format_number(item.uv_index)}",
                ]
            )
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "Assistant instructions:",
            "- Treat this as the authoritative external air-quality state.",
            "- Do not answer air-quality questions from memory.",
            "- For outdoor exercise, consider AQI, PM2.5, PM10, ozone, and UV index.",
            "- For sensitive groups, be conservative when AQI or PM2.5 is elevated.",
            "- Mention the location and observation time.",
        ]
    )

    return "\n".join(lines)


def _format_number(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    return f"{value:g}"


def _format_unit(value: Optional[float], unit: str) -> str:
    if value is None:
        return "unknown"
    return f"{value:g}{unit}"
