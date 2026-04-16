import html
import logging
import re
import xml.etree.ElementTree as ET
import unicodedata
import hashlib
from functools import lru_cache
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode

import httpx
from sqlalchemy.orm import Session

from vigil.collectors.matcher import match_event
from vigil.core.models import Event

logger = logging.getLogger(__name__)

ITALIAN_REGION_ALIASES = {
    "abruzzo": ["abruzzo", "pescara", "l'aquila", "teramo", "chieti"],
    "basilicata": ["basilicata", "potenza", "matera"],
    "calabria": ["calabria", "catanzaro", "cosenza", "reggio calabria", "crotone"],
    "campania": ["campania", "napoli", "salerno", "caserta", "benevento", "avellino"],
    "emilia-romagna": ["emilia-romagna", "emilia romagna", "bologna", "modena", "parma", "reggio emilia", "rimini", "ravenna", "forli", "cesena", "ferrara", "piacenza"],
    "friuli-venezia giulia": ["friuli-venezia giulia", "friuli venezia giulia", "trieste", "udine", "pordenone", "gorizia"],
    "lazio": ["lazio", "roma", "viterbo", "frosinone", "latina", "rieti"],
    "liguria": ["liguria", "genova", "imperia", "savona", "la spezia"],
    "lombardia": ["lombardia", "milano", "brescia", "bergamo", "como", "varese", "cremona", "mantova", "sondrio", "lecco", "monza"],
    "marche": ["marche", "ancona", "pesaro", "urbino", "macerata", "fermo", "ascoli"],
    "molise": ["molise", "campobasso", "isernia"],
    "piemonte": ["piemonte", "torino", "novara", "asti", "cuneo", "biella", "vercelli", "alessandria"],
    "puglia": ["puglia", "bari", "lecce", "foggia", "taranto", "brindisi", "barletta"],
    "sardegna": ["sardegna", "cagliari", "sassari", "nuoro", "oristano", "olbia"],
    "sicilia": ["sicilia", "palermo", "catania", "messina", "siracusa", "trapani", "agrigento", "ragusa", "enna", "caltanissetta"],
    "toscana": ["toscana", "firenze", "pisa", "siena", "livorno", "arezzo", "grosseto", "lucca", "prato", "massa"],
    "trentino-alto adige": ["trentino-alto adige", "trentino alto adige", "trento", "bolzano", "alto adige", "sudtirol"],
    "umbria": ["umbria", "perugia", "terni", "foligno", "spoleto"],
    "valle d'aosta": ["valle d'aosta", "valle d aosta", "valle d\u2019aosta", "aosta"],
    "veneto": ["veneto", "venezia", "verona", "padova", "vicenza", "treviso", "belluno", "rovigo"],
    "italia": ["italia", "italy"],
}

# Italian + English stopwords to exclude from token scoring
IT_STOPWORDS = frozenset({
    # articles / prepositions
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
    "di", "del", "dello", "della", "dei", "degli", "delle",
    "da", "dal", "dallo", "dalla", "dai", "dagli", "dalle",
    "in", "nel", "nello", "nella", "nei", "negli", "nelle",
    "su", "sul", "sullo", "sulla", "sui", "sugli", "sulle",
    "a", "al", "allo", "alla", "ai", "agli", "alle",
    "con", "col", "per", "tra", "fra", "e", "o", "ma",
    # frequent event-report words that carry no discriminating value
    "allerta", "evento", "meteo", "vigilanza", "bollettino",
    "oggi", "domani", "dopodomani", "ore", "del", "della",
    "italia", "italiano", "italiana",
    # English stopwords
    "the", "and", "for", "with", "from", "this", "that",
    "alert", "warning", "issued", "event",
})

TYPE_SIGNAL_KEYWORDS = {
    "cyclone": ["cyclone", "hurricane", "typhoon", "tifone", "uragano", "ciclone", "tempesta tropicale"],
    "hurricane": ["cyclone", "hurricane", "typhoon", "tifone", "uragano", "ciclone", "tempesta tropicale"],
    "flood": ["flood", "alluvione", "allagamento", "esondazione", "piena", "nubifragio", "maltempo"],
    "storm": ["storm", "temporale", "grandine", "tromba d'aria", "vento forte", "maltempo", "bomba d'acqua", "supercella"],
    "wildfire": ["wildfire", "incendio", "rogo", "fiamme", "forestale", "boschivo"],
    "earthquake": ["earthquake", "terremoto", "scossa", "sisma", "magnitudo"],
    "volcano": ["volcano", "vulcano", "eruzione", "lava", "cenere"],
    "drought": ["drought", "siccita", "emergenza idrica", "crisi idrica", "razionamento acqua"],
    "meteoalarm": ["allerta", "allerta meteo", "criticita", "warning", "maltempo", "piogge intense"],
    "dpc_vigilanza": ["protezione civile", "allerta", "vigilanza", "criticita", "maltempo"],
}


