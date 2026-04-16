import asyncio
import logging
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import List

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from vigil.collectors.matcher import clean_media_title, normalize_absolute_url, quality_score
from vigil.core.database import SessionLocal, init_db, get_db
from vigil.core.climate_context import get_climate_context
from vigil.core.geo import extract_coordinates
from vigil.core.ingv_seismicity import get_ingv_seismicity
from vigil.core.models import CollectorHealth, Event, MediaItem, Source, HydroStation
from vigil.core.news_relevance import MIN_RELEVANCE_SCORE, filter_operational_articles, score_article_relevance
from vigil.core.rss_utils import canonical_url_hash, domain_name, extract_image_from_description, extract_og_media, event_region_aliases, normalize_text, parse_published_datetime, parse_rss_feed, score_event_match
from vigil.core.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

# Load local .env for dev runs started without --env-file.
load_dotenv(override=False)

MEDIA_RICH_CACHE_TTL_SEC = 45
_MEDIA_RICH_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_EVENT_NEWS_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_LIVE_OVERLAYS_CACHE: dict[tuple, tuple[float, dict]] = {}
LIVE_OVERLAYS_CACHE_TTL_SEC = 75
_WIND_FIELD_CACHE: dict[tuple, tuple[float, dict]] = {}
WIND_FIELD_CACHE_TTL_SEC = 180
_HYDRO_RIVERS_CACHE: dict[tuple, tuple[float, dict]] = {}
HYDRO_RIVERS_CACHE_TTL_SEC = 600
_TERRITORY_CACHE: dict[tuple, tuple[float, dict]] = {}
TERRITORY_CACHE_TTL_SEC = 45
EVENT_NEWS_CACHE_TTL_SEC = 120
_SUBEVENT_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
SUBEVENT_CACHE_TTL_SEC = 120
_SUBEVENT_GLOBAL_WARM_TS = 0.0
SUBEVENT_GLOBAL_WARM_TTL_SEC = 900


# ─── WebSocket connection manager ────────────────────────────────────────────

class _WSManager:
    def __init__(self):
        self._clients: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._clients.append(ws)
        logger.debug(f"WS connect — {len(self._clients)} client(s)")

    def disconnect(self, ws: WebSocket):
        self._clients.remove(ws)
        logger.debug(f"WS disconnect — {len(self._clients)} client(s)")

    async def broadcast(self, payload: dict):
        dead = []
        for ws in self._clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.remove(ws)


ws_manager = _WSManager()


# ─── App lifecycle ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Vigil API", version="0.2.0", lifespan=lifespan)

