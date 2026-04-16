import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from vigil.core.categories import EventCategory
from vigil.core.models import Event, Source

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "DPC Protezione Civile"
COLLECTOR_INTERVAL = 60
COLLECTOR_ENABLED = True

DPC_PRIMARY_URL = "https://raw.githubusercontent.com/pcm-dpc/DPC-Bollettini-Vigilanza-Meteorologica/master/files/json/today.json"
SOURCE_NAME = "dpc-protezione-civile"
SOURCE_PLATFORM = "dpc"

ZONE_COORDS = {
    "emilia-romagna": (44.6, 11.0),
    "veneto": (45.6, 11.8),
    "toscana": (43.6, 11.1),
    "lombardia": (45.4, 9.9),
    "piemonte": (45.1, 7.9),
    "lazio": (41.9, 12.7),
    "campania": (40.9, 14.8),
    "sicilia": (37.6, 14.1),
    "sardegna": (40.1, 9.0),
    "italia": (42.5, 12.5),
}


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _severity_from_level(level: str) -> tuple[str, str]:
    l = (level or "").strip().upper()
    if l == "ELEVATA":
        return "red", "CRITICO"
    if l == "MODERATA":
        return "orange", "ATTENZIONE"
    if l in {"ORDINARIA", "ASSENTE"}:
        return "blue", "MODERATO"
    return "blue", "MODERATO"


def _content_hash(source_id: str, date_key: str, zone: str) -> str:
    return hashlib.md5(f"{source_id}|{date_key}|{zone}".encode("utf-8", errors="ignore")).hexdigest()


def _infer_zone(text: str) -> str:
    low = (text or "").lower()
    for zone in ZONE_COORDS:
        if zone in low:
            return zone
    return "italia"


def _extract_value(d: dict, keys: list[str], default: str = "") -> str:
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return default


def _infer_category_from_text(text: str) -> Optional[str]:
    low = (text or "").lower()
    if any(k in low for k in ["piogg", "alluv", "idraul", "esond"]):
        return EventCategory.flood.value
    if any(k in low for k in ["tempor", "vento", "raffic", "storm", "meteo"]):
        return EventCategory.storm.value
    return None


def _iter_dicts(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_dicts(item)


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
            url=DPC_PRIMARY_URL,
            event_id=event_id,
            last_fetched=_utc_now_naive(),
            item_count=1,
        )
        db.add(src)
    else:
        src.last_fetched = _utc_now_naive()  # type: ignore[assignment]
        src.item_count = int(src.item_count or 0) + 1  # type: ignore[assignment]


def _build_candidate_urls() -> list[str]:
    base = "https://raw.githubusercontent.com/pcm-dpc/DPC-Bollettini-Vigilanza-Meteorologica"
    candidates = [DPC_PRIMARY_URL]
    today = datetime.now(timezone.utc).date()
    for branch in ("master", "main"):
        for delta in range(0, 14):
            day = (today - timedelta(days=delta)).strftime("%Y%m%d")
            candidates.append(f"{base}/{branch}/files/{day}.json")
    return candidates


def _fetch_first_available_payload() -> tuple[Optional[dict], Optional[str]]:
    for url in _build_candidate_urls():
        try:
            response = httpx.get(url, timeout=20)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            return response.json(), url
        except Exception:
            continue
    # Fallback: resolve latest available daily file from GitHub contents index.
    index_url = "https://api.github.com/repos/pcm-dpc/DPC-Bollettini-Vigilanza-Meteorologica/contents/files?ref=master"
    try:
        response = httpx.get(index_url, timeout=20)
        response.raise_for_status()
        entries = response.json()
        dated = []
        for e in entries if isinstance(entries, list) else []:
            name = str(e.get("name", ""))
            if re.fullmatch(r"\d{8}\.json", name):
                dated.append((name, e.get("download_url")))
        dated.sort(key=lambda x: x[0], reverse=True)
        for _, download_url in dated[:10]:
            if not download_url:
                continue
            try:
                res = httpx.get(str(download_url), timeout=20)
                res.raise_for_status()
                return res.json(), str(download_url)
            except Exception:
                continue
    except Exception:
        pass

    return None, None


