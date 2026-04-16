from __future__ import annotations

import asyncio

import calendar
import math
import time
from datetime import date, datetime, timezone
from statistics import mean
from typing import Any

import httpx

_CACHE_TTL_SECONDS = 3 * 60 * 60
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        if math.isnan(number):
            return None
        return number
    except Exception:
        return None


def _is_italy(lat: float, lon: float) -> bool:
    return 35.5 <= lat <= 47.1 and 6.6 <= lon <= 18.5


def _parse_event_date(event_date: str) -> datetime:
    raw = (event_date or "").strip()
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _monthly_bounds(base_dt: datetime) -> tuple[date, date]:
    month = base_dt.month
    start_year = base_dt.year - 10
    end_year = base_dt.year - 1
    start = date(start_year, month, 1)
    end_day = calendar.monthrange(end_year, month)[1]
    end = date(end_year, month, end_day)
    return start, end


def _cache_get(key: str) -> dict[str, Any] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, payload = hit
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_set(key: str, payload: dict[str, Any]) -> None:
    _CACHE[key] = (time.time(), payload)


async def _fetch_json(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def _compute_current_week(payload: dict[str, Any] | None) -> dict[str, Any]:
    daily = (payload or {}).get("daily") or {}
    days = daily.get("time") or []
    temp_max = [_safe_float(v) for v in (daily.get("temperature_2m_max") or [])]
    temp_min = [_safe_float(v) for v in (daily.get("temperature_2m_min") or [])]
    precip = [_safe_float(v) for v in (daily.get("precipitation_sum") or [])]
    wind = [_safe_float(v) for v in (daily.get("windspeed_10m_max") or [])]

    temp_max_v = [v for v in temp_max if v is not None]
    temp_min_v = [v for v in temp_min if v is not None]
    precip_v = [v for v in precip if v is not None]
    wind_v = [v for v in wind if v is not None]

    daily_precipitation = []
    for idx, day in enumerate(days):
        p = precip[idx] if idx < len(precip) else None
        daily_precipitation.append({"date": day, "precipitation": round(p, 2) if p is not None else None})

    return {
        "temp_max": round(mean(temp_max_v), 2) if temp_max_v else None,
        "temp_min": round(mean(temp_min_v), 2) if temp_min_v else None,
        "precipitation": round(sum(precip_v), 2) if precip_v else None,
        "windspeed_max": round(mean(wind_v), 2) if wind_v else None,
        "days": len(days),
        "daily_precipitation": daily_precipitation,
    }


def _compute_historical(payload: dict[str, Any] | None, event_month: int) -> dict[str, Any]:
    daily = (payload or {}).get("daily") or {}
    times = daily.get("time") or []
    temp_max = [_safe_float(v) for v in (daily.get("temperature_2m_max") or [])]
    temp_min = [_safe_float(v) for v in (daily.get("temperature_2m_min") or [])]
    precip = [_safe_float(v) for v in (daily.get("precipitation_sum") or [])]
    wind = [_safe_float(v) for v in (daily.get("windspeed_10m_max") or [])]

    temp_max_v = [v for v in temp_max if v is not None]
    temp_min_v = [v for v in temp_min if v is not None]
    wind_v = [v for v in wind if v is not None]
    precip_v = [v for v in precip if v is not None]

    monthly_precip_by_year: dict[int, float] = {}
    for idx, d in enumerate(times):
        p = precip[idx] if idx < len(precip) else None
        if p is None:
            continue
        try:
            dt = datetime.fromisoformat(d)
        except Exception:
            continue
        if dt.month != event_month:
            continue
        y = dt.year
        monthly_precip_by_year[y] = monthly_precip_by_year.get(y, 0.0) + p

    monthly_totals = list(monthly_precip_by_year.values())

    return {
        "avg_temp_max": round(mean(temp_max_v), 2) if temp_max_v else None,
        "avg_temp_min": round(mean(temp_min_v), 2) if temp_min_v else None,
        "avg_precipitation": round(mean(monthly_totals), 2) if monthly_totals else None,
        "avg_windspeed_max": round(mean(wind_v), 2) if wind_v else None,
        "record_temp_max": round(max(temp_max_v), 2) if temp_max_v else None,
        "record_precipitation": round(max(precip_v), 2) if precip_v else None,
        "years_count": len(monthly_totals),
    }


async def _compute_meteostat_station(lat: float, lon: float, base_dt: datetime) -> dict[str, Any] | None:
    if not _is_italy(lat, lon):
        return None

    try:
        from meteostat import Monthly, Point, Stations  # type: ignore
    except Exception:
        return None

    try:
        stations = Stations().nearby(lat, lon)
        nearest = stations.fetch(1)
        station_name = None
        station_distance_km = None
        if nearest is not None and not nearest.empty:
            row = nearest.iloc[0]
            station_name = row.get("name")
            slat = _safe_float(row.get("latitude"))
            slon = _safe_float(row.get("longitude"))
            if slat is not None and slon is not None:
                # rough km approximation, enough for UI context
                station_distance_km = round(math.dist([lat, lon], [slat, slon]) * 111.0, 1)

        month_start = date(base_dt.year - 10, base_dt.month, 1)
        month_end = date(base_dt.year - 1, base_dt.month, calendar.monthrange(base_dt.year - 1, base_dt.month)[1])

        point = Point(lat, lon)
        monthly_df = Monthly(point, month_start, month_end).fetch()
        if monthly_df is None or monthly_df.empty:
            return {
                "name": station_name,
                "distance_km": station_distance_km,
                "monthly_normals": {
                    "temp_max": None,
                    "temp_min": None,
                    "precipitation": None,
                    "windspeed_max": None,
                },
            }

        tmax_v = [float(v) for v in monthly_df["tmax"].dropna().tolist()] if "tmax" in monthly_df else []
        tmin_v = [float(v) for v in monthly_df["tmin"].dropna().tolist()] if "tmin" in monthly_df else []
        precip_v = [float(v) for v in monthly_df["prcp"].dropna().tolist()] if "prcp" in monthly_df else []
        wind_v = [float(v) for v in monthly_df["wspd"].dropna().tolist()] if "wspd" in monthly_df else []

        return {
            "name": station_name,
            "distance_km": station_distance_km,
            "monthly_normals": {
                "temp_max": round(mean(tmax_v), 2) if tmax_v else None,
                "temp_min": round(mean(tmin_v), 2) if tmin_v else None,
                "precipitation": round(mean(precip_v), 2) if precip_v else None,
                "windspeed_max": round(mean(wind_v), 2) if wind_v else None,
            },
        }
    except Exception:
        return None


async def get_climate_context(lat: float, lon: float, event_date: str, category: str) -> dict[str, Any]:
    base_dt = _parse_event_date(event_date)
    key = f"{lat:.2f}_{lon:.2f}_{event_date[:7]}"

    cached = _cache_get(key)
    if cached is not None:
        return cached

    start_date, end_date = _monthly_bounds(base_dt)
    is_italy = _is_italy(lat, lon)

    forecast_params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max",
        "past_days": 7,
        "forecast_days": 1,
        "timezone": "Europe/Rome",
    }
    archive_params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max",
        "timezone": "Europe/Rome",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        current_payload, historical_payload = await asyncio.gather(
            _fetch_json(client, FORECAST_URL, forecast_params),
            _fetch_json(client, ARCHIVE_URL, archive_params),
        )

    current_week = _compute_current_week(current_payload)
    historical_avg = _compute_historical(historical_payload, base_dt.month)

    anomaly_temp = None
    if current_week.get("temp_max") is not None and historical_avg.get("avg_temp_max") is not None:
        anomaly_temp = round(float(current_week["temp_max"]) - float(historical_avg["avg_temp_max"]), 2)

    anomaly_precip = None
    if current_week.get("precipitation") is not None and historical_avg.get("avg_precipitation") is not None:
        anomaly_precip = round(float(current_week["precipitation"]) - float(historical_avg["avg_precipitation"]), 2)

    meteostat_station = await _compute_meteostat_station(lat, lon, base_dt) if is_italy else None

    result = {
        "location": {"lat": lat, "lon": lon},
        "category": category,
        "is_italy": is_italy,
        "current_week": current_week,
        "historical_avg": historical_avg,
        "anomalies": {
            "anomaly_temp": anomaly_temp,
            "anomaly_precip": anomaly_precip,
        },
        "meteostat_station": meteostat_station,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    _cache_set(key, result)
    return result
