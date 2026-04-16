from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import re
from typing import Optional, Union

from sqlalchemy.orm import Session

from vigil.core.models import Event, MediaItem

MIN_CONFIDENCE = 0.30

ITALY_REGION_NEIGHBORS = {
    "abruzzo": ["marche", "lazio", "molise"],
    "basilicata": ["puglia", "campania", "calabria"],
    "calabria": ["basilicata"],
    "campania": ["lazio", "molise", "puglia", "basilicata"],
    "emilia-romagna": ["lombardia", "veneto", "toscana", "marche", "liguria", "piemonte"],
    "friuli-venezia giulia": ["veneto"],
    "lazio": ["toscana", "umbria", "marche", "abruzzo", "molise", "campania"],
    "liguria": ["piemonte", "emilia-romagna", "toscana"],
    "lombardia": ["piemonte", "veneto", "trentino-alto adige", "emilia-romagna"],
    "marche": ["emilia-romagna", "toscana", "umbria", "lazio", "abruzzo"],
    "molise": ["abruzzo", "lazio", "campania", "puglia"],
    "piemonte": ["liguria", "lombardia", "valle d'aosta"],
    "puglia": ["molise", "campania", "basilicata"],
    "sardegna": [],
    "sicilia": [],
    "toscana": ["liguria", "emilia-romagna", "marche", "umbria", "lazio"],
    "trentino-alto adige": ["lombardia", "veneto"],
    "umbria": ["toscana", "marche", "lazio"],
    "valle d'aosta": ["piemonte"],
    "veneto": ["lombardia", "trentino-alto adige", "emilia-romagna", "friuli-venezia giulia"],
}

ITALIAN_REGIONS = set(ITALY_REGION_NEIGHBORS.keys())

CATEGORY_KEYWORDS = {
    "snow": ["neve", "nevicata", "bufera", "gelo", "ghiaccio", "blizzard"],
    "storm": ["temporale", "tempesta", "vento", "tromba", "grandine"],
    "flood": ["alluvione", "esondazione", "piena", "allagamento", "inondazione"],
    "earthquake": ["terremoto", "sisma", "scossa", "sismico", "magnitudo"],
    "wildfire": ["incendio", "rogo", "fiamme", "bruciato", "wildfire"],
    "extreme_heat": ["caldo", "ondata di calore", "afa", "temperature record"],
}

GENERIC_WEATHER_DISASTER = [
    "allerta", "maltempo", "meteo", "disastro", "emergenza", "temporale",
    "neve", "alluvione", "terremoto", "incendio", "frana", "vento",
]

TITLE_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "alert", "warning", "issued",
    "allerta", "meteo", "evento", "event", "news", "report", "live", "video", "image",
    "della", "delle", "dello", "degli", "dei", "del", "di", "a", "in", "su", "da",
    "italia", "italy",
}

PLATFORM_QUALITY_BONUS = {
    "dpc": 12,
    "meteoalarm": 12,
    "usgs": 10,
    "youtube_public": 10,
    "openverse": 8,
    "wikimedia": 8,
    "google_news": 6,
    "rss": 5,
    "reddit": 4,
    "peertube": 4,
    "webcam": 7,
}


def match_event(events_or_db: Union[Session, Sequence[Event]], text: str, title: str) -> tuple[Optional[str], float]:
    """Primary/secondary matcher returning confidence in [0,1]."""
    combined = _norm(f"{title or ''} {text or ''}")
    title_norm = _norm(title or "")
    if isinstance(events_or_db, Session):
        events = events_or_db.query(Event).all()
    else:
        events = list(events_or_db)

    if not events:
        return None, 0.0

    candidates: list[tuple[str, float]] = []
    for event in events:
        conf = _event_confidence(event, title_norm, combined)
        if conf >= MIN_CONFIDENCE:
            candidates.append((event.id, conf))

    candidates.sort(key=lambda c: c[1], reverse=True)
    if len(candidates) >= 3:
        return candidates[0][0], _clamp01(candidates[0][1])

    if not candidates:
        return None, 0.0
    return candidates[0][0], _clamp01(candidates[0][1])


def normalize_absolute_url(url: Optional[str]) -> Optional[str]:
    raw = (url or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    if not raw.startswith("http"):
        return None
    if raw.startswith("http://"):
        raw = f"https://{raw[7:]}"
    return raw


def clean_media_title(title: str, source_name: Optional[str], platform: Optional[str]) -> str:
    raw_title = (title or "").strip()
    source = (source_name or "").strip()
    if not raw_title or not source:
        return raw_title

    pattern = re.compile(rf"\s+-\s+{re.escape(source)}\s*$", re.IGNORECASE)
    has_trailing_source = bool(pattern.search(raw_title))
    source_earlier = source.lower() in pattern.sub("", raw_title).lower()
    platform_present = bool((platform or "").strip())
    if has_trailing_source and (source_earlier or platform_present):
        raw_title = pattern.sub("", raw_title).strip()
    return raw_title


def title_signature(text: str) -> str:
    words = [
        w for w in re.findall(r"[a-z0-9]+", _norm(text or ""))
        if len(w) >= 3 and w not in TITLE_STOPWORDS
    ]
    if not words:
        return ""
    return " ".join(words[:14])


def is_semantic_duplicate(
    db: Session,
    event_id: str,
    title: str,
    media_type: str,
    lookback_days: int = 21,
    threshold: float = 0.82,
) -> bool:
    sig_new = title_signature(title)
    if not sig_new:
        return False

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=lookback_days)
    rows = (
        db.query(MediaItem.caption)
        .filter(MediaItem.event_id == str(event_id), MediaItem.media_type == media_type)
        .filter(MediaItem.fetched_at >= cutoff)
        .order_by(MediaItem.fetched_at.desc())
        .limit(120)
        .all()
    )
    if not rows:
        return False

    new_tokens = set(sig_new.split())
    for (caption,) in rows:
        old_sig = title_signature(str(caption or ""))
        if not old_sig:
            continue
        if old_sig == sig_new:
            return True
        old_tokens = set(old_sig.split())
        union = len(new_tokens | old_tokens)
        if union == 0:
            continue
        jaccard = len(new_tokens & old_tokens) / union
        if jaccard >= threshold:
            return True
    return False


