from datetime import date, datetime

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

_DAILY_VARS = "temperature_2m_max,temperature_2m_min,precipitation_sum"

# Open-Meteo's free forecast covers 16 days; stay a little inside that so a
# same-day request doesn't fall on the boundary.
_FORECAST_HORIZON_DAYS = 15
_HISTORICAL_YEARS_TO_AVERAGE = 3


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def get_weather_outlook(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
) -> dict:
    """Gets a weather outlook for a destination and date range, for packing purposes.

    Uses Open-Meteo (no API key required). If the trip starts within about
    two weeks, returns a real forecast. Otherwise — the common case, since
    most trips are planned well ahead — forecasts don't exist yet that far
    out, so this averages the same calendar dates from the last 3 years via
    Open-Meteo's historical archive instead, as a "typical conditions"
    estimate. The status field always tells you which one you got; treat
    "historical_average" as a planning estimate, not a promise.

    Args:
        latitude: Destination latitude (e.g. from resolve_city).
        longitude: Destination longitude (e.g. from resolve_city).
        start_date: Trip start date, YYYY-MM-DD.
        end_date: Trip end date, YYYY-MM-DD.

    Returns:
        On success: {"status": "forecast" | "historical_average",
        "avg_high_c", "avg_low_c", "total_precipitation_mm", "note"}.
        On failure: {"status": "error", "error_message"}.
    """
    try:
        trip_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError as error:
        return {"status": "error", "error_message": f"Invalid start_date: {error}"}

    days_until_trip = (trip_start - date.today()).days

    if 0 <= days_until_trip <= _FORECAST_HORIZON_DAYS:
        return _get_forecast(latitude, longitude, start_date, end_date)
    return _get_historical_average(latitude, longitude, start_date, end_date)


def _get_forecast(latitude: float, longitude: float, start_date: str, end_date: str) -> dict:
    try:
        response = requests.get(
            FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "daily": _DAILY_VARS,
                "forecast_days": 16,
                "timezone": "auto",
            },
            timeout=15,
        )
        response.raise_for_status()
    except requests.HTTPError as error:
        return {"status": "error", "error_message": str(error)}

    daily = response.json().get("daily", {})
    highs, lows, precip = _filter_daily_range(daily, start_date, end_date)

    if not highs:
        return {"status": "error", "error_message": "No forecast data returned for the requested dates."}

    return {
        "status": "forecast",
        "avg_high_c": round(_avg(highs), 1),
        "avg_low_c": round(_avg(lows), 1),
        "total_precipitation_mm": round(sum(precip), 1),
        "note": "Real forecast for these dates.",
    }


def _get_historical_average(latitude: float, longitude: float, start_date: str, end_date: str) -> dict:
    trip_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    trip_end = datetime.strptime(end_date, "%Y-%m-%d").date()

    all_highs: list[float] = []
    all_lows: list[float] = []
    all_precip: list[float] = []
    years_used = []

    for years_back in range(1, _HISTORICAL_YEARS_TO_AVERAGE + 1):
        year = trip_start.year - years_back
        try:
            past_start = trip_start.replace(year=year)
            past_end = trip_end.replace(year=year)
        except ValueError:
            # Feb 29 in a non-leap year — shift back a day rather than skip the year.
            past_start = trip_start.replace(year=year, day=28) if trip_start.month == 2 else trip_start.replace(year=year)
            past_end = trip_end.replace(year=year, day=28) if trip_end.month == 2 else trip_end.replace(year=year)

        try:
            response = requests.get(
                ARCHIVE_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "start_date": past_start.isoformat(),
                    "end_date": past_end.isoformat(),
                    "daily": _DAILY_VARS,
                    "timezone": "auto",
                },
                timeout=15,
            )
            response.raise_for_status()
        except requests.HTTPError:
            continue  # Skip a year that fails rather than fail the whole outlook.

        daily = response.json().get("daily", {})
        highs, lows, precip = _filter_daily_range(daily, past_start.isoformat(), past_end.isoformat())
        if highs:
            all_highs.extend(highs)
            all_lows.extend(lows)
            all_precip.extend(precip)
            years_used.append(year)

    if not all_highs:
        return {"status": "error", "error_message": "No historical weather data available for this location."}

    return {
        "status": "historical_average",
        "avg_high_c": round(_avg(all_highs), 1),
        "avg_low_c": round(_avg(all_lows), 1),
        "total_precipitation_mm": round(_avg(all_precip) * len(all_precip) / max(len(years_used), 1), 1),
        "note": (
            f"No forecast exists this far out — this is a typical-conditions estimate "
            f"averaged from {', '.join(str(y) for y in years_used)} on the same calendar dates, "
            "not a real forecast."
        ),
    }


def _filter_daily_range(
    daily: dict, start_date: str, end_date: str
) -> tuple[list[float], list[float], list[float]]:
    """Extracts high/low/precipitation arrays for dates within [start_date, end_date]."""
    times = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])

    filtered_highs, filtered_lows, filtered_precip = [], [], []
    for i, day in enumerate(times):
        if start_date <= day <= end_date:
            if i < len(highs) and highs[i] is not None:
                filtered_highs.append(highs[i])
            if i < len(lows) and lows[i] is not None:
                filtered_lows.append(lows[i])
            if i < len(precip) and precip[i] is not None:
                filtered_precip.append(precip[i])

    return filtered_highs, filtered_lows, filtered_precip
