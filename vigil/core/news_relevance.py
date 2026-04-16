from __future__ import annotations

import re
from urllib.parse import urlparse

from vigil.core.rss_utils import normalize_text

MIN_RELEVANCE_SCORE = 0.4

OPERATIONAL_KEYWORDS = [
    'allerta', 'emergenza', 'meteo', 'vento', 'neve', 'ghiaccio', 'pioggia', 'temporale',
    'terremoto', 'sisma', 'alluvione', 'frana', 'incendio', 'protezione civile',
    'vigili del fuoco', 'evacuazione', 'esondazione', 'mareggiat', 'tromba d aria',
    'grandine', 'uragano', 'ciclone', 'rischio idrogeologico', 'dpc', 'ingv', 'arpa',
    'meteoalarm', 'livello idrometrico', 'codice rosso', 'codice arancione', 'codice giallo',
]

EMERGENCY_KEYWORDS = [
    'allerta', 'emergenza', 'protezione civile', 'vigili del fuoco', 'evacuazione',
    'esondazione', 'alluvione', 'frana', 'incendio', 'terremoto', 'sisma',
    'rischio idrogeologico', 'livello idrometrico', 'codice rosso', 'codice arancione', 'codice giallo',
]

BLACKLIST_TITLE_KEYWORDS = [
    'calcio', 'sport', 'politica', 'elezioni', 'economia', 'borsa', 'celebrity',
    'spettacolo', 'cinema', 'musica', 'astronaut', 'spazio', 'luna', 'marte',
    'covid', 'vaccin', 'lavoro', 'sciopero',
]

INSTITUTIONAL_PATTERNS = [
    'protezionecivile', 'governo.it', 'ingv', 'arpa', 'meteoalarm',
]

METEO_PATTERNS = [
    'meteoam.it', 'ilmeteo.it', 'meteoweb.eu', 'ansa.it', 'rainews.it', 'meteoalarm',
]


def _strip_html(text: str) -> str:
    return re.sub(r'<[^>]+>', ' ', text or '')


def _first_paragraph(text: str) -> str:
    plain = re.sub(r'\s+', ' ', _strip_html(text)).strip()
    if not plain:
        return ''
    chunks = re.split(r'(?:\n\s*\n|\.\s)', plain, maxsplit=1)
    return (chunks[0] or plain)[:320]


def _contains_any(text: str, patterns: list[str]) -> bool:
    hay = normalize_text(text or '')
    return any(normalize_text(pattern).replace('*', '') in hay for pattern in patterns)


def _classify_source(url: str = '', source_name: str = '') -> str:
    parsed = urlparse(url or '')
    domain = (parsed.netloc or '').lower().replace('www.', '')
    path = normalize_text(parsed.path or '')
    source_norm = normalize_text(source_name or '')
    combined = f'{domain} {source_norm}'

    if any(pattern in combined for pattern in INSTITUTIONAL_PATTERNS):
        return 'institutional'

    if 'meteoweb.eu' in domain:
        return 'meteo' if 'meteo' in path else 'generic'
    if 'ansa.it' in domain:
        return 'meteo' if 'meteo' in path else 'generic'
    if any(pattern in combined for pattern in METEO_PATTERNS):
        return 'meteo'
    return 'generic'


def score_article_relevance(title: str, description: str = '', url: str = '', source_name: str = '') -> float:
    title_norm = normalize_text(title or '')
    if _contains_any(title_norm, BLACKLIST_TITLE_KEYWORDS):
        return 0.0

    body = _first_paragraph(description or '')
    text = f'{title}\n{body}\n{source_name}\n{url}'
    has_operational = _contains_any(text, OPERATIONAL_KEYWORDS)
    has_emergency = _contains_any(text, EMERGENCY_KEYWORDS)
    source_class = _classify_source(url, source_name)

    if source_class == 'institutional' and (has_emergency or has_operational):
        return 1.0
    if source_class == 'meteo' and has_operational:
        return 0.7
    if source_class == 'generic' and has_operational:
        return 0.4
    return 0.0


def filter_operational_articles(items: list[dict]) -> list[dict]:
    filtered: list[dict] = []
    for item in items or []:
        existing = item.get('relevance_score')
        if existing is None:
            score = score_article_relevance(
                str(item.get('title') or ''),
                str(item.get('description') or item.get('caption') or ''),
                str(item.get('url') or item.get('media_url') or item.get('source_url') or ''),
                str(item.get('source') or item.get('source_name') or item.get('author') or ''),
            )
        else:
            try:
                score = float(existing)
            except Exception:
                score = 0.0
        if score < MIN_RELEVANCE_SCORE:
            continue
        enriched = dict(item)
        enriched['relevance_score'] = round(score, 2)
        filtered.append(enriched)
    return filtered