_default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_env_origins = [
    origin.strip()
    for origin in os.getenv("VIGIL_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_env_origins or _default_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _utc_now_naive():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _with_last_updated(payload, fallback_dt=None):
    stamp = (fallback_dt or _utc_now_naive()).isoformat()
    if isinstance(payload, list):
        rows = []
        for item in payload:
            if isinstance(item, dict):
                row = dict(item)
                if not row.get("last_updated"):
                    row["last_updated"] = row.get("updated_at") or row.get("fetched_at") or row.get("captured_at") or stamp
                rows.append(row)
            else:
                rows.append(item)
        return rows
    if isinstance(payload, dict):
        row = dict(payload)
        if not row.get("last_updated"):
            row["last_updated"] = row.get("updated_at") or row.get("fetched_at") or row.get("captured_at") or stamp
        return row
    return payload


def _collector_status(last_ok):
    if last_ok is None:
        return "down"
    minutes_since_ok = (_utc_now_naive() - last_ok).total_seconds() / 60
    if minutes_since_ok <= 60:
        return "ok"
    if minutes_since_ok <= 180:
        return "stale"
    return "down"


def _collector_health_payload(record: CollectorHealth) -> dict:
    payload = record.to_dict()
    run_count = int(record.run_count or 0)
    ok_count = int(record.ok_count or 0)
    payload["status"] = _collector_status(record.last_ok)
    payload["ok_rate"] = int(round((ok_count / run_count) * 100)) if run_count > 0 else 0
    return payload


def _collectors_aggregate_status(db: Session) -> str:
    records = db.query(CollectorHealth).all()
    if not records:
        return "down"
    statuses = [_collector_status(record.last_ok) for record in records]
    if any(s == "down" for s in statuses):
        return "down"
    if any(s == "stale" for s in statuses):
        return "degraded"
    return "ok"


def _normalize_search(text: str) -> str:
    raw = unicodedata.normalize("NFD", text or "")
    raw = raw.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", raw).strip()


def _normalize_river_name(value: str) -> str:
    raw = _normalize_search(value or "")
    raw = re.sub(r"\b(fiume|torrente|rio|rivo|canale|canal|scolo|fosso|della|delle|del|dei|di|da)\b", " ", raw)
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _river_name_matches(expected: str, candidate: str) -> bool:
    left = _normalize_river_name(expected)
    right = _normalize_river_name(candidate)
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return False
    overlap = left_tokens & right_tokens
    return len(overlap) >= min(len(left_tokens), len(right_tokens))


def _geojson_to_bbox_paths(geojson: dict | None, min_lat: float, max_lat: float, min_lon: float, max_lon: float, pad: float = 0.25) -> list[list[list[float]]]:
    if not isinstance(geojson, dict):
        return []

    def in_bounds(lat: float, lon: float) -> bool:
        return (min_lat - pad) <= lat <= (max_lat + pad) and (min_lon - pad) <= lon <= (max_lon + pad)

    def normalize_line(coords: list) -> list[list[float]]:
        line: list[list[float]] = []
        for coord in coords or []:
            if not isinstance(coord, (list, tuple)) or len(coord) < 2:
                continue
            lon, lat = coord[0], coord[1]
            try:
                lat_v = float(lat)
                lon_v = float(lon)
            except (TypeError, ValueError):
                continue
            line.append([lat_v, lon_v])
        return line

    geo_type = str(geojson.get("type") or "")
    coords = geojson.get("coordinates") or []
    raw_lines: list[list[list[float]]] = []

    if geo_type == "LineString":
        raw_lines = [normalize_line(coords)]
    elif geo_type == "MultiLineString":
        raw_lines = [normalize_line(line) for line in coords]
    elif geo_type == "Polygon":
        raw_lines = [normalize_line(ring) for ring in coords[:1]]
    elif geo_type == "MultiPolygon":
        raw_lines = [normalize_line(poly[0]) for poly in coords if poly]

    clipped: list[list[list[float]]] = []
    for line in raw_lines:
        current: list[list[float]] = []
        for idx, point in enumerate(line):
            lat_v, lon_v = point
            inside = in_bounds(lat_v, lon_v)
            prev_inside = idx > 0 and in_bounds(line[idx - 1][0], line[idx - 1][1])
            next_inside = idx + 1 < len(line) and in_bounds(line[idx + 1][0], line[idx + 1][1])
            if inside or prev_inside or next_inside:
                current.append([round(lat_v, 6), round(lon_v, 6)])
            else:
                if len(current) >= 2:
                    clipped.append(current)
                current = []
        if len(current) >= 2:
            clipped.append(current)

    return clipped


async def _fetch_nominatim_river_paths(name: str, min_lat: float, max_lat: float, min_lon: float, max_lon: float) -> list[list[list[float]]]:
    queries = [
        f"{name} river Italy",
        f"Fiume {name} Italia",
        f"{name} Italia",
    ]
    headers = {
        "User-Agent": "vigil-backend/0.2 (hydro geometry lookup)",
        "Accept-Language": "it,en",
    }

    async with httpx.AsyncClient(timeout=12, headers=headers, follow_redirects=True) as client:
        for query in queries:
            try:
                resp = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": query, "format": "jsonv2", "polygon_geojson": 1, "limit": 3},
                )
                resp.raise_for_status()
                items = resp.json() or []
            except Exception as exc:
                logger.warning(f"Nominatim river lookup fallito per {name}: {exc}")
                continue

            for item in items:
                display_name = str(item.get("display_name") or item.get("name") or "")
                if not _river_name_matches(name, display_name):
                    continue
                paths = _geojson_to_bbox_paths(item.get("geojson"), min_lat, max_lat, min_lon, max_lon)
                if paths:
                    return paths
    return []


EUROPE_BOUNDS = {
    "lat_min": 34.0,
    "lat_max": 72.0,
    "lon_min": -25.0,
    "lon_max": 45.0,
}

ITALY_BOUNDS = {
    "lat_min": 35.5,
    "lat_max": 47.2,
    "lon_min": 6.0,
    "lon_max": 18.8,
}


def _is_in_europe(lat, lon) -> bool:
    if lat is None or lon is None:
        return False
    try:
        lat_v = float(lat)
        lon_v = float(lon)
    except (TypeError, ValueError):
        return False
    return (
        EUROPE_BOUNDS["lat_min"] <= lat_v <= EUROPE_BOUNDS["lat_max"]
        and EUROPE_BOUNDS["lon_min"] <= lon_v <= EUROPE_BOUNDS["lon_max"]
    )


def _is_in_italy(lat, lon) -> bool:
    if lat is None or lon is None:
        return False
    try:
        lat_v = float(lat)
        lon_v = float(lon)
    except (TypeError, ValueError):
        return False
    return (
        ITALY_BOUNDS["lat_min"] <= lat_v <= ITALY_BOUNDS["lat_max"]
        and ITALY_BOUNDS["lon_min"] <= lon_v <= ITALY_BOUNDS["lon_max"]
    )


def _hydro_from_local(precip_mm: float, wind_kmh: float) -> tuple[str, str, float]:
    idx = max(0.0, precip_mm) * 14.0 + max(0.0, wind_kmh - 35.0) * 0.8
    if idx >= 180:
        return "high", "#f85149", round(idx, 1)
    if idx >= 80:
        return "moderate", "#d29922", round(idx, 1)
    return "normal", "#3fb950", round(idx, 1)


_NEWS_TYPE_QUERY = {
    "flood": "alluvione OR esondazione OR piena OR frana",
    "storm": "maltempo OR temporali OR nubifragi OR grandine",
    "meteoalarm": "allerta meteo OR maltempo OR piogge OR temporali",
    "dpc_vigilanza": "allerta meteo OR maltempo OR piogge OR temporali",
    "earthquake": "terremoto OR sisma OR scossa",
    "wildfire": "incendio OR rogo OR fiamme",
    "volcano": "vulcano OR eruzione OR cenere",
}
_ITALY_REGION_ANCHORS = {
    "abruzzo": {"name": "L'Aquila", "lat": 42.3500, "lon": 13.3995},
    "basilicata": {"name": "Potenza", "lat": 40.6400, "lon": 15.8050},
    "calabria": {"name": "Catanzaro", "lat": 38.9098, "lon": 16.5877},
    "campania": {"name": "Napoli", "lat": 40.8518, "lon": 14.2681},
    "emilia-romagna": {"name": "Bologna", "lat": 44.4949, "lon": 11.3426},
    "friuli-venezia giulia": {"name": "Trieste", "lat": 45.6495, "lon": 13.7768},
    "lazio": {"name": "Roma", "lat": 41.9028, "lon": 12.4964},
    "liguria": {"name": "Genova", "lat": 44.4056, "lon": 8.9463},
    "lombardia": {"name": "Milano", "lat": 45.4642, "lon": 9.1900},
    "marche": {"name": "Ancona", "lat": 43.6158, "lon": 13.5189},
    "molise": {"name": "Campobasso", "lat": 41.5595, "lon": 14.6684},
    "piemonte": {"name": "Torino", "lat": 45.0703, "lon": 7.6869},
    "puglia": {"name": "Bari", "lat": 41.1171, "lon": 16.8719},
    "sardegna": {"name": "Cagliari", "lat": 39.2238, "lon": 9.1217},
    "sicilia": {"name": "Palermo", "lat": 38.1157, "lon": 13.3615},
    "toscana": {"name": "Firenze", "lat": 43.7696, "lon": 11.2558},
    "trentino-alto adige": {"name": "Trento", "lat": 46.0748, "lon": 11.1217},
    "umbria": {"name": "Perugia", "lat": 43.1107, "lon": 12.3908},
    "valle d'aosta": {"name": "Aosta", "lat": 45.7370, "lon": 7.3201},
    "veneto": {"name": "Venezia", "lat": 45.4408, "lon": 12.3155},
}
_ITALY_PLACE_HINTS = {
    "termoli": {"name": "Termoli", "lat": 41.9993, "lon": 14.9950},
    "campobasso": {"name": "Campobasso", "lat": 41.5595, "lon": 14.6684},
    "isernia": {"name": "Isernia", "lat": 41.5960, "lon": 14.2332},
    "basso molise": {"name": "Basso Molise", "lat": 41.8900, "lon": 14.9900},
    "liscione": {"name": "Lago di Guardialfiera (Liscione)", "lat": 41.8010, "lon": 14.7940},
    "agnone": {"name": "Agnone", "lat": 41.8103, "lon": 14.3766},
}
_NEWS_QUERY_STOPWORDS = {
    "orange", "yellow", "red", "warning", "issued", "italy", "italia",
    "issue", "snow", "ice", "wind", "rain", "thunderstorm", "warning",
    "allerta", "meteo", "issued", "for",
}
_SUBEVENT_PATTERNS: list[tuple[str, str, tuple[str, ...]]] = [
    ("bridge", "Ponte / infrastruttura", ("ponte", "croll", "cediment")),
    ("flood", "Esondazione / allagamento", ("esond", "allagat", "alluvion", "fium", "torrente", "nubifrag")),
    ("landslide", "Frana / smottamento", ("frana", "smott", "costone", "cedimento")),
    ("evacuation", "Evacuazione", ("evacu", "sgomber", "sfoll")),
    ("road", "Viabilità interrotta", ("statale", "strada", "sp 86", "ss.16", "adriatica", "traffico", "chiusa", "chiuso")),
    ("dam", "Diga / argine", ("diga", "liscione", "argine", "monitoraggio")),
]
_SUBEVENT_PLACE_HINTS: dict[str, dict[str, float | str]] = {
    "trigno": {"name": "Trigno", "lat": 41.976, "lon": 14.766},
    "ponte sul trigno": {"name": "Ponte sul Trigno", "lat": 41.976, "lon": 14.766},
    "marina di montenero": {"name": "Marina di Montenero", "lat": 41.995, "lon": 14.996},
    "montenero": {"name": "Montenero di Bisaccia", "lat": 41.950, "lon": 14.782},
    "termoli": {"name": "Termoli", "lat": 41.999, "lon": 14.995},
    "basso molise": {"name": "Basso Molise", "lat": 41.890, "lon": 14.990},
    "campobasso": {"name": "Campobasso", "lat": 41.5595, "lon": 14.6684},
    "liscione": {"name": "Diga del Liscione", "lat": 41.801, "lon": 14.794},
    "guardialfiera": {"name": "Guardialfiera", "lat": 41.805, "lon": 14.794},
    "larino": {"name": "Larino", "lat": 41.804, "lon": 14.911},
    "petacciato": {"name": "Petacciato", "lat": 42.002, "lon": 14.860},
    "venafro": {"name": "Venafro", "lat": 41.482, "lon": 14.045},
}


def _title_from_caption(caption: str | None) -> str:
    raw = (caption or "").strip()
    if not raw:
        return ""
    return raw.splitlines()[0].strip()


def _source_favicon_url(source_url: str | None) -> str | None:
    raw = (source_url or "").strip()
    if not raw:
        return None
    return f"https://www.google.com/s2/favicons?sz=128&domain_url={raw}"


def _event_rss_feed_urls(event: Event) -> list[str]:
    try:
        from vigil.collectors.rss_local import REGIONAL_RSS
    except Exception:
        return []

    region_key = normalize_text(str(event.region or "")).replace(" ", "-")
    feeds: list[str] = []
    if region_key:
        feeds.extend(REGIONAL_RSS.get(region_key, []))
    feeds.extend(REGIONAL_RSS.get("nazionale", []))
    return list(dict.fromkeys(feeds))[:6]


def _event_news_query(event: Event) -> str:
    title_norm = normalize_text(str(event.title or ""))
    region = str(event.region or "").strip()
    event_type = normalize_text(str(event.type or ""))

    phenomenon = []
    if "rain" in title_norm or "flood" in title_norm:
        phenomenon.append("pioggia OR nubifragi OR esondazione OR alluvione")
    if "thunderstorm" in title_norm or "storm" in title_norm:
        phenomenon.append("temporali OR grandine OR maltempo")
    if "wind" in title_norm:
        phenomenon.append("vento OR raffiche OR burrasca")
    if "snow" in title_norm or "ice" in title_norm:
        phenomenon.append("neve OR ghiaccio OR gelicidio")
    if not phenomenon:
        phenomenon.append(_NEWS_TYPE_QUERY.get(event_type, "cronaca OR emergenza"))

    title_words = [
        w for w in normalize_text(str(event.title or "")).split()
        if len(w) >= 4 and w not in _NEWS_QUERY_STOPWORDS
    ]

    parts = []
    if region:
        parts.append(region)
    parts.append("(" + " OR ".join(dict.fromkeys(phenomenon)) + ")")
    if title_words:
        parts.append(" ".join(title_words[:2]))
    return " ".join(p for p in parts if p).strip()


def _event_news_relevance(event: Event, title: str, published_dt=None) -> int:
    relevance = int(score_event_match(event, title or "", title or "", published_dt))
    title_norm = normalize_text(title or "")

    if any(alias and alias in title_norm for alias in event_region_aliases(event)):
        relevance += 12

    anchor = event.started_at or event.updated_at or _utc_now_naive()
    pub_naive = published_dt
    if pub_naive is not None:
        try:
            if getattr(pub_naive, "tzinfo", None) is not None:
                pub_naive = pub_naive.replace(tzinfo=None)
            delta_days = abs((anchor - pub_naive).total_seconds()) / 86400.0
            if delta_days <= 1:
                relevance += 12
            elif delta_days <= 3:
                relevance += 8
            elif delta_days > 21 and (_utc_now_naive() - anchor).days <= 30:
                relevance -= 25
        except Exception:
            pass

    return max(0, min(100, relevance))


def _event_live_public_videos(event: Event, existing_urls: set[str] | None = None, limit: int = 4) -> list[dict]:
    if limit <= 0:
        return []
    try:
        from vigil.collectors.youtube_rss import (
            _build_query,
            _confidence_for_event,
            _search_public_videos,
            _search_youtube_public,
        )
    except Exception as exc:
        logger.warning(f"Live public video fallback unavailable for {event.id}: {exc}")
        return []

    query = _build_query(event)
    raw_items = _search_youtube_public(query)
    if len(raw_items) < max(2, limit):
        raw_items.extend(_search_public_videos(query))

    seen = {u for u in (existing_urls or set()) if u}
    rows: list[dict] = []
    now_iso = _utc_now_naive().isoformat()

    for it in raw_items[: max(limit * 3, 8)]:
        title = (it.get("name") or "").strip()
        link = normalize_absolute_url(it.get("url")) or ""
        description = (it.get("description") or "").strip()
        if not title or not link or link in seen:
            continue
        seen.add(link)

        platform_key = str(it.get("platform") or "peertube").strip().lower()
        source_name = (it.get("source_name") or ("YouTube" if platform_key == "youtube_public" else "PeerTube")).strip()
        published_dt = parse_published_datetime(str(it.get("publishedAt") or "").strip())
        confidence = _confidence_for_event(event, title, f"{description} {source_name}")
        if platform_key == "youtube_public":
            confidence = min(95, confidence + 6)
        relevance = _event_news_relevance(event, f"{title}\n{description}\n{source_name}", published_dt)
        if confidence < 50 or relevance < 32:
            continue

        thumb_url = normalize_absolute_url(it.get("thumbnailPath") or it.get("thumbnail"))
        row = {
            "id": f"live-video::{event.id}::{canonical_url_hash(link)}",
            "event_id": str(event.id),
            "source_id": None,
            "media_url": link,
            "thumb_url": thumb_url,
            "media_type": "video",
            "caption": clean_media_title(title, source_name=source_name, platform=platform_key),
            "author": source_name,
            "platform": platform_key,
            "source": source_name,
            "source_name": source_name,
            "source_url": link,
            "confidence": confidence,
            "relevance": relevance,
            "quality_score": int(round(quality_score(
                confidence=confidence,
                media_type="video",
                platform=platform_key,
                captured_at=published_dt,
                fetched_at=None,
                has_thumb=bool(thumb_url),
            ) * 0.6 + relevance * 0.4)),
            "captured_at": published_dt.isoformat() if published_dt else None,
            "fetched_at": now_iso,
            "created_at": now_iso,
            "is_native_visual": True,
        }
        rows.append(row)
        if len(rows) >= limit:
            break

    return rows


def _summary_severity_phrase(event: Event) -> str:
    sev = str(event.severity or "").strip().lower()
    mapping = {
        "red": "criticità elevata",
        "orange": "forte attenzione operativa",
        "blue": "attenzione ordinaria",
        "yellow": "attenzione ordinaria",
        "green": "monitoraggio ordinario",
    }
    return mapping.get(sev, (event.status or "situazione in osservazione").lower())


def _summary_watch_items(event: Event) -> list[str]:
    watch: list[str] = []
    wind = float(getattr(event, "wind_kmh", 0.0) or 0.0)
    precip = float(getattr(event, "precipitation_mm", 0.0) or 0.0)
    magnitude = float(getattr(event, "magnitude", 0.0) or 0.0)

    if wind >= 70:
        watch.append("raffiche forti con possibili danni a coperture, alberature e viabilità")
    elif wind >= 35:
        watch.append("ventilazione sostenuta e possibili criticità locali alla circolazione")
    if precip >= 20:
        watch.append("piogge abbondanti con rischio di allagamenti e innalzamento dei corsi d'acqua")
    elif precip >= 5:
        watch.append("precipitazioni persistenti e possibili disagi puntuali")
    if magnitude >= 5:
        watch.append("possibili repliche e verifiche sulle infrastrutture più esposte")
    elif magnitude >= 3:
        watch.append("monitoraggio di eventuali repliche locali")
    if not watch:
        watch.append("seguire gli aggiornamenti delle fonti ufficiali e della situazione sul territorio")
    return watch[:3]


def _build_event_summary_payload(event: Event, news_items: list[dict], media_items: list[dict], subevents: list[dict]) -> dict:
    articles = [item for item in filter_operational_articles(news_items or []) if str(item.get("media_type") or "article") == "article"]
    visual_items = [item for item in (media_items or []) if str(item.get("media_type") or "") in {"image", "video", "webcam"}]
    source_names = list(dict.fromkeys(
        str(item.get("source") or item.get("source_name") or item.get("author") or "").strip()
        for item in articles
        if str(item.get("source") or item.get("source_name") or item.get("author") or "").strip()
    ))[:5]

    impacts: list[str] = []
    for item in subevents or []:
        label = str(item.get("subcategory") or item.get("type") or "Impatto locale").strip()
        place = str(item.get("place_name") or item.get("region") or "").strip()
        text = f"{label} a {place}" if place else label
        if text not in impacts:
            impacts.append(text)
    if not impacts:
        for item in articles[:3]:
            title = str(item.get("title") or "").strip()
            if title and title not in impacts:
                impacts.append(title)

    metric_parts: list[str] = []
    event_wind = getattr(event, "wind_kmh", None)
    event_precip = getattr(event, "precipitation_mm", None)
    event_magnitude = getattr(event, "magnitude", None)
    event_depth = getattr(event, "depth_km", None)
    if event_wind is not None:
        metric_parts.append(f"vento fino a {int(round(float(event_wind)))} km/h")
    if event_precip is not None:
        metric_parts.append(f"precipitazioni stimate a {round(float(event_precip), 1)} mm")
    if event_magnitude is not None:
        metric_parts.append(f"magnitudo {round(float(event_magnitude), 1)}")
    if event_depth is not None:
        metric_parts.append(f"profondità {int(round(float(event_depth)))} km")

    key_points: list[str] = [
        f"Evento in {event.region or 'area monitorata'} con livello di {_summary_severity_phrase(event)}.",
    ]
    if metric_parts:
        key_points.append("Parametri chiave: " + ", ".join(metric_parts) + ".")
    if impacts:
        key_points.append("Impatti principali emersi: " + "; ".join(impacts[:3]) + ".")
    if articles or visual_items:
        coverage_bits = []
        if articles:
            coverage_bits.append(f"{len(articles)} articoli recenti")
        if visual_items:
            coverage_bits.append(f"{len(visual_items)} contenuti visuali")
        tail = f" da {', '.join(source_names[:3])}" if source_names else ""
        key_points.append("Copertura disponibile: " + ", ".join(coverage_bits) + tail + ".")

    summary_parts = []
    if metric_parts or impacts or articles or visual_items:
        summary_parts.append(
            f"{event.title or 'Evento in corso'} interessa {event.region or 'l’area monitorata'} ed è attualmente classificato con {_summary_severity_phrase(event)}."
        )
    else:
        summary_parts.append("Dati operativi insufficienti per questo evento.")
    if metric_parts:
        summary_parts.append("I parametri più rilevanti indicano " + ", ".join(metric_parts) + ".")
    if impacts:
        summary_parts.append("Dalle notizie e dai riscontri locali emergono soprattutto: " + "; ".join(impacts[:2]) + ".")
    if articles or visual_items:
        source_tail = f" e fonti principali come {', '.join(source_names[:3])}" if source_names else ""
        summary_parts.append(f"La copertura attuale include {len(articles)} articoli e {len(visual_items)} media visivi{source_tail}.")

    return {
        "event_id": str(event.id),
        "generated_at": _utc_now_naive().isoformat(),
        "headline": f"{event.title or 'Evento'} · situazione in {event.region or 'area monitorata'}",
        "summary": " ".join(part.strip() for part in summary_parts if part).strip(),
        "key_points": key_points[:4],
        "major_impacts": impacts[:5],
        "watch_items": _summary_watch_items(event),
        "latest_headlines": [str(item.get("title") or "").strip() for item in articles[:4] if str(item.get("title") or "").strip()],
        "sources": source_names,
        "coverage": {
            "articles": len(articles),
            "visual_media": len(visual_items),
            "videos": sum(1 for item in visual_items if str(item.get("media_type") or "") == "video"),
            "images": sum(1 for item in visual_items if str(item.get("media_type") or "") == "image"),
            "webcams": sum(1 for item in visual_items if str(item.get("media_type") or "") == "webcam"),
            "local_incidents": len(subevents or []),
        },
    }


def _subevent_label_from_text(text: str) -> tuple[str | None, str | None]:
    hay = normalize_text(text or "")

    if "ponte" in hay and any(token in hay for token in ("croll", "cediment", "spezz", "interrotta")):
        return "bridge", "Ponte / infrastruttura"
    if any(token in hay for token in ("esond", "allagat", "alluvion", "fium", "torrente", "nubifrag")):
        return "flood", "Esondazione / allagamento"
    if any(token in hay for token in ("frana", "smott", "costone", "cedimento")):
        return "landslide", "Frana / smottamento"
    if any(token in hay for token in ("evacu", "sgomber", "sfoll")):
        return "evacuation", "Evacuazione"
    if any(token in hay for token in ("statale", "strada", "sp 86", "ss 16", "adriatica", "traffico", "chiusa", "chiuso")):
        return "road", "Viabilità interrotta"
    if any(token in hay for token in ("diga", "liscione", "argine", "monitoraggio")):
        return "dam", "Diga / argine"

    for key, label, tokens in _SUBEVENT_PATTERNS:
        if any(token in hay for token in tokens):
            return key, label
    return None, None


def _subevent_place_anchor(text: str, event: Event) -> tuple[float | None, float | None, str | None]:
    hay = normalize_text(text or "")
    for token, anchor in _SUBEVENT_PLACE_HINTS.items():
        if normalize_text(token) in hay:
            return float(anchor["lat"]), float(anchor["lon"]), str(anchor["name"])

    lat, lon, geo_raw, _ = extract_coordinates(text or "", use_geocoder=True)
    if lat is not None and lon is not None:
        place_name = str(geo_raw or event.region or "Località rilevata")
        return float(lat), float(lon), place_name

    inferred = _infer_event_anchor(event)
    if inferred:
        return float(inferred["lat"]), float(inferred["lon"]), str(inferred["name"])
    if event.lat is not None and event.lon is not None:
        return float(event.lat), float(event.lon), str(event.region or "Area evento")
    return None, None, str(event.region or "Area evento")


def _subevent_matches_video(subevent: dict, video: dict) -> bool:
    hay = normalize_text(f"{video.get('caption') or ''} {video.get('author') or ''} {video.get('source') or ''}")
    place = normalize_text(str(subevent.get("place_name") or ""))
    label = normalize_text(str(subevent.get("subcategory") or ""))
    if place and any(len(tok) >= 4 and tok in hay for tok in place.split()):
        return True
    if "ponte" in label and any(tok in hay for tok in ("ponte", "croll", "trigno")):
        return True
    if any(tok in label for tok in ("allag", "esond")) and any(tok in hay for tok in ("allag", "esond", "termoli", "liscione", "molise")):
        return True
    if "frana" in label and any(tok in hay for tok in ("frana", "smott", "costone")):
        return True
    if "evacua" in label and any(tok in hay for tok in ("evacu", "sgomber", "montenero")):
        return True
    return False


def _attach_subevent_videos(subevents: list[dict], videos: list[dict]):
    for subevent in subevents:
        matched: list[dict] = []
        for video in videos:
            if _subevent_matches_video(subevent, video):
                matched.append({
                    "title": video.get("caption") or video.get("title") or "video",
                    "url": video.get("media_url") or video.get("url"),
                    "thumb_url": video.get("thumb_url"),
                    "source": video.get("source") or video.get("author") or video.get("platform"),
                    "platform": video.get("platform"),
                })
            if len(matched) >= 2:
                break
        if not matched and videos:
            fallback = videos[0]
            matched.append({
                "title": fallback.get("caption") or fallback.get("title") or "video",
                "url": fallback.get("media_url") or fallback.get("url"),
                "thumb_url": fallback.get("thumb_url"),
                "source": fallback.get("source") or fallback.get("author") or fallback.get("platform"),
                "platform": fallback.get("platform"),
            })
        subevent["videos"] = matched
        subevent["video_url"] = matched[0].get("url") if matched else None


def _infer_subevents_from_news(event: Event, news_items: list[dict], videos: list[dict] | None = None) -> list[dict]:
    rows: list[dict] = []
    seen: dict[str, dict] = {}

    for idx, item in enumerate(news_items or []):
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        sub_key, sub_label = _subevent_label_from_text(title)
        if not sub_key or not sub_label:
            continue

        lat, lon, place_name = _subevent_place_anchor(f"{title}\n{item.get('source') or ''}\n{item.get('url') or ''}", event)
        dedupe_key = f"{sub_key}::{normalize_text(str(place_name or title))}"
        evidence = {
            "title": title,
            "url": item.get("url"),
            "source": item.get("source"),
            "published": item.get("published"),
            "thumb_url": item.get("thumb_url"),
            "video_url": item.get("video_url"),
        }

        current = seen.get(dedupe_key)
        if current is None:
            current = {
                "id": f"subevent::{event.id}::{sub_key}::{idx}",
                "parent_event_id": str(event.id),
                "title": title,
                "subcategory": sub_label,
                "type": sub_key,
                "severity": event.severity,
                "status": event.status,
                "lat": lat,
                "lon": lon,
                "place_name": place_name,
                "region": event.region,
                "source": item.get("source"),
                "news_url": item.get("url"),
                "thumb_url": item.get("thumb_url"),
                "video_url": item.get("video_url"),
                "relevance": int(item.get("relevance") or 0),
                "news": [evidence],
                "videos": [],
            }
            seen[dedupe_key] = current
            rows.append(current)
        else:
            current["news"].append(evidence)
            current["relevance"] = max(int(current.get("relevance") or 0), int(item.get("relevance") or 0))
            if item.get("thumb_url") and not current.get("thumb_url"):
                current["thumb_url"] = item.get("thumb_url")
            if item.get("url") and not current.get("news_url"):
                current["news_url"] = item.get("url")
            if item.get("video_url") and not current.get("video_url"):
                current["video_url"] = item.get("video_url")

    rows.sort(key=lambda row: (int(row.get("relevance") or 0), len(row.get("news") or [])), reverse=True)
    if videos:
        _attach_subevent_videos(rows, videos)
    return rows[:8]


def _subevent_category(item_type: str | None, parent_event: Event) -> str:
    key = str(item_type or "").strip().lower()
    if key in {"flood", "landslide", "dam"}:
        return "flood"
    if key in {"bridge", "road", "evacuation"}:
        return normalize_text(str(parent_event.category or parent_event.type or "storm")) or "storm"
    return normalize_text(str(parent_event.category or parent_event.type or "other")) or "other"


def _ensure_subevent_source(db: Session, event_id: str, source_type: str, name: str, platform: str, url: str | None) -> str:
    source_id = f"subevent-{source_type}-{event_id}-{canonical_url_hash(f'{platform}|{url or name}')[:12]}"
    src = db.query(Source).filter(Source.id == source_id).first()
    if src is None:
        src = Source(
            id=source_id,
            name=name or platform or source_type,
            type=source_type,
            platform=platform or source_type,
            url=url,
            event_id=event_id,
            last_fetched=_utc_now_naive(),
            item_count=0,
        )
        db.add(src)
        db.flush()
    else:
        src.name = name or src.name  # type: ignore[assignment]
        src.platform = platform or src.platform  # type: ignore[assignment]
        src.url = url or src.url  # type: ignore[assignment]
        src.event_id = event_id  # type: ignore[assignment]
        src.last_fetched = _utc_now_naive()  # type: ignore[assignment]
    return source_id


def _persist_subevents(db: Session, parent_event: Event, subevents: list[dict]) -> int:
    created = 0
    now = _utc_now_naive()
    seen_hashes: set[str] = set()

    for item in subevents or []:
        event_id = str(item.get("event_id") or item.get("id") or "").strip()
        if not event_id:
            event_id = f"subevent::{parent_event.id}::{canonical_url_hash(str(item.get('title') or parent_event.id))[:12]}"
        item["event_id"] = event_id

        child = db.query(Event).filter(Event.id == event_id).first()
        is_new = child is None
        if child is None:
            child = Event(
                id=event_id,
                title=str(item.get("title") or f"Evento locale · {parent_event.title}"),
                type="local_incident",
                severity=str(item.get("severity") or parent_event.severity or "blue"),
                status=str(item.get("status") or parent_event.status or "ATTENZIONE"),
                lat=float(item.get("lat")) if item.get("lat") is not None else parent_event.lat,
                lon=float(item.get("lon")) if item.get("lon") is not None else parent_event.lon,
                region=str(item.get("region") or parent_event.region or ""),
                parent_event_id=str(parent_event.id),
                subcategory=str(item.get("subcategory") or item.get("type") or "Evento locale"),
                derived_from="news_inference",
                category=_subevent_category(item.get("type"), parent_event),
                is_alert=False,
                started_at=parent_event.started_at or now,
                updated_at=now,
            )
            db.add(child)
            db.flush()
            created += 1
        else:
            changed = False
            next_title = str(item.get("title") or child.title)
            next_severity = str(item.get("severity") or child.severity or parent_event.severity or "blue")
            next_status = str(item.get("status") or child.status or parent_event.status or "ATTENZIONE")
            next_lat = float(item.get("lat")) if item.get("lat") is not None else child.lat
            next_lon = float(item.get("lon")) if item.get("lon") is not None else child.lon
            next_region = str(item.get("region") or child.region or parent_event.region or "")
            next_subcategory = str(item.get("subcategory") or child.subcategory or item.get("type") or "Evento locale")
            next_category = _subevent_category(item.get("type"), parent_event)

            if child.title != next_title:
                child.title = next_title  # type: ignore[assignment]
                changed = True
            if child.severity != next_severity:
                child.severity = next_severity  # type: ignore[assignment]
                changed = True
            if child.status != next_status:
                child.status = next_status  # type: ignore[assignment]
                changed = True
            if child.lat != next_lat:
                child.lat = next_lat  # type: ignore[assignment]
                changed = True
            if child.lon != next_lon:
                child.lon = next_lon  # type: ignore[assignment]
                changed = True
            if child.region != next_region:
                child.region = next_region  # type: ignore[assignment]
                changed = True
            if child.parent_event_id != str(parent_event.id):
                child.parent_event_id = str(parent_event.id)  # type: ignore[assignment]
                changed = True
            if child.subcategory != next_subcategory:
                child.subcategory = next_subcategory  # type: ignore[assignment]
                changed = True
            if child.derived_from != "news_inference":
                child.derived_from = "news_inference"  # type: ignore[assignment]
                changed = True
            if child.category != next_category:
                child.category = next_category  # type: ignore[assignment]
                changed = True
            if changed:
                child.updated_at = now  # type: ignore[assignment]

        article_items = item.get("news") or []
        for evidence in article_items:
            media_url = normalize_absolute_url(evidence.get("url")) or ""
            if not media_url:
                continue
            source_name = str(evidence.get("source") or item.get("source") or domain_name(media_url) or "News")
            source_id = _ensure_subevent_source(db, event_id, "article", source_name, "news_inference", media_url)
            content_hash = canonical_url_hash(f"{event_id}::article::{media_url}")
            if content_hash in seen_hashes:
                continue
            exists = db.query(MediaItem).filter(MediaItem.content_hash == content_hash).first()
            if exists is not None:
                seen_hashes.add(content_hash)
                continue
            db.add(MediaItem(
                event_id=event_id,
                source_id=source_id,
                media_url=media_url,
                thumb_url=normalize_absolute_url(evidence.get("thumb_url")),
                media_type="article",
                caption=str(evidence.get("title") or item.get("title") or "articolo"),
                author=source_name,
                lat=float(item.get("lat")) if item.get("lat") is not None else None,
                lon=float(item.get("lon")) if item.get("lon") is not None else None,
                geo_raw=str(item.get("place_name") or item.get("region") or ""),
                captured_at=parse_published_datetime(str(evidence.get("published") or "")),
                confidence=max(45, int(item.get("relevance") or 0)),
                content_hash=content_hash,
            ))
            seen_hashes.add(content_hash)

        video_items = item.get("videos") or []
        for video in video_items:
            media_url = normalize_absolute_url(video.get("url") or video.get("media_url")) or ""
            if not media_url:
                continue
            platform = str(video.get("platform") or "video_inference")
            source_name = str(video.get("source") or platform or "Video")
            source_id = _ensure_subevent_source(db, event_id, "video", source_name, platform, media_url)
            content_hash = canonical_url_hash(f"{event_id}::video::{media_url}")
            if content_hash in seen_hashes:
                continue
            exists = db.query(MediaItem).filter(MediaItem.content_hash == content_hash).first()
            if exists is not None:
                seen_hashes.add(content_hash)
                continue
            db.add(MediaItem(
                event_id=event_id,
                source_id=source_id,
                media_url=media_url,
                thumb_url=normalize_absolute_url(video.get("thumb_url")),
                media_type="video",
                caption=str(video.get("title") or item.get("title") or "video locale"),
                author=source_name,
                lat=float(item.get("lat")) if item.get("lat") is not None else None,
                lon=float(item.get("lon")) if item.get("lon") is not None else None,
                geo_raw=str(item.get("place_name") or item.get("region") or ""),
                captured_at=parse_published_datetime(str(video.get("published") or "")),
                confidence=max(55, int(item.get("relevance") or 0)),
                content_hash=content_hash,
            ))
            seen_hashes.add(content_hash)

    db.flush()
    return created


async def _derive_and_persist_subevents_for_event(db: Session, event: Event) -> int:
    if not event or event.parent_event_id:
        return 0
    news_items = await event_news(str(event.id), db)
    videos = _event_live_public_videos(event, limit=8)
    rows = _infer_subevents_from_news(event, news_items, videos=videos)
    if not rows:
        return 0
    return _persist_subevents(db, event, rows)


def _warm_recent_subevents(db: Session, limit: int = 12) -> int:
    global _SUBEVENT_GLOBAL_WARM_TS
    now_ts = time.time()
    if (now_ts - _SUBEVENT_GLOBAL_WARM_TS) < SUBEVENT_GLOBAL_WARM_TTL_SEC:
        return 0

    events = (
        db.query(Event)
        .filter(or_(Event.parent_event_id.is_(None), Event.parent_event_id == ""))
        .order_by(Event.updated_at.desc())
        .limit(max(1, min(30, int(limit))))
        .all()
    )
    total_created = 0
    for event in events:
        if not _is_in_europe(event.lat, event.lon):
            continue
        try:
            total_created += asyncio.run(_derive_and_persist_subevents_for_event(db, event))
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(f"subevent warmup skip for {event.id}: {exc}")
    _SUBEVENT_GLOBAL_WARM_TS = time.time()
    return total_created


def _infer_event_anchor(event: Event) -> dict | None:
    text = normalize_text(f"{event.title or ''} {event.region or ''}")

    for token, anchor in _ITALY_PLACE_HINTS.items():
        if normalize_text(token) in text:
            return {
                "name": anchor["name"],
                "lat": float(anchor["lat"]),
                "lon": float(anchor["lon"]),
                "source": "title_or_region_hint",
                "precision": "place",
            }

    region_norm = normalize_text(str(event.region or "")).replace("  ", " ")
    for key, anchor in _ITALY_REGION_ANCHORS.items():
        if normalize_text(key) == region_norm:
            return {
                "name": anchor["name"],
                "lat": float(anchor["lat"]),
                "lon": float(anchor["lon"]),
                "source": "regional_capital_fallback",
                "precision": "region",
            }
    return None


# ─── REST endpoints ───────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "name": "Vigil API",
        "version": "0.2.0",
        "status": "ok",
        "endpoints": {
            "health": "/health",
            "events": "/events",
            "ws_events": "ws://…/ws/events",
            "collectors_status": "/collectors/status",
        },
    }


