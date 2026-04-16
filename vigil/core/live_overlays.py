from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

WEATHER_API = "https://api.open-meteo.com/v1/forecast"
FLOOD_API = "https://flood-api.open-meteo.com/v1/flood"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(value: int, mn: int, mx: int) -> int:
    return max(mn, min(mx, value))


def _grid_points(min_lat: float, max_lat: float, min_lon: float, max_lon: float, zoom: int) -> list[tuple[float, float]]:
    # Denser grid at higher zoom, conservative at low zoom to limit API load.
    size = _clamp(int(zoom // 2) + 3, 3, 8)
    lat_span = max(0.05, max_lat - min_lat)
    lon_span = max(0.05, max_lon - min_lon)
    lat_step = lat_span / (size - 1)
    lon_step = lon_span / (size - 1)

    points: list[tuple[float, float]] = []
    for i in range(size):
        for j in range(size):
            lat = min_lat + i * lat_step
            lon = min_lon + j * lon_step
            points.append((round(lat, 4), round(lon, 4)))
    return points


def _open_meteo_batch(coords: list[tuple[float, float]]) -> list[dict[str, Any]]:
    if not coords:
        return []

    latitudes = ",".join(str(c[0]) for c in coords)
    longitudes = ",".join(str(c[1]) for c in coords)

    params = {
        "latitude": latitudes,
        "longitude": longitudes,
        "current": "temperature_2m,precipitation,rain,wind_speed_10m,wind_gusts_10m",
        "hourly": "precipitation,wind_speed_10m",
        "forecast_days": 1,
    }

    try:
        resp = httpx.get(WEATHER_API, params=params, timeout=8)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("latitude"), list):
        out = []
        lats = payload.get("latitude", [])
        lons = payload.get("longitude", [])
        currents = payload.get("current", [])
        for idx in range(min(len(lats), len(lons))):
            out.append({
                "latitude": lats[idx],
                "longitude": lons[idx],
                "current": currents[idx] if isinstance(currents, list) and idx < len(currents) else {},
            })
        return out
    if isinstance(payload, dict):
        return [payload]
    return []


def _open_meteo_flood_batch(coords: list[tuple[float, float]]) -> list[dict[str, Any]]:
    if not coords:
        return []

    # Flood API can be slower; sample fewer points for responsiveness.
    sampled = coords[:9]
    latitudes = ",".join(str(c[0]) for c in sampled)
    longitudes = ",".join(str(c[1]) for c in sampled)
    params = {
        "latitude": latitudes,
        "longitude": longitudes,
        "daily": "river_discharge_mean,river_discharge_max",
        "forecast_days": 1,
    }

    try:
        resp = httpx.get(FLOOD_API, params=params, timeout=8)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("latitude"), list):
        out = []
        lats = payload.get("latitude", [])
        lons = payload.get("longitude", [])
        dailies = payload.get("daily", [])
        for idx in range(min(len(lats), len(lons))):
            out.append({
                "latitude": lats[idx],
                "longitude": lons[idx],
                "daily": dailies[idx] if isinstance(dailies, list) and idx < len(dailies) else {},
            })
        return out
    if isinstance(payload, dict):
        return [payload]
    return []


def _hydro_level_color(discharge_max: float | None) -> tuple[str, str]:
    if discharge_max is None:
        return "unknown", "#8b949e"
    if discharge_max >= 400:
        return "high", "#f85149"
    if discharge_max >= 200:
        return "moderate", "#d29922"
    return "normal", "#3fb950"


def _hydro_estimate_from_weather(precip: float | None, rain: float | None, wind: float | None) -> tuple[str, str, float | None]:
    p = float(precip or 0.0)
    r = float(rain or 0.0)
    w = float(wind or 0.0)
    # Heuristic proxy index for rapid visual monitoring when live discharge is missing.
    idx = p * 14.0 + r * 10.0 + max(0.0, w - 35.0) * 0.8
    if idx >= 180:
        return "high", "#f85149", round(idx, 1)
    if idx >= 80:
        return "moderate", "#d29922", round(idx, 1)
    return "normal", "#3fb950", round(idx, 1)


def build_live_overlays(min_lat: float, max_lat: float, min_lon: float, max_lon: float, zoom: int) -> dict[str, Any]:
    points = _grid_points(min_lat, max_lat, min_lon, max_lon, zoom)

    weather_rows = _open_meteo_batch(points)
    flood_rows = _open_meteo_flood_batch(points)

    flood_map: dict[tuple[float, float], dict[str, Any]] = {}
    for row in flood_rows:
        lat = _safe_float(row.get("latitude"))
        lon = _safe_float(row.get("longitude"))
        if lat is None or lon is None:
            continue
        daily = row.get("daily") or {}
        rd_max = None
        if isinstance(daily, dict):
            vals = daily.get("river_discharge_max")
            if isinstance(vals, list) and vals:
                rd_max = _safe_float(vals[0])
            elif vals is not None:
                rd_max = _safe_float(vals)
        level, color = _hydro_level_color(rd_max)
        flood_map[(round(lat, 4), round(lon, 4))] = {
            "river_discharge_max": rd_max,
            "hydro_level": level,
            "hydro_color": color,
        }

    out_points: list[dict[str, Any]] = []
    for row in weather_rows:
        lat = _safe_float(row.get("latitude"))
        lon = _safe_float(row.get("longitude"))
        if lat is None or lon is None:
            continue

        current = row.get("current") or {}
        precip = _safe_float(current.get("precipitation"))
        rain = _safe_float(current.get("rain"))
        wind = _safe_float(current.get("wind_speed_10m"))
        gust = _safe_float(current.get("wind_gusts_10m"))
        temp = _safe_float(current.get("temperature_2m"))

        key = (round(lat, 4), round(lon, 4))
        hydro = flood_map.get(key)
        if hydro is None:
            level, color, est_idx = _hydro_estimate_from_weather(precip, rain, wind)
            hydro = {
                "river_discharge_max": None,
                "hydro_level": level,
                "hydro_color": color,
                "hydro_index_estimate": est_idx,
                "source": "estimated",
            }
        else:
            hydro["source"] = "flood-api"

        out_points.append({
            "lat": lat,
            "lon": lon,
            "weather": {
                "temp_c": temp,
                "precipitation_mm": precip,
                "rain_mm": rain,
                "wind_kmh": wind,
                "wind_gust_kmh": gust,
            },
            "hydro": hydro,
        })

    return {
        "generated_at": _iso_now(),
        "bbox": {
            "min_lat": min_lat,
            "max_lat": max_lat,
            "min_lon": min_lon,
            "max_lon": max_lon,
            "zoom": zoom,
        },
        "points": out_points,
    }
