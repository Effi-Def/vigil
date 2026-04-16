import hashlib
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from vigil.core.categories import EventCategory
from vigil.core.models import Event, Source

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "Meteoalarm EU"
COLLECTOR_INTERVAL = 30
COLLECTOR_ENABLED = True

METEOALARM_FEED_URL = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-italy"
SOURCE_NAME = "meteoalarm-it"
SOURCE_PLATFORM = "meteoalarm"

REGION_COORDS = {
    "abruzzo": (42.2, 13.9),
    "basilicata": (40.6, 16.3),
    "calabria": (38.9, 16.6),
    "campania": (40.9, 14.8),
    "emilia-romagna": (44.5, 11.3),
    "friuli-venezia giulia": (46.1, 13.0),
    "lazio": (41.9, 12.7),
    "liguria": (44.3, 8.9),
    "lombardia": (45.5, 9.9),
    "marche": (43.4, 13.1),
    "molise": (41.6, 14.7),
    "piemonte": (45.1, 7.7),
    "puglia": (41.1, 16.8),
    "sardegna": (40.1, 9.0),
    "sicilia": (37.5, 14.0),
    "toscana": (43.8, 11.2),
    "trentino-alto adige": (46.5, 11.4),
    "umbria": (42.9, 12.6),
    "valle d'aosta": (45.7, 7.3),
    "veneto": (45.6, 11.7),
}


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _severity_from_text(text: str) -> tuple[str, str]:
    low = text.lower()
    if "red" in low or "ross" in low:
        return "red", "CRITICO"
    if "orange" in low or "aranc" in low:
        return "orange", "ATTENZIONE"
    if "yellow" in low or "giall" in low:
        return "blue", "MODERATO"
    return "blue", "MODERATO"


def _extract_region(text: str) -> str:
    low = text.lower()
    for region in REGION_COORDS:
        if region in low:
            return region.title()
    return "Italia"


def _category_from_text(text: str) -> Optional[str]:
    low = (text or "").lower()
    if any(k in low for k in ["flood", "alluv", "piogge intense", "esondazione"]):
        return EventCategory.flood.value
    if any(k in low for k in ["thunderstorm", "temporale", "storm"]):
        return EventCategory.storm.value
    if any(k in low for k in ["wind", "vento", "raffiche"]):
        return EventCategory.wind.value
    if any(k in low for k in ["snow", "neve", "blizzard", "ghiaccio"]):
        return EventCategory.snow.value
    if any(k in low for k in ["heat", "caldo", "temperature elevate"]):
        return EventCategory.extreme_heat.value
    if any(k in low for k in ["cold", "freddo", "gel", "frost"]):
        return EventCategory.extreme_cold.value
    if any(k in low for k in ["landslide", "frana"]):
        return EventCategory.landslide.value
    return None


def _extract_validity_window(text: str, fallback_pub: Optional[str]) -> tuple[Optional[datetime], Optional[datetime]]:
    low = (text or "").lower()
    pattern = r"(\d{1,2}/\d{1,2}/\d{4})(?:\s+(\d{1,2}:\d{2}))?"
    matches = re.findall(pattern, low)
    parsed = []
    for day, hm in matches[:2]:
        ts = f"{day} {hm or '00:00'}"
        try:
            parsed.append(datetime.strptime(ts, "%d/%m/%Y %H:%M"))
        except Exception:
            continue

    if parsed:
        start = parsed[0]
        end = parsed[1] if len(parsed) > 1 else None
        return start, end

    if fallback_pub:
        try:
            pub = parsedate_to_datetime(fallback_pub).replace(tzinfo=None)
            return pub, None
        except Exception:
            return None, None
    return None, None


def _content_hash(source_id: str, date_key: str, zone: str, fingerprint: str = "") -> str:
    return hashlib.md5(f"{source_id}|{date_key}|{zone}|{fingerprint}".encode("utf-8", errors="ignore")).hexdigest()


def _upsert_event(db: Session, data: dict) -> None:
    event = db.query(Event).filter(Event.id == data["id"]).first()
    if event is None:
        db.add(Event(**data))
        return

    for k, v in data.items():
        if k != "id" and v is not None:
            setattr(event, k, v)
    event.updated_at = _utc_now_naive()  # type: ignore[assignment]


def _upsert_source(db: Session, event_id: str) -> None:
    src_id = f"{SOURCE_NAME}-{event_id}"
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        src = Source(
            id=src_id,
            name=SOURCE_NAME,
            type="ufficiale",
            platform=SOURCE_PLATFORM,
            url=METEOALARM_FEED_URL,
            event_id=event_id,
            last_fetched=_utc_now_naive(),
            item_count=1,
        )
        db.add(src)
    else:
        src.last_fetched = _utc_now_naive()  # type: ignore[assignment]
        src.item_count = int(src.item_count or 0) + 1  # type: ignore[assignment]