@app.get("/health")
def health(db: Session = Depends(get_db)):
    return {"status": "ok", "collectors_status": _collectors_aggregate_status(db)}


def _should_expose_event(event: Event) -> bool:
    category = str(event.category or event.type or "").strip().lower()
    derived_from = str(event.derived_from or "").strip().lower()
    if category == "wildfire" and derived_from == "rss_local_wildfire":
        try:
            from vigil.collectors.rss_local import is_probably_italian_wildfire_story, looks_like_wildfire_story
            return looks_like_wildfire_story(str(event.title or "")) and is_probably_italian_wildfire_story(
                str(event.title or ""),
                region=str(event.region or ""),
            )
        except Exception:
            text = _normalize_search(f"{event.title or ''} {event.region or ''}")
            if "abu dhabi" in text or "borouge" in text or "uae" in text:
                return False
    return True


def _normalize_exposed_event_row(row: dict) -> dict:
    category = str(row.get("category") or row.get("type") or "").strip().lower()
    derived_from = str(row.get("derived_from") or "").strip().lower()
    if category == "wildfire" and derived_from == "rss_local_wildfire":
        try:
            from vigil.collectors.rss_local import _infer_region_label
            title = str(row.get("title") or "")
            region = str(row.get("region") or "")
            better_region = _infer_region_label(f"{title}\n{region}", "")
            if better_region and (not region or region == "Italia"):
                row["region"] = better_region
            if row.get("lat") in (None, 41.87) or row.get("lon") in (None, 12.57):
                from vigil.core.geo import extract_coordinates
                lat, lon, _, _ = extract_coordinates(f"{title}\n{row.get('region') or ''}", use_geocoder=False)
                if lat is not None and lon is not None:
                    row["lat"] = lat
                    row["lon"] = lon
        except Exception:
            pass
    return row


