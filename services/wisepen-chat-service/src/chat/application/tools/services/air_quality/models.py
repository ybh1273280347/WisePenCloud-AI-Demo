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
class CurrentAirQuality:
    time: str
    european_aqi: Optional[float]
    us_aqi: Optional[float]
    pm10: Optional[float]
    pm2_5: Optional[float]
    carbon_monoxide: Optional[float]
    nitrogen_dioxide: Optional[float]
    sulphur_dioxide: Optional[float]
    ozone: Optional[float]
    uv_index: Optional[float]


@dataclass(frozen=True, slots=True)
class HourlyAirQuality:
    time: str
    european_aqi: Optional[float]
    us_aqi: Optional[float]
    pm10: Optional[float]
    pm2_5: Optional[float]
    uv_index: Optional[float]


@dataclass(frozen=True, slots=True)
class AirQualityResult:
    location: GeoLocation
    timezone: str
    current: Optional[CurrentAirQuality]
    hourly: List[HourlyAirQuality]
