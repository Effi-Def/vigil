"""
Collector webcam live vicino agli eventi italiani attivi.
"""
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from httpx import HTTPStatusError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vigil.collectors.matcher import normalize_absolute_url
from vigil.core.models import Event, MediaItem, Source

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "Webcam Collector"
COLLECTOR_INTERVAL = 60
COLLECTOR_ENABLED = True

RAPIDAPI_URL_TEMPLATE = "https://webcamstravel.p.rapidapi.com/webcams/list/nearby={lat},{lon},50"
WINDY_API_URL = "https://api.windy.com/webcams/api/v3/webcams"


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_italy(lat: float, lon: float) -> bool:
    return 35.5 <= lat <= 47.1 and 6.6 <= lon <= 18.5


def _upsert_source(db: Session) -> str:
    src_id = "webcam-live"
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        src = Source(
            id=src_id,
            name="Webcams",
            type="video",
            platform="webcam",
            url="https://webcamstravel.p.rapidapi.com",
            event_id=None,
            last_fetched=_utc_now_naive(),
            item_count=0,
        )
        db.add(src)
        db.flush()
    else:
        src.last_fetched = _utc_now_naive()  # type: ignore[assignment]
    return src_id


def _fetch_webcams_rapidapi(lat: float, lon: float, api_key: str) -> tuple[list[dict[str, Any]], bool]:
    url = RAPIDAPI_URL_TEMPLATE.format(lat=lat, lon=lon)
    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": "webcamstravel.p.rapidapi.com",
    }
    try:
        resp = httpx.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload, False
        if isinstance(payload, dict):
            for key in ("webcams", "result", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value, False
        return [], False
    except HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response else 0
        logger.warning(f"Webcam RapidAPI HTTP {status_code} ({lat},{lon}): {exc}")
        # On auth/rate-limit errors we stop using RapidAPI in this run.
        return [], status_code in {401, 403, 429}
    except Exception as exc:
        logger.warning(f"Webcam RapidAPI errore ({lat},{lon}): {exc}")
        return [], False


def _fetch_webcams_windy(lat: float, lon: float, api_key: str) -> list[dict[str, Any]]:
    headers = {"x-windy-api-key": api_key}
    params = {"nearby": f"{lat},{lon},50", "limit": 15, "offset": 0}
    try:
        resp = httpx.get(WINDY_API_URL, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict):
            if isinstance(payload.get("webcams"), list):
                return payload["webcams"]
            if isinstance(payload.get("result"), list):
                return payload["result"]
        return []
    except Exception as exc:
        logger.warning(f"Webcam Windy errore ({lat},{lon}): {exc}")
        return []


def _extract_url(cam: dict[str, Any]) -> tuple[str | None, str | None]:
    player = cam.get("player") or cam.get("url") or cam.get("webcam")
    thumb = cam.get("image") or cam.get("thumbnail") or cam.get("preview")

    player_url = None
    thumb_url = None

    if isinstance(player, dict):
        for key in ("day", "url", "player", "embed"):
            player_url = normalize_absolute_url(player.get(key))
            if player_url:
                break
    else:
        player_url = normalize_absolute_url(player)

    if isinstance(thumb, dict):
        for key in ("current", "url", "preview"):
            thumb_url = normalize_absolute_url(thumb.get(key))
            if thumb_url:
                break
    else:
        thumb_url = normalize_absolute_url(thumb)

    return player_url, thumb_url


def fetch_webcams(db: Session) -> int:
    rapidapi_key = (os.getenv("RAPIDAPI_KEY") or "").strip()
    windy_key = (os.getenv("WINDY_API_KEY") or "").strip()
    if not rapidapi_key and not windy_key:
        logger.info("Webcam collector: nessuna API key (RAPIDAPI_KEY/WINDY_API_KEY), skip")
        return 0

    src_id = _upsert_source(db)
    cutoff = _utc_now_naive() - timedelta(days=10)
    events = (
        db.query(Event)
        .filter(Event.lat.isnot(None), Event.lon.isnot(None))
        .filter(Event.updated_at >= cutoff)
        .order_by(Event.updated_at.desc())
        .all()
    )

    total = 0
    rapidapi_blocked = False
    for event in events:
        lat_raw = getattr(event, "lat", None)
        lon_raw = getattr(event, "lon", None)
        if lat_raw is None or lon_raw is None:
            continue
        lat = float(lat_raw)
        lon = float(lon_raw)
        if not _is_italy(lat, lon):
            continue

        cams: list[dict[str, Any]] = []
        if rapidapi_key and not rapidapi_blocked:
            cams, should_block_rapid = _fetch_webcams_rapidapi(lat, lon, rapidapi_key)
            if should_block_rapid:
                rapidapi_blocked = True
                if windy_key:
                    logger.info("Webcam collector: RapidAPI bloccata (auth/rate-limit), uso Windy fallback")

        if not cams and windy_key:
            cams = _fetch_webcams_windy(lat, lon, windy_key)
        if not cams:
            continue

        for cam in cams:
            url, thumb = _extract_url(cam)
            if not url:
                continue

            content_hash = hashlib.md5(f"webcam::{event.id}::{url}".encode()).hexdigest()
            if db.query(MediaItem).filter(MediaItem.content_hash == content_hash).first():
                continue

            name = (cam.get("title") or cam.get("name") or "Webcam live").strip()
            location = (cam.get("location") or cam.get("city") or event.region or "").strip()
            title = f"{name} - {location}".strip(" -")

            item = MediaItem(
                event_id=event.id,
                source_id=src_id,
                media_url=url,
                thumb_url=thumb,
                media_type="webcam",
                caption=title,
                author="webcam",
                lat=lat,
                lon=lon,
                geo_raw=event.region,
                captured_at=None,
                confidence=70,
                content_hash=content_hash,
            )
            try:
                with db.begin_nested():
                    db.add(item)
                    db.flush()
                src = db.query(Source).filter(Source.id == src_id).first()
                if src is not None:
                    src.item_count = int(src.item_count or 0) + 1  # type: ignore[assignment]
                total += 1
            except IntegrityError:
                continue

    logger.info(f"Webcam collector: {total} webcam salvate")
    return total