def quality_score(
    confidence: int | float,
    media_type: str,
    platform: str | None,
    captured_at: datetime | None,
    fetched_at: datetime | None,
    has_thumb: bool,
) -> int:
    score = max(0.0, min(100.0, float(confidence or 0.0)))

    mt = (media_type or "").lower().strip()
    if mt == "webcam":
        score += 10
    elif mt == "video":
        score += 8
    elif mt == "image":
        score += 5
    elif mt == "article":
        score -= 10

    pf = (platform or "").lower().strip()
    score += float(PLATFORM_QUALITY_BONUS.get(pf, 0))
    if pf in {"openverse", "wikimedia"}:
        score -= 4

    if has_thumb:
        score += 3

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    reference_dt = captured_at or fetched_at
    if reference_dt is not None:
        age_hours = max(0.0, (now - reference_dt).total_seconds() / 3600.0)
        if age_hours <= 24:
            score += 8
        elif age_hours <= 72:
            score += 4
        elif age_hours > 30 * 24:
            score -= 6

    return int(max(0.0, min(100.0, round(score))))


def _clamp01(value: Optional[float]) -> float:
    try:
        n = float(value if value is not None else 0.0)
    except (TypeError, ValueError):
        return 0.0
    if n < 0.0:
        return 0.0
    if n > 1.0:
        return 1.0
    return n


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _event_confidence(event: Event, title_norm: str, combined_norm: str) -> float:
    event_type = _norm((event.category or event.type or ""))
    category_words = CATEGORY_KEYWORDS.get(event_type, _type_keywords(event_type))
    geo_words = _geo_terms(event)
    country = _country_name(event)

    geo_match = any(g in combined_norm for g in geo_words)
    topical_match = any(k in title_norm for k in category_words)

    # Primary: geographic + topical in title.
    if geo_match and topical_match:
        score = 0.60
        strong_geo_hits = sum(1 for g in geo_words if g in combined_norm)
        topic_hits = sum(1 for k in category_words if k in title_norm)
        score += min(0.25, strong_geo_hits * 0.06)
        score += min(0.15, topic_hits * 0.05)
        return _clamp01(score)

    # Secondary A: same country + category keyword in title.
    if country and country in combined_norm and topical_match:
        score = 0.35 + min(0.20, sum(1 for k in category_words if k in title_norm) * 0.05)
        return min(score, 0.59)

    # Secondary B: same region + any weather/disaster keyword.
    if geo_match and any(k in combined_norm for k in GENERIC_WEATHER_DISASTER):
        score = 0.30 + min(0.25, sum(1 for k in GENERIC_WEATHER_DISASTER if k in combined_norm) * 0.03)
        return min(score, 0.59)

    return 0.0


def _country_name(event: Event) -> Optional[str]:
    region = (event.region or "").strip().lower()
    if not region:
        return None
    if region in ITALIAN_REGIONS:
        return "italy"
    if "italia" in region or "italy" in region:
        return "italy"
    # Fallback: if comma-delimited location, use last token as country hint.
    if "," in region:
        candidate = region.split(",")[-1].strip()
        return candidate or None
    return None


def _geo_terms(event: Event) -> list[str]:
    region = (event.region or "").strip().lower()
    terms: list[str] = []
    if region:
        terms.append(region)
        parts = [p.strip() for p in re.split(r"[,;/]", region) if p.strip()]
        terms.extend(parts)
    country = _country_name(event)
    if country:
        terms.append(country)
    neighbors = ITALY_REGION_NEIGHBORS.get(region, [])
    terms.extend(neighbors)
    return [t for t in dict.fromkeys(terms) if t]


def _type_keywords(event_type: str) -> list[str]:
    type_keywords = {
        "cyclone": ["cyclone", "typhoon", "hurricane", "tropical storm", "tifone", "uragano"],
        "flood": ["flood", "alluvion", "alluvione", "flash flood", "inundation", "esondazione", "piena"],
        "volcano": ["volcano", "eruption", "lava", "ash cloud", "eruzione", "cenere"],
        "earthquake": ["earthquake", "quake", "tremor", "seism", "terremoto", "scossa"],
        "storm": ["storm", "supercell", "tornado", "twister", "hail", "temporale", "grandine"],
        "wildfire": ["wildfire", "fire", "blaze", "incendio", "forest fire"],
        "meteoalarm": ["allerta", "warning", "meteo", "criticita", "weather alert", "maltempo"],
        "dpc_vigilanza": ["vigilanza", "criticita", "protezione civile", "allerta", "maltempo"],
        "weather_alert": ["allerta", "warning", "meteo", "criticita", "maltempo"],
        "snow": ["snow", "neve", "nevicata", "blizzard", "ghiaccio", "gelo"],
        "extreme_cold": ["extreme cold", "cold wave", "gelo", "ghiaccio", "neve", "nevicata", "blizzard"],
    }
    return type_keywords.get(event_type, [event_type] if event_type else [])