@app.get("/events")
def list_events(
    severity: str = Query(None, description="red, orange, blue"),
    type: str = Query(None, description="cyclone, flood, storm, …"),
    include_children: bool = Query(False, description="Include persisted local subevents"),
    db: Session = Depends(get_db),
):
    platform_rows = (
        db.query(
            Source.event_id,
            func.group_concat(func.distinct(Source.platform)).label("platforms"),
        )
        .filter(Source.event_id.isnot(None))
        .group_by(Source.event_id)
        .all()
    )
    platforms_by_event = {
        event_id: [p.strip() for p in (platforms or "").split(",") if p]
        for event_id, platforms in platform_rows
    }
    child_rows = (
        db.query(Event.parent_event_id, func.count(Event.id))
        .filter(Event.parent_event_id.isnot(None), Event.parent_event_id != "")
        .group_by(Event.parent_event_id)
        .all()
    )
    child_count_by_parent = {str(parent_id): int(count or 0) for parent_id, count in child_rows if parent_id}

    q = (
        db.query(Event, func.count(MediaItem.id).label("media_count"))
        .outerjoin(MediaItem, MediaItem.event_id == Event.id)
        .group_by(Event.id)
    )
    if severity:
        q = q.filter(Event.severity == severity)
    if type:
        q = q.filter(Event.type == type)
    if not include_children:
        q = q.filter(or_(Event.parent_event_id.is_(None), Event.parent_event_id == ""))
    q = q.order_by(Event.updated_at.desc())

    payload = []
    for event, media_count in q.all():
        if not _is_in_europe(event.lat, event.lon):
            continue
        if not _should_expose_event(event):
            continue
        row = _normalize_exposed_event_row(event.to_dict())
        platforms = platforms_by_event.get(event.id, [])
        row["media_count"] = int(media_count or 0)
        row["local_incident_count"] = int(child_count_by_parent.get(str(event.id), 0))
        row["platforms"] = platforms
        row["primary_platform"] = platforms[0] if platforms else None
        payload.append(row)
    return _with_last_updated(payload)