def _extract_tag(block: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    value = match.group(1)
    value = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", value, flags=re.DOTALL)
    return html.unescape(value).strip()


def _iter_items_from_text(feed_text: str) -> list[dict[str, str]]:
    blocks = re.findall(r"<item\b[^>]*>(.*?)</item>", feed_text, flags=re.IGNORECASE | re.DOTALL)
    items = []
    for block in blocks:
        title = _extract_tag(block, "title")
        description = _extract_tag(block, "description")
        pub_date = _extract_tag(block, "pubDate")
        if title or description or pub_date:
            items.append({"title": title, "description": description, "pubDate": pub_date})
    return items


def _iter_items_from_atom(root: ET.Element) -> list[dict[str, str]]:
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "cap": "urn:oasis:names:tc:emergency:cap:1.2",
    }
    items: list[dict[str, str]] = []
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
        if not summary:
            summary = (entry.findtext("atom:content", default="", namespaces=ns) or "").strip()
        pub = (
            entry.findtext("atom:updated", default="", namespaces=ns)
            or entry.findtext("atom:published", default="", namespaces=ns)
            or ""
        ).strip()
        if title or summary or pub:
            items.append({"title": title, "description": html.unescape(summary), "pubDate": pub})
    return items


def fetch_meteoalarm(db: Session) -> int:
    logger.info("Meteoalarm collector: avvio fetch")
    try:
        response = httpx.get(METEOALARM_FEED_URL, timeout=20)
        response.raise_for_status()
    except Exception as exc:
        logger.warning(f"Meteoalarm fetch fallito: {exc}")
        return 0

    text = response.text or ""
    root = None
    try:
        root = ET.fromstring(text)
    except Exception:
        # Some feed entries occasionally include bare '&' which breaks XML parsing.
        sanitized = re.sub(r"&(?!#?\w+;)", "&amp;", text)
        try:
            root = ET.fromstring(sanitized)
        except Exception as exc:
            logger.warning(f"Meteoalarm parse XML fallito: {exc}; uso parser fallback")

    parsed_items: list[dict[str, str]] = []
    if root is not None:
        tag = root.tag.lower()
        if tag.endswith("feed"):
            parsed_items = _iter_items_from_atom(root)
        else:
            channel = root.find("channel")
            if channel is None:
                logger.warning("Meteoalarm feed non in formato RSS atteso (channel mancante), provo fallback")
                parsed_items = _iter_items_from_text(text)
            else:
                for item in channel.findall("item"):
                    parsed_items.append(
                        {
                            "title": (item.findtext("title") or "").strip(),
                            "description": html.unescape((item.findtext("description") or "").strip()),
                            "pubDate": (item.findtext("pubDate") or "").strip(),
                        }
                    )

    if not parsed_items:
        parsed_items = _iter_items_from_text(text)
    if not parsed_items:
        logger.warning("Meteoalarm: nessun item trovato nel feed")
        return 0

    processed = 0
    seen_event_ids: set[str] = set()
    for item in parsed_items:
        try:
            title = (item.get("title") or "Allerta Meteoalarm Italia").strip()
            desc = (item.get("description") or "").strip()
            pub_date = (item.get("pubDate") or "").strip()
            text_blob = f"{title} {desc}"

            severity, status = _severity_from_text(text_blob)
            category = _category_from_text(text_blob)
            region = _extract_region(text_blob)
            lat, lon = REGION_COORDS.get(region.lower(), (42.5, 12.5))
            start_at, end_at = _extract_validity_window(text_blob, pub_date)

            date_key = (start_at.isoformat() if start_at else pub_date) or _utc_now_naive().isoformat()
            content_hash = _content_hash(SOURCE_NAME, date_key, region, title)
            event_id = f"meteoalarm-{content_hash[:16]}"
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)

            data = {
                "id": event_id,
                "title": title if not end_at else f"{title} (fino {end_at.strftime('%d/%m %H:%M')})",
                "type": "meteoalarm",
                "category": category,
                "is_alert": True,
                "severity": severity,
                "status": status,
                "lat": lat,
                "lon": lon,
                "region": region,
                "wind_kmh": None,
                "pressure_hpa": None,
                "started_at": start_at,
            }
            _upsert_event(db, data)
            _upsert_source(db, event_id)
            processed += 1
        except Exception as exc:
            logger.warning(f"Meteoalarm item skip per errore: {exc}")

    logger.info(f"Meteoalarm collector: {processed} allerta/e processate")
    return processed
