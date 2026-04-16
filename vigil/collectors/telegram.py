import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vigil.core.models import Event, MediaItem, Source
from vigil.collectors.matcher import match_event

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "Telegram Channels"
COLLECTOR_INTERVAL = 5
# Disabilitato automaticamente a runtime se mancano le env var API_ID/API_HASH
COLLECTOR_ENABLED = True

TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Canali pubblici curati — mix internazionale + italiano
# Formato: ("@handle", "nome leggibile")
CHANNELS = [
    ("@severeweathereу",     "Severe Weather EU"),
    ("@storm_radar_world",   "Storm Radar World"),
    ("@typhoon_tracker",     "Typhoon Tracker"),
    ("@cyclone_io",          "Cyclone Indian Ocean"),
    ("@meteosat_eu",         "Meteosat EU"),
    ("@volcanoestoday",      "Volcanoes Today"),
    ("@gdacsalerts",         "GDACS Alerts"),
    ("@meteoit_storms",      "Meteo IT Storms"),
    ("@storm_chasers_eu",    "Storm Chasers EU"),
    ("@copernicus_atmo",     "Copernicus Atmosphere"),
]

# Estensioni considerate media visivo
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm"}


def _content_hash(channel: str, message_id: int) -> str:
    return hashlib.md5(f"telegram-{channel}-{message_id}".encode()).hexdigest()


def _is_media_message(message) -> bool:
    """True se il messaggio contiene foto, video o documento visivo."""
    if hasattr(message, "photo") and message.photo:
        return True
    if hasattr(message, "video") and message.video:
        return True
    if hasattr(message, "document") and message.document:
        mime = getattr(message.document, "mime_type", "") or ""
        return mime.startswith("image/") or mime.startswith("video/")
    return False


def _extract_caption(message) -> str:
    """Estrae il testo del messaggio, fallback a stringa vuota."""
    return (message.text or message.message or "").strip()[:1000]


async def _download_thumb(client, message) -> Optional[str]:
    """
    Scarica la thumbnail del media e restituisce il path locale.
    In produzione andresti su object storage (S3/R2).
    Per MVP salviamo in ./media_cache/.
    """
    import os
    cache_dir = "./media_cache"
    os.makedirs(cache_dir, exist_ok=True)

    try:
        if hasattr(message, "photo") and message.photo:
            path = await client.download_media(
                message.photo,
                file=f"{cache_dir}/{message.id}_thumb.jpg",
                thumb=-1,  # thumbnail più piccola disponibile
            )
            return path
    except Exception as e:
        logger.debug(f"Thumb download fallito per msg {message.id}: {e}")
    return None


def _upsert_source(db: Session, channel_handle: str, channel_name: str) -> str:
    src_id = f"telegram-{channel_handle.lstrip('@')}"
    existing = db.query(Source).filter(Source.id == src_id).first()
    if existing is None:
        src = Source(
            id=src_id,
            name=channel_name,
            type="social",
            platform="telegram",
            url=f"https://t.me/{channel_handle.lstrip('@')}",
            event_id=None,
            last_fetched=datetime.now(timezone.utc).replace(tzinfo=None),
            item_count=0,
        )
        db.add(src)
        db.flush()
    else:
        existing.last_fetched = datetime.now(timezone.utc).replace(tzinfo=None)
    return src_id


async def _fetch_channel(client, db: Session, events: list[Event], handle: str, name: str,
                         limit: int) -> int:
    """Fetcha gli ultimi `limit` messaggi con media da un canale pubblico."""
    src_id = _upsert_source(db, handle, name)
    source = db.query(Source).filter(Source.id == src_id).first()
    count = 0

    try:
        async for message in client.iter_messages(handle, limit=limit):
            if not _is_media_message(message):
                continue

            content_hash = _content_hash(handle, message.id)
            existing = db.query(MediaItem).filter(
                MediaItem.content_hash == content_hash
            ).first()
            if existing:
                continue

            caption = _extract_caption(message)
            event_id, confidence = match_event(events, caption, caption)
            if event_id is None or confidence < 0.30:
                continue

            # Tenta download thumbnail (non bloccante)
            thumb_url = await _download_thumb(client, message)

            captured_at = message.date.replace(tzinfo=None) if message.date else None

            # Sender name
            author = handle
            if hasattr(message, "post_author") and message.post_author:
                author = message.post_author

            item = MediaItem(
                event_id=event_id,
                source_id=src_id,
                media_url=None,         # URL diretto non disponibile senza download
                thumb_url=thumb_url,
                media_type=("image" if ((hasattr(message, "photo") and message.photo) or (hasattr(message, "document") and getattr(getattr(message, "document", None), "mime_type", "").startswith("image/"))) else "article"),
                caption=caption,
                author=author,
                lat=None,
                lon=None,
                geo_raw=None,
                captured_at=captured_at,
                confidence=max(0, min(100, int(round(float(confidence or 0.0) * 100)))),
                content_hash=content_hash,
            )
            try:
                with db.begin_nested():
                    db.add(item)
                    db.flush()
            except IntegrityError:
                continue

            if source:
                source.item_count = (source.item_count or 0) + 1

            count += 1
            logger.debug(
                f"Telegram {handle}: nuovo item [{confidence}%] "
                f"{caption[:60] or '[solo media]'}"
            )

    except Exception as e:
        logger.warning(f"Telegram {handle} fetch fallito: {e}")

    return count


async def fetch_telegram_media_async(db: Session, limit_per_channel: int = 30) -> int:
    """
    Entry point async del collector Telegram.
    Usa telethon per leggere i canali pubblici senza autenticazione utente
    (solo API ID + Hash, in modalità anonima).
    """
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        logger.warning("Telegram collector: credenziali non configurate, skip")
        return 0

    if not TELEGRAM_API_ID.isdigit():
        logger.error("Telegram collector: TELEGRAM_API_ID non numerico")
        return 0

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        logger.error("telethon non installato — pip install telethon")
        return 0

    logger.info("Telegram collector: avvio fetch")
    events = db.query(Event).all()

    if not events:
        logger.info("Telegram collector: nessun evento disponibile, skip")
        return 0

    # StringSession vuota = sessione anonima (sola lettura canali pubblici)
    client = TelegramClient(StringSession(), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)

    total = 0
    try:
        await client.connect()
        if not await client.is_user_authorized():
            # Modalità anonima: funziona per canali pubblici
            logger.info("Telegram: connesso in modalità anonima")

        for handle, name in CHANNELS:
            n = await _fetch_channel(client, db, events, handle, name, limit_per_channel)
            total += n
            logger.info(f"Telegram {handle}: {n} item")

    except Exception as e:
        logger.error(f"Telegram collector errore: {e}")
    finally:
        await client.disconnect()

    logger.info(f"Telegram collector: {total} item totali")
    return total


def fetch_telegram_media(db: Session, limit_per_channel: int = 30) -> int:
    """
    Wrapper sincrono per lo scheduler APScheduler.
    Lancia il loop async in un thread separato.
    """
    import asyncio
    loop = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            fetch_telegram_media_async(db, limit_per_channel)
        )
    except Exception as e:
        logger.error(f"Telegram sync wrapper errore: {e}")
        return 0
    finally:
        if loop is not None:
            loop.close()
        asyncio.set_event_loop(None)