def normalize_text(text: str) -> str:
    raw = unicodedata.normalize("NFKD", text or "")
    raw = raw.encode("ascii", "ignore").decode("ascii")
    raw = raw.lower().replace("'", " ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def event_region_aliases(event: Event) -> list[str]:
    region_key = normalize_text(str(event.region or "")).replace("  ", " ")
    if not region_key:
        return []
    for key, aliases in ITALIAN_REGION_ALIASES.items():
        if normalize_text(key) == region_key:
            return [normalize_text(alias) for alias in aliases]
    return [region_key]


def score_event_match(
    event: Event,
    text: str,
    title: str = "",
    article_published: Optional[datetime] = None,
) -> int:
    combined = normalize_text(f"{title or ''} {text or ''}")
    if not combined:
        return 0

    score = 0
    event_type = normalize_text(str(event.type or ""))
    event_title = normalize_text(str(event.title or ""))

    # Region alias boost
    for alias in event_region_aliases(event):
        if alias and alias in combined:
            score += 20 if alias == normalize_text(str(event.region or "")) else 12

    # Significant title token boost — skip stopwords
    significant_words = [
        w for w in event_title.split()
        if len(w) >= 4 and w not in IT_STOPWORDS
    ]
    for word in significant_words[:4]:
        if word in combined:
            score += 14

    # Event-type keyword boost
    for kw in TYPE_SIGNAL_KEYWORDS.get(event_type, []):
        kw_norm = normalize_text(kw)
        if kw_norm and kw_norm in combined:
            score += 18

    # Negative signals: revocation / closure language
    negative_terms = ["terminato", "cessato", "revocata", "revocato", "fine allerta", "allerta revocata"]
    for term in negative_terms:
        if normalize_text(term) in combined:
            score -= 15
            break

    # Time-proximity boost: article published close to event start
    if article_published is not None and event.started_at is not None:
        try:
            event_start = event.started_at
            if event_start.tzinfo is None:
                event_start = event_start.replace(tzinfo=timezone.utc)
            art_dt = article_published
            if art_dt.tzinfo is None:
                art_dt = art_dt.replace(tzinfo=timezone.utc)
            delta = abs((art_dt - event_start).total_seconds())
            if delta <= 86400:       # within 24h
                score += 15
            elif delta <= 259200:    # within 72h
                score += 8
        except Exception:
            pass

    return max(0, min(score, 100))


def _local_name(tag: str) -> str:
    if not tag:
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[1].lower()
    return tag.lower()


def _find_child_text(elem: ET.Element, names: set[str]) -> str:
    for child in list(elem):
        if _local_name(child.tag) in names:
            return (child.text or "").strip()
    return ""


def _find_enclosure_url(elem: ET.Element) -> Optional[str]:
    for child in list(elem):
        name = _local_name(child.tag)
        if name == "enclosure":
            url = (child.attrib.get("url") or "").strip()
            if url:
                return url
        if name == "content":
            url = (child.attrib.get("url") or "").strip()
            if url:
                return url
    return None


def parse_rss_feed(url: str) -> list[dict]:
    """Fetch and parse RSS/Atom feed into normalized items. Never raises."""
    try:
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        xml_text = response.text or ""
    except Exception as exc:
        logger.warning(f"parse_rss_feed fetch error for {url}: {exc}")
        return []

    try:
        root = ET.fromstring(xml_text)
    except Exception as exc:
        logger.warning(f"parse_rss_feed parse error for {url}: {exc}")
        return []

    items: list[dict] = []
    root_name = _local_name(root.tag)

    if root_name == "rss":
        channel = None
        for child in list(root):
            if _local_name(child.tag) == "channel":
                channel = child
                break
        if channel is None:
            return []

        for item in list(channel):
            if _local_name(item.tag) != "item":
                continue
            title = _find_child_text(item, {"title"})
            link = _find_child_text(item, {"link"})
            description = _find_child_text(item, {"description", "summary"})
            published = _find_child_text(item, {"pubdate", "published", "updated"})
            source_name = _find_child_text(item, {"source"})
            enclosure_url = _find_enclosure_url(item)
            items.append(
                {
                    "title": title,
                    "link": link,
                    "description": description,
                    "published": published,
                    "enclosure_url": enclosure_url,
                    "source": source_name,
                }
            )

    elif root_name == "feed":
        for entry in list(root):
            if _local_name(entry.tag) != "entry":
                continue
            title = _find_child_text(entry, {"title"})
            description = _find_child_text(entry, {"summary", "content", "description"})
            published = _find_child_text(entry, {"published", "updated", "pubdate"})
            source_name = _find_child_text(entry, {"source"})

            link = ""
            enclosure_url = None
            for child in list(entry):
                if _local_name(child.tag) != "link":
                    continue
                href = (child.attrib.get("href") or "").strip()
                rel = (child.attrib.get("rel") or "").strip().lower()
                if not link and href and rel in {"", "alternate", "related"}:
                    link = href
                if rel == "enclosure" and href:
                    enclosure_url = href

            items.append(
                {
                    "title": title,
                    "link": link,
                    "description": description,
                    "published": published,
                    "enclosure_url": enclosure_url,
                    "source": source_name,
                }
            )

    return items


OG_IMAGE_PATTERNS = [
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    r'<meta[^>]+property=["\']og:image:secure_url["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image:secure_url["\']',
    r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
    r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']image_src["\']',
    r'"thumbnailUrl"\s*:\s*\[\s*"([^"]+)"',
    r'"thumbnailUrl"\s*:\s*"([^"]+)"',
]

OG_VIDEO_PATTERNS = [
    r'<meta[^>]+property=["\']og:video(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video(?::secure_url)?["\']',
    r'<meta[^>]+name=["\']twitter:player["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:player["\']',
    r'<iframe[^>]+src=["\']([^"\']*(?:youtube\.com/embed|player\.vimeo\.com/video|facebook\.com/plugins/video|dailymotion\.com/embed|streamable\.com/e)[^"\']*)["\']',
    r'"embedUrl"\s*:\s*"([^"]+)"',
    r'"contentUrl"\s*:\s*"([^"]+\.(?:mp4|m3u8)[^"]*)"',
    r'<video[^>]+src=["\']([^"\']+)["\']',
    r'<source[^>]+src=["\']([^"\']+)["\'][^>]+type=["\']video/',
]


def _match_preview_url(chunk: str, page_url: str, patterns: list[str]) -> Optional[str]:
    for pat in patterns:
        m = re.search(pat, chunk, flags=re.IGNORECASE)
        if m:
            val = html.unescape((m.group(1) or '').strip().replace('\\/', '/').replace('\\u0026', '&'))
            if val and not val.startswith('data:'):
                return urljoin(page_url, val)
    return None


@lru_cache(maxsize=512)
def extract_og_media(url: str) -> tuple[Optional[str], Optional[str]]:
    """Fetch a page once and extract both image and video preview URLs.

    Caching avoids repeated network lookups for the same article across refreshes.
    """
    headers = {
        "Range": "bytes=0-98303",
        "User-Agent": "Mozilla/5.0 (compatible; vigil-rss/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://news.google.com/",
    }
    try:
        response = httpx.get(url, headers=headers, timeout=4, follow_redirects=True)
        chunk = response.text or ""
        page_url = str(response.url)
    except Exception:
        return None, None

    if not chunk:
        return None, None

    return (
        _match_preview_url(chunk, page_url, OG_IMAGE_PATTERNS),
        _match_preview_url(chunk, page_url, OG_VIDEO_PATTERNS),
    )


def extract_og_image(url: str) -> Optional[str]:
    """Extract image preview from page meta tags."""
    return extract_og_media(url)[0]


def extract_og_video(url: str) -> Optional[str]:
    """Extract video/embed preview URL from page meta tags or inline video tags."""
    return extract_og_media(url)[1]


def extract_image_from_description(description: str) -> Optional[str]:
    """Find an image URL inside an RSS description field (often contains HTML <img> tags)."""
    if not description:
        return None
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', description, flags=re.IGNORECASE)
    if m:
        val = m.group(1).strip()
        if val and val.startswith("http"):
            return val
    return None


def parse_published_datetime(raw_value: str) -> Optional[datetime]:
    if not raw_value:
        return None
    value = raw_value.strip()
    if not value:
        return None

    try:
        dt = parsedate_to_datetime(value)
        return dt.replace(tzinfo=None)
    except Exception:
        pass

    iso_guess = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_guess)
        return dt.replace(tzinfo=None)
    except Exception:
        return None


