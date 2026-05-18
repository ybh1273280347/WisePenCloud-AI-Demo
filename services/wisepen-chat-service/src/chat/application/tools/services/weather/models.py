from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True, slots=True)
class GeoLocation:
    name: str
    latitude: float
    longitude: float
    country: Optional[str] = None
    admin1: Optional[str] = None
    timezone: Optional[str] = None


@dataclass(frozen=True, slots=True)
class CurrentWeather:
    time: str
    temperature_2m: Optional[float]
    relative_humidity_2m: Optional[float]
    apparent_temperature: Optional[float]
    precipitation: Optional[float]
    rain: Optional[float]
    showers: Optional[float]
    snowfall: Optional[float]
    weather_code: Optional[int]
    wind_speed_10m: Optional[float]
    wind_direction_10m: Optional[float]


@dataclass(frozen=True, slots=True)
class DailyWeather:
    date: str
    weather_code: Optional[int]
    temperature_2m_max: Optional[float]
    temperature_2m_min: Optional[float]
    precipitation_sum: Optional[float]
    precipitation_probability_max: Optional[float]
    wind_speed_10m_max: Optional[float]


@dataclass(frozen=True, slots=True)
class WeatherResult:
    location: GeoLocation
    timezone: str
    current: Optional[CurrentWeather]
    daily: List[DailyWeather]