@app.get("/events/search")
def search_events(
    q: str = Query(..., min_length=1),
    severity: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    tokens = _normalize_search(q).split()
    if not tokens:
        return []
    queries = db.query(Event)
    if severity:
        queries = queries.filter(Event.severity == severity)
    candidates = [
        event for event in queries.order_by(Event.updated_at.desc()).all()
        if _is_in_europe(event.lat, event.lon)
    ]
    results = []
    for event in candidates:
        haystack = _normalize_search(
            f"{event.title} {event.region} {event.type} {event.status}"
        )
        if not all(tok in haystack for tok in tokens):
            captions = db.query(MediaItem.caption).filter(
                MediaItem.event_id == event.id,
                MediaItem.caption.isnot(None),
            ).all()
            caption_text = _normalize_search(" ".join(c[0] for c in captions if c[0]))
            combined = haystack + " " + caption_text
        else:
            combined = haystack
        if all(tok in combined for tok in tokens):
            results.append(event.to_dict())
            if len(results) >= limit:
                break
    return _with_last_updated(results)


@app.get("/geo/wind-field")
async def geo_wind_field(
    min_lat: float = Query(...),
    max_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lon: float = Query(...),
    zoom: int = Query(6, ge=3, le=12),
    db: Session = Depends(get_db),
):
    """Sample real wind vectors on a lightweight grid for a Windy-like frontend layer."""
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(status_code=422, detail="Bounding box non valido")

    cache_key = (
        round(min_lat, 2),
        round(max_lat, 2),
        round(min_lon, 2),
        round(max_lon, 2),
        int(zoom),
    )
    now = time.time()
    cached = _WIND_FIELD_CACHE.get(cache_key)
    if cached and (now - cached[0]) <= WIND_FIELD_CACHE_TTL_SEC:
        return cached[1]

    rows = 5 if zoom <= 4 else 7 if zoom <= 6 else 8 if zoom <= 8 else 9
    cols = 6 if zoom <= 4 else 9 if zoom <= 6 else 10 if zoom <= 8 else 12
    lat_step = (max_lat - min_lat) / max(rows - 1, 1)
    lon_step = (max_lon - min_lon) / max(cols - 1, 1)
    samples = [
        (round(min_lat + (r * lat_step), 3), round(min_lon + (c * lon_step), 3))
        for r in range(rows)
        for c in range(cols)
    ]

    points: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            tasks = [
                client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "current": "wind_speed_10m,wind_direction_10m",
                        "timezone": "auto",
                    },
                )
                for lat, lon in samples
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for (lat, lon), result in zip(samples, results):
            if isinstance(result, Exception):
                continue
            try:
                result.raise_for_status()
                current = (result.json() or {}).get("current") or {}
                speed = current.get("wind_speed_10m")
                direction = current.get("wind_direction_10m")
                if speed is None:
                    continue
                points.append({
                    "lat": float(lat),
                    "lon": float(lon),
                    "wind_kmh": float(speed),
                    "direction_deg": float(direction) if direction is not None else None,
                })
            except Exception:
                continue
    except Exception as exc:
        logger.warning(f"Wind field fetch fallito: {exc}")

    if not points:
        fallback_rows = (
            db.query(Event)
            .filter(Event.lat >= min_lat, Event.lat <= max_lat, Event.lon >= min_lon, Event.lon <= max_lon)
            .filter(Event.wind_kmh.isnot(None))
            .order_by(Event.updated_at.desc())
            .limit(80)
            .all()
        )
        for event in fallback_rows:
            if event.lat is None or event.lon is None:
                continue
            direction = hash((round(float(event.lat), 2), round(float(event.lon), 2), int(event.wind_kmh or 0))) % 360
            points.append({
                "lat": float(event.lat),
                "lon": float(event.lon),
                "wind_kmh": float(event.wind_kmh or 0.0),
                "direction_deg": float(direction),
            })

    payload = {
        "generated_at": _utc_now_naive().isoformat(),
        "source": "open-meteo" if points else "fallback",
        "points": points,
    }
    _WIND_FIELD_CACHE[cache_key] = (now, payload)
    return payload


@app.get("/geo/hydro-rivers")
async def geo_hydro_rivers(
    min_lat: float = Query(...),
    max_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lon: float = Query(...),
    limit: int = Query(20, ge=1, le=40),
    db: Session = Depends(get_db),
):
    """Return river paths in the visible bbox, colored by the worst hydrometric level among visible stations."""
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(status_code=422, detail="Bounding box non valido")

    cache_key = (
        round(min_lat, 2),
        round(max_lat, 2),
        round(min_lon, 2),
        round(max_lon, 2),
        int(limit),
    )
    now = time.time()
    cached = _HYDRO_RIVERS_CACHE.get(cache_key)
    if cached and (now - cached[0]) <= HYDRO_RIVERS_CACHE_TTL_SEC:
        return cached[1]

    rows = (
        db.query(HydroStation)
        .filter(
            HydroStation.lat >= min_lat,
            HydroStation.lat <= max_lat,
            HydroStation.lon >= min_lon,
            HydroStation.lon <= max_lon,
            HydroStation.river.isnot(None),
        )
        .order_by(HydroStation.updated_at.desc())
        .all()
    )

    river_groups: dict[str, dict] = {}
    rank = {"normal": 1, "moderate": 2, "high": 3}
    color_map = {"normal": "#3fb950", "moderate": "#d29922", "high": "#f85149"}

    for hs in rows:
        name = str(hs.river or "").strip()
        if not name:
            continue
        entry = river_groups.setdefault(name, {"stations": [], "level": "normal", "color": "#3fb950"})
        level = str(hs.hydro_level or "normal").strip().lower()
        color = color_map.get(level, "#3fb950")
        entry["stations"].append({
            "lat": float(hs.lat),
            "lon": float(hs.lon),
            "level": level,
            "color": color,
            "name": hs.name,
            "discharge_m3s": float(hs.discharge_m3s) if hs.discharge_m3s is not None else None,
        })
        if rank.get(level, 1) >= rank.get(entry["level"], 1):
            entry["level"] = level
            entry["color"] = color_map.get(level, "#3fb950")

    names = list(river_groups.keys())[:limit]
    if not names:
        return {"count": 0, "rivers": []}

    escaped = [re.escape(name) for name in names if name]
    name_regex = "|".join(escaped)
    remote_paths: dict[str, list[list[list[float]]]] = {name: [] for name in names}

    if name_regex:
        query = (
            f'[out:json][timeout:18];('
            f'way["waterway"~"river|stream|canal"]["name"~"({name_regex})",i]'
            f'({min_lat},{min_lon},{max_lat},{max_lon});'
            f'way["natural"="water"]["name"~"({name_regex})",i]'
            f'({min_lat},{min_lon},{max_lat},{max_lon});'
            f'relation["waterway"]["name"~"({name_regex})",i]'
            f'({min_lat},{min_lon},{max_lat},{max_lon});'
            f');out geom;'
        )
        overpass_endpoints = [
            "https://overpass-api.de/api/interpreter",
            "https://lz4.overpass-api.de/api/interpreter",
        ]
        for endpoint in overpass_endpoints:
            try:
                async with httpx.AsyncClient(timeout=12, headers={"User-Agent": "vigil-backend/0.2 (hydro lookup)"}) as client:
                    resp = await client.get(endpoint, params={"data": query})
                    resp.raise_for_status()
                    data = resp.json() or {}
                for element in data.get("elements", []):
                    tags = element.get("tags") or {}
                    river_name = str(tags.get("name") or "").strip()
                    geom = element.get("geometry") or []
                    coords = [[float(node["lat"]), float(node["lon"])] for node in geom if "lat" in node and "lon" in node]
                    if len(coords) < 2:
                        continue
                    for target_name in names:
                        if _river_name_matches(target_name, river_name):
                            remote_paths[target_name].append(coords)
                if any(remote_paths.values()):
                    break
            except Exception as exc:
                logger.warning(f"Hydro rivers fetch fallito da {endpoint}: {exc}")

    missing_names = [name for name in names if not remote_paths.get(name)]
    for name in missing_names:
        try:
            remote_paths[name] = await _fetch_nominatim_river_paths(name, min_lat, max_lat, min_lon, max_lon)
        except Exception as exc:
            logger.warning(f"Hydro rivers Nominatim fallback fallito per {name}: {exc}")

    rivers = []
    for name in names:
        entry = river_groups.get(name) or {}
        paths = remote_paths.get(name) or []
        if not paths:
            fallback_points = sorted(entry.get("stations") or [], key=lambda item: (item.get("lon", 0.0), item.get("lat", 0.0)))
            fallback = [[float(item.get("lat", 0.0)), float(item.get("lon", 0.0))] for item in fallback_points]
            if len(fallback) >= 2:
                if len(fallback) <= 2:
                    paths = [fallback]
                else:
                    split_paths: list[list[list[float]]] = []
                    current_path: list[list[float]] = [fallback[0]]
                    for coord in fallback[1:]:
                        prev = current_path[-1]
                        gap = ((coord[0] - prev[0]) ** 2 + (coord[1] - prev[1]) ** 2) ** 0.5
                        if gap > 1.2:
                            if len(current_path) >= 2:
                                split_paths.append(current_path)
                            current_path = [coord]
                        else:
                            current_path.append(coord)
                    if len(current_path) >= 2:
                        split_paths.append(current_path)
                    paths = split_paths
        if not paths:
            continue
        rivers.append({
            "river": name,
            "level": entry.get("level") or "normal",
            "color": entry.get("color") or "#3fb950",
            "paths": paths,
            "stations": entry.get("stations") or [],
        })

    payload = {"count": len(rivers), "rivers": rivers}
    _HYDRO_RIVERS_CACHE[cache_key] = (now, payload)
    return payload


@app.get("/geo/live-overlays")
def geo_live_overlays(
    min_lat: float = Query(...),
    max_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lon: float = Query(...),
    zoom: int = Query(6, ge=3, le=12),
    db: Session = Depends(get_db),
):
    """Live meteo/idrometria/pluviometria/vento su bbox corrente mappa."""
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(status_code=422, detail="Bounding box non valido")

    cache_key = (
        round(min_lat, 2),
        round(max_lat, 2),
        round(min_lon, 2),
        round(max_lon, 2),
        int(zoom),
    )
    now = time.time()
    cached = _LIVE_OVERLAYS_CACHE.get(cache_key)
    if cached and (now - cached[0]) <= LIVE_OVERLAYS_CACHE_TTL_SEC:
        return cached[1]

    events = (
        db.query(Event)
        .filter(Event.lat >= min_lat, Event.lat <= max_lat, Event.lon >= min_lon, Event.lon <= max_lon)
        .order_by(Event.updated_at.desc())
        .limit(250)
        .all()
    )

    points = []
    for event in events:
        if event.lat is None or event.lon is None:
            continue
        precip = float(event.precipitation_mm or 0.0)
        wind = float(event.wind_kmh or 0.0)
        level, color, idx = _hydro_from_local(precip, wind)
        points.append({
            "lat": float(event.lat),
            "lon": float(event.lon),
            "event_id": event.id,
            "weather": {
                "temp_c": float(event.temp_c) if event.temp_c is not None else None,
                "precipitation_mm": precip,
                "rain_mm": precip,
                "wind_kmh": wind,
                "wind_gust_kmh": None,
            },
            "hydro": {
                "river_discharge_max": None,
                "hydro_level": level,
                "hydro_color": color,
                "hydro_index_estimate": idx,
                "source": "event-estimated",
            },
        })

    payload = {
        "generated_at": _utc_now_naive().isoformat(),
        "bbox": {
            "min_lat": float(min_lat),
            "max_lat": float(max_lat),
            "min_lon": float(min_lon),
            "max_lon": float(max_lon),
            "zoom": int(zoom),
        },
        "points": points,
    }
    _LIVE_OVERLAYS_CACHE[cache_key] = (now, payload)
    return payload


