import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Pattern coordinate esplicite nel testo
_COORD_PATTERNS = [
    re.compile(
        r"(\d{1,3}\.\d+)\s*[°]?\s*([NS])[,\s]+(\d{1,3}\.\d+)\s*[°]?\s*([EW])",
        re.IGNORECASE,
    ),
    re.compile(
        r"lat[:\s]+(-?\d{1,3}\.\d+)[,\s]+lon[:\s]+(-?\d{1,3}\.\d+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(-?\d{1,3}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})"
    ),
]

# Lookup veloce per nomi di luogo frequenti nei post meteo
# Evita una chiamata geocoder per i casi più comuni
_QUICK_LOOKUP: dict[str, tuple[float, float]] = {
    "valencia": (39.47, -0.38),
    "miami": (25.77, -80.19),
    "tokyo": (35.68, 139.69),
    "manila": (14.60, 120.98),
    "luzon": (16.0, 121.0),
    "osaka": (34.69, 135.50),
    "guangzhou": (23.13, 113.26),
    "dhaka": (23.81, 90.41),
    "kolkata": (22.57, 88.36),
    "myanmar": (19.74, 96.08),
    "mozambique": (-18.67, 35.53),
    "madagascar": (-20.28, 44.68),
    "florida": (27.99, -81.76),
    "oklahoma": (35.49, -97.50),
    "texas": (31.97, -99.90),
    "louisiana": (31.07, -91.96),
    "carolina": (35.63, -79.81),
    "bangladesh": (23.68, 90.35),
    "philippines": (12.88, 121.77),
    "iceland": (64.96, -19.02),
    "hawaii": (20.80, -156.33),
    "indonesia": (-2.55, 118.01),
    "japan": (36.20, 138.25),
    "italy": (41.87, 12.57),
    "sicily": (37.60, 14.02),
    "sardinia": (40.12, 9.01),
    "lombardia": (45.47, 9.19),
    "veneto": (45.44, 11.99),
    "emilia": (44.49, 11.34),
    "emilia-romagna": (44.49, 11.34),
    "toscana": (43.77, 11.25),
    "friuli": (46.07, 13.24),
    "friuli venezia giulia": (45.65, 13.77),
    "piemonte": (45.07, 7.69),
    "liguria": (44.41, 8.93),
    "lazio": (41.90, 12.49),
    "campania": (40.85, 14.27),
    "puglia": (41.12, 16.87),
    "sicilia": (38.12, 13.36),
    "sardegna": (39.22, 9.12),
    "marche": (43.62, 13.52),
    "umbria": (43.11, 12.39),
    "abruzzo": (42.35, 13.40),
    "molise": (41.56, 14.66),
    "basilicata": (40.64, 15.80),
    "calabria": (38.90, 16.59),
}


def _try_regex(text: str) -> Optional[tuple[float, float]]:
    """Tenta estrazione coordinate da pattern regex nel testo."""
    for pattern in _COORD_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        groups = m.groups()
        try:
            if len(groups) == 4:
                # Pattern con N/S E/W
                lat = float(groups[0]) * (-1 if groups[1].upper() == "S" else 1)
                lon = float(groups[2]) * (-1 if groups[3].upper() == "W" else 1)
            else:
                lat, lon = float(groups[0]), float(groups[1])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
        except (ValueError, IndexError):
            continue
    return None


def _try_quick_lookup(text: str) -> Optional[tuple[float, float]]:
    """Cerca nomi di luogo noti nel testo — zero latenza, zero API."""
    text_lower = text.lower()
    for place, coords in _QUICK_LOOKUP.items():
        if place in text_lower:
            return coords
    return None


def _try_geocoder(text: str) -> Optional[tuple[float, float]]:
    """
    Fallback: estrae il primo toponimo dal testo e lo geocodifica
    con Nominatim (OpenStreetMap, gratuito, no API key).
    Rate limit: 1 req/sec — usare con parsimonia.
    """
    try:
        from geopy.geocoders import Nominatim
        from geopy.exc import GeocoderTimedOut, GeocoderUnavailable

        # Estrai candidati: parole con iniziale maiuscola (probabili nomi propri)
        candidates = re.findall(r"\b[A-Z][a-z]{3,}\b", text)
        if not candidates:
            return None

        geolocator = Nominatim(user_agent="vigil-geotagger/0.1")

        for candidate in candidates[:3]:  # max 3 tentativi per item
            try:
                location = geolocator.geocode(candidate, timeout=5)
                if location:
                    lat, lon = location.latitude, location.longitude
                    logger.debug(f"Geocoded '{candidate}' → ({lat:.2f}, {lon:.2f})")
                    return lat, lon
            except (GeocoderTimedOut, GeocoderUnavailable):
                continue

    except ImportError:
        logger.warning("geopy non installato — geocoding disabilitato")

    return None


def extract_coordinates(
    text: str,
    use_geocoder: bool = False,
) -> tuple[Optional[float], Optional[float], Optional[str], int]:
    """
    Estrae coordinate da testo libero con tre strategie in cascata:
      1. Regex — coordinate esplicite nel testo
      2. Quick lookup — nomi di luogo noti (dizionario locale)
      3. Geocoder Nominatim — fallback API (solo se use_geocoder=True)

    Returns:
        (lat, lon, geo_raw, confidence_boost)
        confidence_boost: quanto aggiungere alla confidence dell'item
    """
    if not text or not text.strip():
        return None, None, None, 0

    # 1. Regex — massima precisione
    result = _try_regex(text)
    if result:
        lat, lon = result
        return lat, lon, text[:200], 25

    # 2. Quick lookup — veloce, zero latenza
    result = _try_quick_lookup(text)
    if result:
        lat, lon = result
        matched = next(p for p in _QUICK_LOOKUP if p in text.lower())
        return lat, lon, matched, 15

    # 3. Geocoder — solo se esplicitamente abilitato (rate limit)
    if use_geocoder:
        result = _try_geocoder(text)
        if result:
            lat, lon = result
            return lat, lon, text[:100], 10

    return None, None, None, 0


def enrich_media_items(db, use_geocoder: bool = False) -> int:
    """
    Post-processing: arricchisce i MediaItem senza coordinate
    estraendo geo dal campo caption.
    Da chiamare dopo ogni ciclo di fetch.
    """
    from vigil.core.models import MediaItem

    items = (
        db.query(MediaItem)
        .filter(MediaItem.lat.is_(None))
        .filter(MediaItem.caption.isnot(None))
        .limit(100)
        .all()
    )

    enriched = 0
    for item in items:
        lat, lon, geo_raw, boost = extract_coordinates(
            item.caption, use_geocoder=use_geocoder
        )
        if lat is not None:
            item.lat = lat
            item.lon = lon
            item.geo_raw = geo_raw
            item.confidence = min((item.confidence or 0) + boost, 100)
            enriched += 1

    logger.info(f"Geo enrichment: {enriched}/{len(items)} item arricchiti")
    return enriched
