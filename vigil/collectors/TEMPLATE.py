"""
Template per un nuovo collector Vigil.

Copia questo file, rinominalo (es. bluesky.py) e implementa fetch_X.
Il collector verrà caricato automaticamente allo startup — non è
necessario modificare scheduler.py o nessun altro file.

Requisiti minimi:
  - Il file si trova in vigil/collectors/
  - Il nome del file NON inizia con underscore
  - Esiste una funzione con nome che inizia per 'fetch_' che accetta
    esattamente un parametro (db: Session) e ritorna int
"""

from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurazione (opzionale — usa i default se omessa)
# ---------------------------------------------------------------------------
COLLECTOR_NAME = "Il Mio Collector"   # nome leggibile nel registry e nei log
COLLECTOR_INTERVAL = 15               # minuti tra un run e l'altro
COLLECTOR_ENABLED = True              # False per disabilitare senza eliminare il file


def fetch_mio_collector(db: Session) -> int:
    """
    Fetcha dati dalla fonte e salva MediaItem/Event nel DB.

    Args:
        db: sessione SQLAlchemy aperta dallo scheduler.
            NON chiamare db.commit() qui: lo fa lo scheduler dopo il return.
            NON chiamare db.close(): lo fa lo scheduler nel finally.

    Returns:
        Numero di nuovi item salvati in questo run (usato per le metriche).

    Note:
        - Usa content_hash (UUID o hash dell'URL) per la deduplicazione:
            from vigil.core.models import MediaItem
            item = MediaItem(content_hash=hash, ...)
            db.add(item)   # IntegrityError silenzioso se già presente

        - Usa Event da vigil.core.models per creare/aggiornare eventi.
        - Usa Source per tracciare la provenienza degli item.
        - logger.warning() per errori non fatali (item singoli saltati).
        - Solleva Exception per errori fatali (tutta la fonte non disponibile):
            lo scheduler la intercetta, logga l'errore e aggiorna la health.
            #
            # Per trovare l'evento più simile a un testo:
            #   from vigil.core.matcher import match_event
            #   event_id, confidence = match_event(db, testo, titolo)
            #   # Usa use_embeddings=False per disabilitare il layer semantico nei test
    """
    logger.info("[mio_collector] avvio fetch")
    count = 0

    # Il tuo codice qui
    # Esempio minimo:
    #
    # import httpx
    # from vigil.core.models import Event, MediaItem, Source
    # import hashlib
    #
    # resp = httpx.get("https://esempio.it/feed.json", timeout=15)
    # resp.raise_for_status()
    # for item in resp.json().get("items", []):
    #     content_hash = hashlib.sha256(item["url"].encode()).hexdigest()
    #     media = MediaItem(
    #         event_id="...",     # collega a un Event esistente
    #         caption=item["title"],
    #         media_url=item["url"],
    #         content_hash=content_hash,
    #         confidence=60,
    #     )
    #     try:
    #         db.add(media)
    #         db.flush()
    #         count += 1
    #     except Exception:
    #         db.rollback()       # solo il nested, non tutta la sessione

    logger.info(f"[mio_collector] {count} item salvati")
    return count
