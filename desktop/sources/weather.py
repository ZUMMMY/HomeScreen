"""Madison weather from Open-Meteo (free, no key)."""

from datetime import datetime

from ..common import get_json

API = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes -> (text, icon)
CODES = [
    ((0,), "Clear", "☀️"),
    ((1,), "Mostly clear", "🌤️"),
    ((2,), "Partly cloudy", "⛅"),
    ((3,), "Cloudy", "☁️"),
    ((45, 48), "Fog", "🌫️"),
    ((51, 53, 55, 56, 57), "Drizzle", "🌦️"),
    ((61, 63, 65, 66, 67, 80, 81, 82), "Rain", "🌧️"),
    ((71, 73, 75, 77, 85, 86), "Snow", "🌨️"),
    ((95, 96, 99), "Storms", "⛈️"),
]


def describe(code: int) -> tuple[str, str]:
    for codes, text, icon in CODES:
        if code in codes:
            return text, icon
    return "", ""


def fetch(cfg: dict, now: datetime) -> dict:
    loc = cfg.get("location", {})
    data = get_json(API, {
        "latitude": loc.get("lat", 43.0731),
        "longitude": loc.get("lon", -89.4012),
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunset",
        "temperature_unit": cfg.get("display", {}).get("units", "fahrenheit"),
        "wind_speed_unit": "mph",
        "timezone": cfg.get("timezone", "America/Chicago"),
        "forecast_days": 8,
    })
    cur = data["current"]
    text, icon = describe(cur["weather_code"])
    daily = data["daily"]
    days = []
    for i, day in enumerate(daily["time"]):
        dtext, dicon = describe(daily["weather_code"][i])
        days.append({
            "date": day,
            "hi": round(daily["temperature_2m_max"][i]),
            "lo": round(daily["temperature_2m_min"][i]),
            "rain": daily["precipitation_probability_max"][i],
            "text": dtext,
            "icon": dicon,
            "sunset": daily["sunset"][i],
        })
    return {
        "temp": round(cur["temperature_2m"]),
        "feels": round(cur["apparent_temperature"]),
        "wind": round(cur["wind_speed_10m"]),
        "text": text,
        "icon": icon,
        "unit": "°F" if cfg.get("display", {}).get("units", "fahrenheit") == "fahrenheit" else "°C",
        "days": days,
    }