def domain_name(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "unknown"


# UTM and tracking params to strip before hashing
_STRIP_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "source", "_ga", "mc_cid", "mc_eid",
})


def canonical_url_hash(url: str) -> str:
    """Return a stable MD5 of the canonical form of a URL (strips UTM params, www., trailing slash).
    Use this as content_hash across all collectors to deduplicate the same article regardless of source."""
    try:
        parsed = urlparse(url.strip())
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query, keep_blank_values=False)
        clean_qs = {k: v for k, v in qs.items() if k.lower() not in _STRIP_PARAMS}
        canonical = urlunparse((
            parsed.scheme.lower(),
            host,
            path,
            "",
            urlencode(sorted(clean_qs.items()), doseq=True),
            "",
        ))
    except Exception:
        canonical = url.strip()
    return hashlib.md5(canonical.encode("utf-8", errors="ignore")).hexdigest()


def keyword_match_event(
    db: Session,
    text: str,
    article_published: Optional[datetime] = None,
) -> tuple[Optional[str], int]:
    """Match text to event with Italian-region boost and optional time-proximity scoring."""
    combined = (text or "").strip()
    event_id, confidence = match_event(db, combined, combined)
    try:
        events = db.query(Event).all()
        best_event_id = event_id
        best_score = confidence

        for event in events:
            fallback_score = score_event_match(event, combined, combined, article_published)
            if event_id == event.id:
                fallback_score = max(fallback_score, confidence)
            if fallback_score > best_score:
                best_score = fallback_score
                best_event_id = event.id

        if best_event_id is None or best_score < 30:
            return None, 0
        return str(best_event_id), min(best_score, 100)
    except Exception:
        if event_id is None:
            return None, 0
        return str(event_id), confidence