def _extract_bulletin_events(payload: dict, source_url: str) -> list[dict]:
    events = []
    date_key = str(payload.get("date") or _utc_now_naive().date().strftime("%Y%m%d"))
    day_sections = [
        ("oggi", payload.get("today")),
        ("domani", payload.get("tomorrow")),
        ("dopodomani", payload.get("aftertomorrow")),
    ]
    for label, section in day_sections:
        if not isinstance(section, dict):
            continue
        description = _extract_value(section, ["html_description", "description", "desc"]).strip()
        if not description:
            continue
        title = _extract_value(section, ["name", "title"], default=f"Bollettino DPC {label}")
        text = f"{title} {description}".lower()
        if "ross" in text:
            sev, status = "red", "CRITICO"
            level = "ELEVATA"
        elif "aranc" in text:
            sev, status = "orange", "ATTENZIONE"
            level = "MODERATA"
        else:
            sev, status = "blue", "MODERATO"
            level = "ORDINARIA"

        region = "Italia"
        content_hash = _content_hash(SOURCE_NAME, f"{date_key}-{label}", region)
        events.append(
            {
                "id": f"dpc-{content_hash[:16]}",
                "title": f"{title} ({label})",
                "type": "dpc_vigilanza",
                "category": _infer_category_from_text(text),
                "is_alert": True,
                "severity": sev,
                "status": status,
                "lat": ZONE_COORDS["italia"][0],
                "lon": ZONE_COORDS["italia"][1],
                "region": region,
                "wind_kmh": None,
                "pressure_hpa": None,
                "started_at": None,
                "source_url": source_url,
                "level": level,
            }
        )
    return events


def fetch_dpc_vigilanza(db: Session) -> int:
    logger.info("DPC collector: avvio fetch")
    payload, source_url = _fetch_first_available_payload()
    if payload is None:
        logger.warning("DPC fetch/parsing fallito: nessun URL disponibile")
        return 0
    logger.info(f"DPC collector: feed attivo {source_url}")

    processed = 0
    seen_ids = set()

    # Preferred path: current DPC schema provides day sections instead of zone rows.
    for bulletin in _extract_bulletin_events(payload, source_url or DPC_PRIMARY_URL):
        event_id = bulletin["id"]
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        data = {
            "id": event_id,
            "title": bulletin["title"],
            "type": bulletin["type"],
            "category": bulletin.get("category"),
            "is_alert": bool(bulletin.get("is_alert", True)),
            "severity": bulletin["severity"],
            "status": bulletin["status"],
            "lat": bulletin["lat"],
            "lon": bulletin["lon"],
            "region": bulletin["region"],
            "wind_kmh": None,
            "pressure_hpa": None,
            "started_at": None,
        }
        _upsert_event(db, data)
        _upsert_source(db, event_id)
        src_id = f"{SOURCE_NAME}-{event_id}"
        src = db.query(Source).filter(Source.id == src_id).first()
        if src is not None:
            src.url = str(bulletin.get("source_url") or source_url or DPC_PRIMARY_URL)  # type: ignore[assignment]
        processed += 1

    for entry in _iter_dicts(payload):
        try:
            zone = _extract_value(entry, ["zona", "zona_allerta", "nome_zona", "area", "zone"])
            level = _extract_value(entry, ["livello", "criticita", "livello_rischio", "rischio", "allerta"])
            if not zone and not level:
                continue

            fenomeno = _extract_value(
                entry,
                ["fenomeno", "fenomeni", "descrizione", "descrizione_fenomeni", "tipo"],
                default="fenomeni meteo",
            )
            day = _extract_value(entry, ["data", "giorno", "date", "validita", "inizio"])

            sev, status = _severity_from_level(level)
            zone_norm = _infer_zone(zone or fenomeno)
            lat, lon = ZONE_COORDS.get(zone_norm, ZONE_COORDS["italia"])
            region = zone if zone else zone_norm.title()

            date_key = day or _utc_now_naive().date().isoformat()
            content_hash = _content_hash(SOURCE_NAME, date_key, region)
            event_id = f"dpc-{content_hash[:16]}"
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)

            data = {
                "id": event_id,
                "title": f"DPC {region}: {fenomeno} ({level or 'ORDINARIA'})",
                "type": "dpc_vigilanza",
                "category": _infer_category_from_text(f"{fenomeno} {level} {region}"),
                "is_alert": True,
                "severity": sev,
                "status": status,
                "lat": lat,
                "lon": lon,
                "region": region,
                "wind_kmh": None,
                "pressure_hpa": None,
                "started_at": None,
            }
            _upsert_event(db, data)
            _upsert_source(db, event_id)
            if source_url:
                src_id = f"{SOURCE_NAME}-{event_id}"
                src = db.query(Source).filter(Source.id == src_id).first()
                if src is not None:
                    src.url = source_url  # type: ignore[assignment]
            processed += 1
        except Exception as exc:
            logger.warning(f"DPC entry skip per errore: {exc}")

    logger.info(f"DPC collector: {processed} bollettini processati")
    return processed
