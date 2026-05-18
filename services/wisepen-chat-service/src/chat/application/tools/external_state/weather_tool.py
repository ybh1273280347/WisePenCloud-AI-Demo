from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.tools.config import DEFAULT_TOOL_TIMEZONE
from chat.application.tools.services.weather import OpenMeteoClient, WeatherApiError, WeatherResult
from chat.application.tools.services.weather.weather_code import describe_weather_code
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error, log_event

_TOOL_DESCRIPTION = (
    "Gets current weather and short-term forecast from Open-Meteo. "
    "Use this tool for weather, temperature, rain, snow, wind, humidity, forecast, "
    "umbrella, clothing, travel, or outdoor-condition questions.\n\n"
    "Forecast dates and times use Beijing time, Asia/Shanghai. "
    "Input can be a location name or latitude/longitude coordinates.\n\n"
    "This tool provides real external weather state. Do not answer weather questions from model memory."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "location": {
            "type": "string",
            "description": (
                "City, region, or place name, such as 上海, Beijing, Tokyo, San Francisco."
            ),
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
            "maximum": 7,
            "default": 3,
            "description": "Number of forecast days to return.",
        },
    },
    "additionalProperties": False,
}


class WeatherTool(BaseTool):
    def __init__(self, client: Optional[OpenMeteoClient] = None) -> None:
        self._client = client or OpenMeteoClient()

    @property
    def name(self) -> str:
        return "weather"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        forecast_days = _coerce_forecast_days(kwargs.get("forecast_days", 3))
        if forecast_days is None or forecast_days < 1 or forecast_days > 7:
            return "[Tool Error] forecast_days must be between 1 and 7."

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
        except WeatherApiError as e:
            log_error(
                "weather tool",
                e,
                location=location,
                latitude=latitude,
                longitude=longitude,
            )
            if str(e).startswith("Location not found:"):
                return "[Tool Error] Location not found."
            return f"[Tool Error] Weather API request failed: {e}"
        except Exception as e:
            log_error(
                "weather tool",
                e,
                location=location,
                latitude=latitude,
                longitude=longitude,
            )
            return f"[Tool Error] Weather tool failed: {e}"

        log_event(
            "weather tool fetched forecast",
            location=result.location.name,
            latitude=result.location.latitude,
            longitude=result.location.longitude,
            timezone=result.timezone,
            forecast_days=forecast_days,
        )

        return _format_weather_result(result)

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


def _format_weather_result(result: WeatherResult) -> str:
    lines = [
        "[Tool Result] weather",
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
                "Current weather:",
                f"- time: {current.time}",
                f"- condition: {describe_weather_code(current.weather_code)}",
                f"- temperature_2m: {_format_unit(current.temperature_2m, '°C')}",
                f"- apparent_temperature: {_format_unit(current.apparent_temperature, '°C')}",
                f"- relative_humidity_2m: {_format_unit(current.relative_humidity_2m, '%')}",
                f"- precipitation: {_format_unit(current.precipitation, 'mm')}",
                f"- rain: {_format_unit(current.rain, 'mm')}",
                f"- showers: {_format_unit(current.showers, 'mm')}",
                f"- snowfall: {_format_unit(current.snowfall, 'cm')}",
                f"- wind_speed_10m: {_format_unit(current.wind_speed_10m, 'km/h')}",
                f"- wind_direction_10m: {_format_unit(current.wind_direction_10m, '°')}",
            ]
        )
    else:
        lines.extend(["", "Current weather:", "- none"])

    lines.append("")
    lines.append("Daily forecast:")
    if result.daily:
        for day in result.daily:
            lines.extend(
                [
                    f"- date: {day.date}",
                    f"  condition: {describe_weather_code(day.weather_code)}",
                    f"  temperature: {_format_unit(day.temperature_2m_min, '°C')} ~ {_format_unit(day.temperature_2m_max, '°C')}",
                    f"  precipitation_probability_max: {_format_unit(day.precipitation_probability_max, '%')}",
                    f"  precipitation_sum: {_format_unit(day.precipitation_sum, 'mm')}",
                    f"  wind_speed_10m_max: {_format_unit(day.wind_speed_10m_max, 'km/h')}",
                ]
            )
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "Assistant instructions:",
            "- Treat this as the authoritative weather state.",
            "- Do not answer weather questions from memory.",
            "- Mention the location and forecast time.",
            "- For umbrella/rain questions, use condition, precipitation, and precipitation probability.",
            "- For clothing questions, use temperature, apparent temperature, wind, and precipitation.",
        ]
    )

    return "\n".join(lines)


def _format_unit(value: Optional[float], unit: str) -> str:
    if value is None:
        return "unknown"
    return f"{value:g}{unit}"