@app.get("/geo/stations")
def geo_stations(
    min_lat: float = Query(...),
    max_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lon: float = Query(...),
    limit: int = Query(80, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get hydrometric and weather stations within bounding box.
    
    Priority order:
      1. ARPA measured stations (real hydrometric data)
      2. Event-based stations (local estimation from weather events)
    
    Returns up to `limit` stations, prioritizing measured data.
    """
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(status_code=422, detail="Bounding box non valido")

    stations_dict = {}  # id -> station_dict for deduplication
    
    # 1. Prioritize ARPA HydroStation records (measured data)
    hydro_rows = (
        db.query(HydroStation)
        .filter(
            HydroStation.lat >= min_lat,
            HydroStation.lat <= max_lat,
            HydroStation.lon >= min_lon,
            HydroStation.lon <= max_lon,
        )
        .order_by(HydroStation.updated_at.desc())
        .all()
    )
    
    for hs in hydro_rows[:limit]:
        level = hs.hydro_level or "normal"
        # Map hydro level to color
        color_map = {"high": "#f85149", "moderate": "#d29922", "normal": "#3fb950"}
        color = color_map.get(level, "#3fb950")
        
        station_dict = {
            "id": hs.id,
            "name": hs.name,
            "lat": float(hs.lat),
            "lon": float(hs.lon),
            "type": "hydro_station",
            "provider": hs.provider,
            "river": hs.river,
            "water_level_m": float(hs.water_level_m) if hs.water_level_m is not None else None,
            "discharge_m3s": float(hs.discharge_m3s) if hs.discharge_m3s is not None else None,
            "discharge_max_m3s": float(hs.discharge_max_m3s) if hs.discharge_max_m3s is not None else None,
            "precip_mm": float(hs.precip_mm) if hs.precip_mm is not None else None,
            "precip_24h_mm": float(hs.precip_24h_mm) if hs.precip_24h_mm is not None else None,
            "hydro_level": level,
            "hydro_color": color,
            "hydro_index": float(hs.hydro_index) if hs.hydro_index is not None else 0.0,
            "data_quality": str(hs.data_quality or "synthetic"),
            "data_source": hs.data_source or "measured",
            "updated_at": hs.updated_at.isoformat() if hs.updated_at else None,
        }
        stations_dict[hs.id] = station_dict
    
    # 2. Supplement with Event-based stations (fallback for areas without ARPA coverage)
    remaining_limit = limit - len(stations_dict)
    if remaining_limit > 0:
        event_rows = (
            db.query(Event)
            .filter(
                Event.lat >= min_lat,
                Event.lat <= max_lat,
                Event.lon >= min_lon,
                Event.lon <= max_lon,
            )
            .order_by(Event.updated_at.desc())
            .limit(remaining_limit)
            .all()
        )
        
        for ev in event_rows:
            if ev.lat is None or ev.lon is None:
                continue
            
            # Skip if this location is already covered by ARPA data
            # (check if within 0.1 degrees ~11km)
            skip = False
            for existing_id, existing_station in stations_dict.items():
                lat_diff = abs(ev.lat - existing_station["lat"])
                lon_diff = abs(ev.lon - existing_station["lon"])
                if lat_diff < 0.1 and lon_diff < 0.1:
                    skip = True
                    break
            
            if skip:
                continue
            
            precip = float(ev.precipitation_mm or 0.0)
            wind = float(ev.wind_kmh or 0.0)
            level, color, idx = _hydro_from_local(precip, wind)
            
            station_dict = {
                "id": ev.id,
                "name": ev.region or ev.title,
                "lat": float(ev.lat),
                "lon": float(ev.lon),
                "type": "weather_event",
                "temp_c": float(ev.temp_c) if ev.temp_c is not None else None,
                "precip_mm": precip,
                "wind_kmh": wind,
                "hydro_level": level,
                "hydro_color": color,
                "hydro_index": idx,
                "data_quality": "estimated",
                "data_source": "estimated",
                "updated_at": ev.updated_at.isoformat() if ev.updated_at else None,
            }
            stations_dict[ev.id] = station_dict
    
    stations = list(stations_dict.values())
    
    return {
        "count": len(stations),
        "stations": stations,
    }


@app.get("/geo/synoptic-maps")
def geo_synoptic_maps():
    """Public weather-chart sources for synoptic/isobar viewing in the UI."""
    return {
        "generated_at": _utc_now_naive().isoformat(),
        "charts": [
            {
                "id": "surface-analysis-global",
                "title": "Carta sinottica globale · isobare (NOAA)",
                "kind": "isobars",
                "url": "https://ocean.weather.gov/P_sfc_full_ocean_color.png",
                "source": "NOAA Ocean Prediction Center",
            },
            {
                "id": "surface-analysis-atlantic",
                "title": "Pressione al suolo Nord Atlantico / Europa (DWD)",
                "kind": "synoptic",
                "url": "https://www.dwd.de/DWD/wetter/wv_spez/hobbymet/wetterkarten/bwk_bodendruck_na_ana.png",
                "source": "Deutscher Wetterdienst",
            },
        ],
    }


@app.get("/geo/territory-summary")
def geo_territory_summary(
    min_lat: float = Query(...),
    max_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lon: float = Query(...),
    zoom: int = Query(7, ge=3, le=12),
    focus_name: str = Query(""),
    db: Session = Depends(get_db),
):
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(status_code=422, detail="Bounding box non valido")

    cache_key = (
        round(min_lat, 2),
        round(max_lat, 2),
        round(min_lon, 2),
        round(max_lon, 2),
        int(zoom),
        (focus_name or "").strip().lower(),
    )
    now = time.time()
    cached = _TERRITORY_CACHE.get(cache_key)
    if cached and (now - cached[0]) <= TERRITORY_CACHE_TTL_SEC:
        return cached[1]

    events = (
        db.query(Event)
        .filter(Event.lat >= min_lat, Event.lat <= max_lat, Event.lon >= min_lon, Event.lon <= max_lon)
        .order_by(Event.updated_at.desc())
        .limit(400)
        .all()
    )

    n = len(events)
    temps = [float(e.temp_c) for e in events if e.temp_c is not None]
    winds = [float(e.wind_kmh) for e in events if e.wind_kmh is not None]
    precs = [float(e.precipitation_mm) for e in events if e.precipitation_mm is not None]

    hydro_counts = {"high": 0, "moderate": 0, "normal": 0}
    for e in events:
        level, _, _ = _hydro_from_local(float(e.precipitation_mm or 0.0), float(e.wind_kmh or 0.0))
        if level in hydro_counts:
            hydro_counts[level] += 1
    
    # Include ARPA hydrometric station data for comprehensive hydro assessment
    arpa_stations = (
        db.query(HydroStation)
        .filter(
            HydroStation.lat >= min_lat,
            HydroStation.lat <= max_lat,
            HydroStation.lon >= min_lon,
            HydroStation.lon <= max_lon,
        )
        .all()
    )
    for hs in arpa_stations:
        if hs.hydro_level and hs.hydro_level in hydro_counts:
            hydro_counts[hs.hydro_level] += 1

    sev_rank = {"red": 3, "orange": 2, "blue": 1}
    top_alerts = sorted(
        [e for e in events],
        key=lambda e: (sev_rank.get((e.severity or "").lower(), 0), e.updated_at or _utc_now_naive()),
        reverse=True,
    )[:8]

    payload = {
        "generated_at": _utc_now_naive().isoformat(),
        "focus": {
            "name": focus_name or "Area selezionata",
            "bbox": {
                "min_lat": float(min_lat),
                "max_lat": float(max_lat),
                "min_lon": float(min_lon),
                "max_lon": float(max_lon),
                "zoom": int(zoom),
            },
        },
        "metrics": {
            "event_count": n,
            "temp_avg_c": round(sum(temps) / len(temps), 1) if temps else None,
            "wind_avg_kmh": round(sum(winds) / len(winds), 1) if winds else None,
            "precip_avg_mm": round(sum(precs) / len(precs), 1) if precs else None,
            "hydro_levels": hydro_counts,
        },
        "top_alerts": [
            {
                "id": e.id,
                "title": e.title,
                "region": e.region,
                "severity": e.severity,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            }
            for e in top_alerts
        ],
    }
    _TERRITORY_CACHE[cache_key] = (now, payload)
    return payload


@app.get("/events/{event_id}")
def get_event(event_id: str, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento non trovato")
    return _with_last_updated(event.to_dict(), event.updated_at)


@app.get("/events/{event_id}/media")
def list_media(
    event_id: str,
    platform: str = Query(None),
    min_confidence: int = Query(0, ge=0, le=100),
    min_quality: int = Query(0, ge=0, le=100),
    limit: int = Query(50, ge=1, le=200),
    sort_by: str = Query("quality", description="quality or confidence"),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(Event.id == event_id).first()
    q = db.query(MediaItem).filter(MediaItem.event_id == event_id)
    if platform:
        source_ids = [
            s.id for s in db.query(Source).filter(Source.platform == platform).all()
        ]
        q = q.filter(MediaItem.source_id.in_(source_ids))
    q = q.filter(MediaItem.confidence >= min_confidence)
    q = q.order_by(MediaItem.fetched_at.desc())
    items = q.limit(max(200, limit)).all()

    payload = []
    for m in items:
        row = m.to_dict()
        row["quality_score"] = quality_score(
            confidence=int(row.get("confidence") or 0),
            media_type=str(row.get("media_type") or ""),
            platform=row.get("platform"),
            captured_at=m.captured_at,
            fetched_at=m.fetched_at,
            has_thumb=bool(row.get("thumb_url")),
        )
        relevance = 100
        media_type_value = str(row.get("media_type") or "")
        media_platform = str(row.get("platform") or "").lower()
        if event is not None:
            title_text = _title_from_caption(row.get("caption")) or str(row.get("caption") or row.get("author") or "")
            published_dt = m.captured_at or m.fetched_at
            relevance = _event_news_relevance(
                event,
                f"{title_text}\n{row.get('caption') or ''}\n{row.get('author') or ''}",
                published_dt,
            )
            row["relevance"] = relevance
            if media_type_value == "article" and relevance < 40:
                continue
            if media_type_value in {"image", "video", "webcam"} and media_platform in {"peertube", "openverse", "wikimedia"} and relevance < 28:
                continue
            if media_type_value in {"article", "image", "video", "webcam"}:
                row["quality_score"] = int(round(row["quality_score"] * 0.65 + relevance * 0.35))
        if int(row["quality_score"] or 0) < min_quality:
            continue
        row["is_native_visual"] = str(row.get("media_type") or "") in {"image", "video", "webcam"}
        payload.append(row)

    mode = (sort_by or "quality").strip().lower()
    if mode == "confidence":
        payload.sort(key=lambda r: (bool(r.get("is_native_visual")), int(r.get("confidence") or 0), int(r.get("quality_score") or 0)), reverse=True)
    else:
        payload.sort(key=lambda r: (bool(r.get("is_native_visual")), int(r.get("quality_score") or 0), int(r.get("relevance") or 0), int(r.get("confidence") or 0)), reverse=True)
    return _with_last_updated(payload[:limit])


@app.get("/events/stats/media-rich")
def list_media_rich_events(
    limit: int = Query(20, ge=1, le=100),
    include_zero: bool = Query(False),
    min_total: int = Query(1, ge=0, le=5000),
    scope: str = Query("all", description="all, europe, italy"),
    rank_by: str = Query("count", description="count or quality"),
    db: Session = Depends(get_db),
):
    """Ritorna gli eventi ordinati per quantita' di media salvati (article/image/video/webcam)."""
    cache_key = (
        int(limit),
        bool(include_zero),
        int(min_total),
        str(scope or "all").strip().lower(),
        str(rank_by or "count").strip().lower(),
    )
    now = time.time()
    cached = _MEDIA_RICH_CACHE.get(cache_key)
    if cached and (now - cached[0]) <= MEDIA_RICH_CACHE_TTL_SEC:
        return _with_last_updated(cached[1])

    events = db.query(Event).order_by(Event.updated_at.desc()).limit(500).all()
    events_by_id = {str(event.id): event for event in events}

    by_event: dict[str, dict[str, int]] = {}
    quality_by_event: dict[str, dict[str, float]] = {}
    media_rows = (
        db.query(
            MediaItem.event_id,
            MediaItem.media_type,
            MediaItem.confidence,
            MediaItem.captured_at,
            MediaItem.fetched_at,
            MediaItem.thumb_url,
            MediaItem.caption,
            Source.platform,
        )
        .outerjoin(Source, MediaItem.source_id == Source.id)
        .all()
    )
    for event_id, media_type, confidence, captured_at, fetched_at, thumb_url, caption, platform in media_rows:
        eid = str(event_id)
        event = events_by_id.get(eid)
        mt = (media_type or "other").strip().lower()
        if mt not in ("article", "image", "video", "webcam"):
            mt = "other"

        relevance = 100
        platform_key = str(platform or "").lower()
        if event is not None:
            title_text = _title_from_caption(caption) or str(caption or "")
            relevance = _event_news_relevance(event, f"{title_text}\n{caption or ''}", captured_at or fetched_at)
            if mt == "article" and relevance < 40:
                continue
            if mt in {"image", "video", "webcam"} and platform_key in {"peertube", "openverse", "wikimedia"} and relevance < 28:
                continue

        ev = by_event.setdefault(eid, {
            "article": 0,
            "image": 0,
            "video": 0,
            "webcam": 0,
            "other": 0,
            "total": 0,
        })
        ev[mt] += 1
        ev["total"] += 1

        q = float(quality_score(
            confidence=int(confidence or 0),
            media_type=str(media_type or ""),
            platform=(platform or ""),
            captured_at=captured_at,
            fetched_at=fetched_at,
            has_thumb=bool(thumb_url),
        ))
        if mt in {"article", "image", "video", "webcam"}:
            q = round(q * 0.65 + relevance * 0.35, 1)
        agg = quality_by_event.setdefault(eid, {"sum": 0.0, "count": 0.0, "top": 0.0})
        agg["sum"] += q
        agg["count"] += 1.0
        if q > agg["top"]:
            agg["top"] = q

    rows = []
    scope_normalized = (scope or "all").strip().lower()
    for event in events:
        counts = by_event.get(str(event.id), {
            "article": 0,
            "image": 0,
            "video": 0,
            "webcam": 0,
            "other": 0,
            "total": 0,
        })
        if not include_zero and counts["total"] == 0:
            continue
        if counts["total"] < min_total:
            continue
        if scope_normalized == "europe" and not _is_in_europe(event.lat, event.lon):
            continue
        if scope_normalized == "italy" and not _is_in_italy(event.lat, event.lon):
            continue
        qagg = quality_by_event.get(str(event.id), {"sum": 0.0, "count": 0.0, "top": 0.0})
        qavg = (qagg["sum"] / qagg["count"]) if qagg["count"] > 0 else 0.0
        rows.append({
            "event_id": event.id,
            "title": event.title,
            "category": event.category,
            "severity": event.severity,
            "lat": event.lat,
            "lon": event.lon,
            "updated_at": event.updated_at.isoformat() if event.updated_at else None,
            "counts": counts,
            "quality": {
                "avg": round(float(qavg), 1),
                "top": int(round(float(qagg["top"]))),
            },
        })

    mode = (rank_by or "count").strip().lower()
    if mode == "quality":
        rows.sort(
            key=lambda r: (
                float(r["quality"]["avg"]),
                int(r["quality"]["top"]),
                int(r["counts"]["total"]),
                r["updated_at"] or "",
            ),
            reverse=True,
        )
    else:
        rows.sort(key=lambda r: (r["counts"]["total"], r["updated_at"] or ""), reverse=True)
    result = rows[:limit]
    _MEDIA_RICH_CACHE[cache_key] = (now, result)
    return _with_last_updated(result)


@app.get("/events/stats/media-rich/top-italy")
def list_media_rich_events_top_italy(
    limit: int = Query(20, ge=1, le=100),
    min_total: int = Query(1, ge=1, le=5000),
    rank_by: str = Query("quality", description="count or quality"),
    db: Session = Depends(get_db),
):
    """Top eventi in Italia ordinati per quantita' media per trovare rapidamente quelli popolati."""
    return list_media_rich_events(
        limit=limit,
        include_zero=False,
        min_total=min_total,
        scope="italy",
        rank_by=rank_by,
        db=db,
    )


@app.get("/events/stats/media-rich/top-europe")
def list_media_rich_events_top_europe(
    limit: int = Query(20, ge=1, le=100),
    min_total: int = Query(1, ge=1, le=5000),
    rank_by: str = Query("quality", description="count or quality"),
    db: Session = Depends(get_db),
):
    """Top eventi in Europa ordinati per quantita' media per trovare rapidamente quelli popolati."""
    return list_media_rich_events(
        limit=limit,
        include_zero=False,
        min_total=min_total,
        scope="europe",
        rank_by=rank_by,
        db=db,
    )


@app.get("/events/{event_id}/sources")
def list_sources(event_id: str, db: Session = Depends(get_db)):
    rows = [s.to_dict() for s in db.query(Source).filter(Source.event_id == event_id).all()]
    return _with_last_updated(rows)


@app.get("/events/{event_id}/news")
async def event_news(event_id: str, db: Session = Depends(get_db)):
    """Ritorna notizie recenti e pertinenti gia persistite nel DB per l'evento."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento non trovato")

    now = _utc_now_naive()
    anchor = event.started_at or event.updated_at or now
    cache_key = (event_id, (event.updated_at or event.started_at or now).isoformat())
    cached_entry = _EVENT_NEWS_CACHE.get(cache_key)
    if cached_entry and (time.time() - cached_entry[0]) < EVENT_NEWS_CACHE_TTL_SEC:
        return _with_last_updated(cached_entry[1], event.updated_at or event.started_at or now)

    max_age_days = 21 if (now - anchor).days <= 30 else 180
    fresh_cutoff = now - timedelta(days=max_age_days)

    items = []
    seen: set[str] = set()

    # 1) Priorita agli articoli gia salvati nel DB per questo evento
    db_rows = (
        db.query(MediaItem, Source)
        .outerjoin(Source, MediaItem.source_id == Source.id)
        .filter(MediaItem.event_id == event_id, MediaItem.media_type == "article")
        .filter(MediaItem.confidence >= 25)
        .order_by(MediaItem.captured_at.desc(), MediaItem.fetched_at.desc())
        .limit(80)
        .all()
    )
    for media_item, source in db_rows:
        title = _title_from_caption(media_item.caption) or (media_item.media_url or "")
        if not title or not media_item.media_url:
            continue
        published_dt = media_item.captured_at or media_item.fetched_at
        if published_dt is not None and published_dt < fresh_cutoff:
            continue
        relevance = _event_news_relevance(event, title, published_dt)
        relevance_score = media_item.relevance_score
        if relevance_score is None:
            relevance_score = score_article_relevance(
                title,
                media_item.caption or "",
                media_item.media_url or "",
                media_item.author or (source.name if source else None) or "",
            )
        if relevance_score < MIN_RELEVANCE_SCORE:
            continue
        key = f"db::{normalize_text(title)}::{media_item.media_url}"
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "title": title,
            "url": media_item.media_url,
            "published": published_dt.isoformat() if published_dt else None,
            "source": media_item.author or (source.name if source else None) or (source.platform if source else None),
            "media_type": "article",
            "thumb_url": media_item.thumb_url or _source_favicon_url((source.url if source else None) or media_item.media_url),
            "video_url": None,
            "relevance": relevance,
            "relevance_score": round(float(relevance_score), 2),
        })

    items.sort(key=lambda row: (int(row.get("relevance") or 0), str(row.get("published") or "")), reverse=True)
    result = filter_operational_articles(items)[:12]
    _EVENT_NEWS_CACHE[cache_key] = (time.time(), result)
    return _with_last_updated(result, event.updated_at or event.started_at or now)


@app.get("/events/{event_id}/summary")
async def event_summary(event_id: str, db: Session = Depends(get_db)):
    """Sunto operativo dell'evento, aggregando dati, notizie, media e impatti locali."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento non trovato")

    news_items = await event_news(event_id, db)
    media_rows = (
        db.query(MediaItem, Source)
        .outerjoin(Source, MediaItem.source_id == Source.id)
        .filter(MediaItem.event_id == event_id)
        .filter(MediaItem.confidence >= 20)
        .order_by(MediaItem.captured_at.desc(), MediaItem.fetched_at.desc())
        .limit(80)
        .all()
    )

    media_items: list[dict] = []
    existing_urls: set[str] = set()
    for media_item, source in media_rows:
        row = media_item.to_dict()
        row["source"] = row.get("author") or (source.name if source else None) or (source.platform if source else None)
        row["source_name"] = row.get("source")
        row["platform"] = row.get("platform") or (source.platform if source else None)
        media_url = normalize_absolute_url(row.get("media_url")) or ""
        if media_url:
            existing_urls.add(media_url)
        media_items.append(row)

    subevents = [] if event.parent_event_id else _infer_subevents_from_news(event, news_items, videos=[])
    payload = _build_event_summary_payload(event, news_items, media_items, subevents)
    return _with_last_updated(payload, event.updated_at or event.started_at)


@app.get("/events/{event_id}/subevents")
async def event_subevents(event_id: str, db: Session = Depends(get_db)):
    """Deriva micro-eventi geolocalizzati (crolli, esondazioni, frane, evacuazioni) dalle notizie dell'evento principale."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento non trovato")
    if event.parent_event_id:
        return _with_last_updated([], event.updated_at or event.started_at)

    now = _utc_now_naive()
    cache_key = (event_id, (event.updated_at or event.started_at or now).isoformat())
    cached_entry = _SUBEVENT_CACHE.get(cache_key)
    if cached_entry and (time.time() - cached_entry[0]) < SUBEVENT_CACHE_TTL_SEC:
        return _with_last_updated(cached_entry[1], event.updated_at or event.started_at or now)

    news_items = await event_news(event_id, db)
    result = _infer_subevents_from_news(event, news_items, videos=[])

    persisted_ok = False
    try:
        _persist_subevents(db, event, result)
        persisted_ok = True
    except OperationalError as exc:
        db.rollback()
        logger.warning(f"Subevent persistence skipped for {event_id} due DB lock: {exc}")

    for row in result:
        row["persisted"] = persisted_ok or bool(db.query(Event.id).filter(Event.id == str(row.get("event_id") or row.get("id") or "")).first())
    _SUBEVENT_CACHE[cache_key] = (time.time(), result)
    return _with_last_updated(result, event.updated_at or event.started_at or now)


@app.get("/events/{event_id}/diagnostics")
async def event_diagnostics(event_id: str, db: Session = Depends(get_db)):
    """Diagnostica rapida per capire perche' tab Notizie/Media/Webcam risultano vuote."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento non trovato")

    rows = (
        db.query(MediaItem.media_type, Source.platform, func.count(MediaItem.id))
        .outerjoin(Source, MediaItem.source_id == Source.id)
        .filter(MediaItem.event_id == event_id)
        .group_by(MediaItem.media_type, Source.platform)
        .all()
    )

    by_type = {"article": 0, "image": 0, "video": 0, "webcam": 0, "other": 0}
    by_platform: dict[str, int] = {}

    for media_type, platform, count in rows:
        mt = (media_type or "other").strip().lower()
        if mt not in by_type:
            mt = "other"
        by_type[mt] += int(count or 0)

        pf = (platform or "unknown").strip().lower()
        by_platform[pf] = by_platform.get(pf, 0) + int(count or 0)

    news_items = await event_news(event_id, db)

    return _with_last_updated({
        "event": {
            "id": event.id,
            "title": event.title,
            "category": event.category,
        },
        "media": {
            "total": sum(by_type.values()),
            "by_type": by_type,
            "by_platform": by_platform,
        },
        "news": {
            "live_count": len(news_items),
        },
        "webcam_config": {
            "rapidapi_key_set": bool((os.getenv("RAPIDAPI_KEY") or "").strip()),
            "windy_key_set": bool((os.getenv("WINDY_API_KEY") or "").strip()),
        },
    }, event.updated_at or event.started_at)


@app.get("/events/{event_id}/completeness")
async def event_completeness(event_id: str, db: Session = Depends(get_db)):
    """Riepilogo completezza contenuti per evento (news live + media per tipo + stato chiavi webcam)."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento non trovato")

    type_rows = (
        db.query(MediaItem.media_type, func.count(MediaItem.id))
        .filter(MediaItem.event_id == event_id)
        .group_by(MediaItem.media_type)
        .all()
    )
    by_type = {"article": 0, "image": 0, "video": 0, "webcam": 0, "other": 0}
    for media_type, count in type_rows:
        mt = (media_type or "other").strip().lower()
        if mt not in by_type:
            mt = "other"
        by_type[mt] += int(count or 0)

    source_rows = (
        db.query(Source.platform, func.count(MediaItem.id))
        .join(MediaItem, MediaItem.source_id == Source.id)
        .filter(MediaItem.event_id == event_id)
        .group_by(Source.platform)
        .all()
    )
    by_platform = {str(platform or "unknown").lower(): int(count or 0) for platform, count in source_rows}

    live_news = await event_news(event_id, db)
    visual_total = by_type["image"] + by_type["video"] + by_type["webcam"]

    return _with_last_updated({
        "event_id": event_id,
        "title": event.title,
        "category": event.category,
        "media_total": int(sum(by_type.values())),
        "media_visual_total": int(visual_total),
        "media_by_type": by_type,
        "media_by_platform": by_platform,
        "news_live_count": int(len(live_news)),
        "webcam_config": {
            "rapidapi_key_set": bool((os.getenv("RAPIDAPI_KEY") or "").strip()),
            "windy_key_set": bool((os.getenv("WINDY_API_KEY") or "").strip()),
        },
        "has_visual_media": bool(visual_total > 0),
    }, event.updated_at or event.started_at)


@app.get("/events/{event_id}/climate-context")
async def get_event_climate_context(event_id: str, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento non trovato")

    resolved_place = None
    lat = event.lat
    lon = event.lon

    if lat is None or lon is None:
        resolved_place = _infer_event_anchor(event)
        if not resolved_place:
            return _with_last_updated({"error": "no_coordinates", "resolved_place": None}, event.updated_at or event.started_at)
        lat = resolved_place["lat"]
        lon = resolved_place["lon"]
    else:
        resolved_place = {
            "name": str(event.region or event.title or "Punto evento"),
            "lat": float(lat),
            "lon": float(lon),
            "source": "event_coordinates",
            "precision": "event",
        }

    event_date = event.started_at.isoformat() if event.started_at else (event.updated_at.isoformat() if event.updated_at else "")
    payload = await get_climate_context(
        float(lat),
        float(lon),
        event_date,
        event.category or event.type,
    )
    payload["resolved_place"] = resolved_place
    return _with_last_updated(payload, event.updated_at or event.started_at)


@app.get("/events/{event_id}/seismicity")
async def get_event_seismicity(
    event_id: str,
    radius_km: int = Query(150, ge=10, le=1000),
    limit: int = Query(10, ge=1, le=100),
    days: int = Query(120, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Evento non trovato")

    if event.lat is None or event.lon is None:
        raise HTTPException(status_code=422, detail={"status": "no_coordinates", "code": "missing_lat_lon"})  # fix: [6]

    event_date = event.started_at.isoformat() if event.started_at else (event.updated_at.isoformat() if event.updated_at else "")
    try:
        result = await get_ingv_seismicity(
            float(event.lat),
            float(event.lon),
            event_date,
            radius_km=radius_km,
            limit=limit,
            days=days,
        )
        return _with_last_updated(result, event.updated_at or event.started_at)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"upstream_error: {str(e)}")  # fix: [5]


@app.get("/media/recent")
def recent_media(
    limit: int = Query(20, ge=1, le=100),
    min_confidence: int = Query(50),
    db: Session = Depends(get_db),
):
    items = (
        db.query(MediaItem)
        .filter(MediaItem.confidence >= min_confidence)
        .order_by(MediaItem.fetched_at.desc())
        .limit(limit)
        .all()
    )
    return _with_last_updated([m.to_dict() for m in items])


@app.get("/health/collectors")
def health_collectors(db: Session = Depends(get_db)):
    records = (
        db.query(CollectorHealth)
        .order_by(CollectorHealth.last_run.desc())
        .all()
    )
    return [_collector_health_payload(r) for r in records]


@app.get("/collectors")
def list_collectors(db: Session = Depends(get_db)):
    from vigil.core.collector_registry import discover_collectors
    plugins = discover_collectors()
    health_by_name = {r.collector: r for r in db.query(CollectorHealth).all()}
    result = []
    for plugin in plugins:
        health = health_by_name.get(plugin.name)
        result.append({
            "name": plugin.name,
            "module_name": plugin.module_name,
            "interval_minutes": plugin.interval_minutes,
            "enabled": plugin.enabled,
            "last_run": health.last_run.isoformat() if health and health.last_run else None,
            "last_ok": health.last_ok.isoformat() if health and health.last_ok else None,
            "status": _collector_status(health.last_ok) if health else "unknown",
        })
    return result


@app.get("/collectors/status")
def collectors_status(db: Session = Depends(get_db)):
    totals = {
        "events": db.query(func.count(Event.id)).scalar() or 0,
        "media_items": db.query(func.count(MediaItem.id)).scalar() or 0,
        "sources": db.query(func.count(Source.id)).scalar() or 0,
    }
    by_platform = []
    rows = (
        db.query(
            Source.platform,
            func.count(Source.id).label("source_count"),
            func.coalesce(func.sum(Source.item_count), 0).label("item_count"),
        )
        .group_by(Source.platform)
        .order_by(func.count(Source.id).desc())
        .all()
    )
    for platform, source_count, item_count in rows:
        by_platform.append({
            "platform": platform,
            "source_count": int(source_count or 0),
            "item_count": int(item_count or 0),
        })
    by_event_type = [
        {"type": t, "count": int(c or 0)}
        for t, c in db.query(Event.type, func.count(Event.id)).group_by(Event.type).order_by(func.count(Event.id).desc()).all()
    ]
    by_severity = [
        {"severity": s, "count": int(c or 0)}
        for s, c in db.query(Event.severity, func.count(Event.id)).group_by(Event.severity).order_by(func.count(Event.id).desc()).all()
    ]
    recent_sources = [
        {
            "id": src.id,
            "name": src.name,
            "platform": src.platform,
            "last_fetched": src.last_fetched.isoformat() if src.last_fetched else None,
            "item_count": int(src.item_count or 0),
        }
        for src in db.query(Source).order_by(Source.last_fetched.desc()).limit(10).all()
    ]
    return {
        "totals": totals,
        "by_platform": by_platform,
        "by_event_type": by_event_type,
        "by_severity": by_severity,
        "recent_sources": recent_sources,
    }


@app.get("/debug/match")
def debug_match(
    text: str = Query(..., min_length=5),
    db: Session = Depends(get_db),
):
    from vigil.collectors.matcher import match_event as heuristic_match
    h_id, h_conf = heuristic_match(db, text, text)
    heuristic_result = {"event_id": h_id, "confidence": h_conf}
    semantic_result: dict = {"event_id": None, "confidence": 0}
    try:
        from vigil.core.embeddings import semantic_match_event
        s_id, s_conf = semantic_match_event(db, text)
        semantic_result = {"event_id": s_id, "confidence": s_conf}
    except ImportError:
        semantic_result["error"] = "sentence-transformers non installato"

    if semantic_result["confidence"] > heuristic_result["confidence"]:
        winner = {**semantic_result, "method": "semantic"}
    elif heuristic_result["confidence"] > 0:
        winner = {**heuristic_result, "method": "heuristic"}
    else:
        winner = {"event_id": None, "confidence": 0, "method": "none"}

    def _enrich(result: dict) -> dict:
        if result.get("event_id"):
            ev = db.query(Event).filter(Event.id == result["event_id"]).first()
            if ev:
                result["event_title"] = ev.title
        return result

    return {
        "text": text[:200],
        "heuristic": _enrich(heuristic_result),
        "semantic": _enrich(semantic_result),
        "winner": _enrich(winner),
    }


# ─── WebSocket: push eventi ogni 10s ─────────────────────────────────────────

@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    """
    Connessione WebSocket che invia la lista eventi aggiornata ogni 12 secondi.
    Usa wait_for(receive, timeout=12) per rilevare immediatamente il disconnect
    del client senza aspettare il prossimo send — elimina ECONNABORTED sul proxy.
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            db = SessionLocal()
            try:
                events = db.query(Event).order_by(Event.updated_at.desc()).all()
                platform_rows = (
                    db.query(
                        Source.event_id,
                        func.group_concat(func.distinct(Source.platform)).label("platforms"),
                    )
                    .filter(Source.event_id.isnot(None))
                    .group_by(Source.event_id)
                    .all()
                )
                platforms_by_event = {
                    eid: [p.strip() for p in (pl or "").split(",") if p]
                    for eid, pl in platform_rows
                }
                media_counts = dict(
                    db.query(MediaItem.event_id, func.count(MediaItem.id))
                    .group_by(MediaItem.event_id)
                    .all()
                )
                data = []
                for ev in events:
                    if not _is_in_europe(ev.lat, ev.lon):
                        continue
                    row = ev.to_dict()
                    platforms = platforms_by_event.get(ev.id, [])
                    row["media_count"] = int(media_counts.get(ev.id, 0))
                    row["platforms"] = platforms
                    row["primary_platform"] = platforms[0] if platforms else None
                    data.append(row)
            finally:
                db.close()

            await websocket.send_json({"type": "events", "data": data})

            # Aspetta 12s, ma interrompe immediatamente se il client si disconnette.
            # Questo evita ECONNABORTED sul proxy Vite: la disconnessione viene
            # rilevata durante receive invece che al prossimo send.
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=12.0)
            except asyncio.TimeoutError:
                pass  # timeout normale — continua il loop
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"WS chiuso: {e}")
    finally:
        try:
            ws_manager.disconnect(websocket)
        except ValueError:
            pass
