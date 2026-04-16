from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

INGV_FDSN_URL = "https://webservices.ingv.it/fdsnws/event/1/query"
_CACHE_TTL_SECONDS = 60 * 60
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        n = float(value)
        if math.isnan(n):
            return None
        return n
    except Exception:
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return r * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def _severity_from_mag(mag: float | None) -> str:
    if mag is None:
        return "blue"
    if mag >= 5.0:
        return "red"
    if mag >= 4.0:
        return "orange"
    return "blue"


def _parse_event_date(event_date: str | None) -> datetime:
    raw = (event_date or "").strip()
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


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


async def get_ingv_seismicity(
    lat: float,
    lon: float,
    event_date: str,
    radius_km: int = 150,
    limit: int = 10,
    days: int = 120,
) -> dict[str, Any]:
    key = f"{lat:.5f}_{lon:.5f}_{radius_km}_{days}"  # fix: [1]
    cached = _cache_get(key)
    if cached is not None:
        return cached

    base_dt = _parse_event_date(event_date)
    end_dt = base_dt.astimezone(timezone.utc)
    start_dt = end_dt - timedelta(days=max(days, 1))

    params = {
        "format": "geojson",
        "latitude": lat,
        "longitude": lon,
        "maxradiuskm": max(radius_km, 1),
        "starttime": start_dt.strftime("%Y-%m-%d"),
        "endtime": end_dt.strftime("%Y-%m-%d"),
        "orderby": "time-desc",
        "limit": max(limit * 3, 20),
    }

    payload: dict[str, Any] | None = None
    status = "ok"  # fix: [2+3]
    error_detail: str | None = None  # fix: [2+3]
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(INGV_FDSN_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        payload = None
        status = "upstream_error"  # fix: [2+3]
        error_detail = str(exc)  # fix: [2+3]

    features = (payload or {}).get("features") or []
    parsed_events: list[dict[str, Any]] = []

    for feature in features:
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or []

        ev_lon = _safe_float(coords[0]) if len(coords) > 0 else None
        ev_lat = _safe_float(coords[1]) if len(coords) > 1 else None
        ev_depth = _safe_float(coords[2]) if len(coords) > 2 else None
        ev_mag = _safe_float(props.get("mag"))
        ev_place = props.get("place")
        ev_time_ms = props.get("time")

        ev_time = None
        if ev_time_ms is not None:
            try:
                ev_time = datetime.fromtimestamp(float(ev_time_ms) / 1000, tz=timezone.utc).isoformat()
            except Exception:
                ev_time = None

        distance_km = None
        if ev_lat is not None and ev_lon is not None:
            distance_km = _haversine_km(lat, lon, ev_lat, ev_lon)

        parsed_events.append(
            {
                "id": feature.get("id") or props.get("ids") or None,
                "time": ev_time,
                "magnitude": ev_mag,
                "depth_km": ev_depth,
                "place": ev_place,
                "lat": ev_lat,
                "lon": ev_lon,
                "distance_km": round(distance_km, 2) if distance_km is not None else None,
                "severity": _severity_from_mag(ev_mag),
                "source": "INGV",
            }
        )

    parsed_events.sort(
        key=lambda e: (
            e.get("distance_km") if e.get("distance_km") is not None else 1e9,
            e.get("time") or "",
        )
    )

    if status == "ok" and len(parsed_events) == 0:
        status = "no_data"  # fix: [2+3]

    current_event = parsed_events[0] if parsed_events else None
    past_events = parsed_events[: max(limit, 1)]
    sorted_by_time = sorted(
        [e for e in past_events if e.get("time")],
        key=lambda x: x["time"],
        reverse=True,
    )  # fix: [4]

    mags = [e["magnitude"] for e in parsed_events if e.get("magnitude") is not None]
    depths = [e["depth_km"] for e in parsed_events if e.get("depth_km") is not None]

    result = {
        "provider": "INGV FDSN Event Web Service",
        "source_url": INGV_FDSN_URL,
        "status": status,  # fix: [2+3]
        "query": {
            "latitude": lat,
            "longitude": lon,
            "radius_km": radius_km,
            "days": days,
            "starttime": params["starttime"],
            "endtime": params["endtime"],
        },
        "current_event": current_event,
        "past_events": past_events,
        "stats": {
            "count": len(parsed_events),
            "avg_magnitude": round(sum(mags) / len(mags), 2) if mags else None,
            "max_magnitude": round(max(mags), 2) if mags else None,
            "avg_depth_km": round(sum(depths) / len(depths), 2) if depths else None,
            "last_event_at": sorted_by_time[0].get("time") if sorted_by_time else None,  # fix: [4]
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_detail is not None:
        result["error_detail"] = error_detail  # fix: [2+3]

    if status == "ok":
        _cache_set(key, result)  # fix: [2+3]
    return result
