import logging
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from vigil.core.database import SessionLocal
from vigil.core.geo import enrich_media_items
from vigil.core.health import update_health

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(
    timezone="UTC",
    job_defaults={
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 60,
    },
    executors={"default": {"type": "threadpool", "max_workers": 1}},
)


def _run_geo_enrichment():
    """Job speciale: post-processing geografico, non è un collector standard."""
    db = SessionLocal()
    try:
        n = enrich_media_items(db, use_geocoder=False)
        update_health(db, "geo_enrichment", n)
        db.commit()
        logger.info(f"[scheduler] Geo enrichment: {n} item arricchiti")
    except Exception as e:
        db.rollback()
        update_health(db, "geo_enrichment", 0, str(e))
        db.commit()
        logger.error(f"[scheduler] Geo enrichment errore: {e}")
    finally:
        db.close()


def _run_embedding_cleanup():
    """Pulizia periodica della cache embeddings: rimuove eventi non più nel DB."""
    db = SessionLocal()
    try:
        from vigil.core.embeddings import cleanup_embedding_cache
        cleanup_embedding_cache(db)
        db.commit()
    except ImportError:
        pass  # sentence-transformers non installato, niente da pulire
    except Exception as e:
        db.rollback()
        logger.error(f"[scheduler] Embedding cache cleanup errore: {e}")
    finally:
        db.close()


def _run_subevent_warmup():
    """Precalcola incidenti locali derivati dalle notizie per gli eventi recenti."""
    db = SessionLocal()
    try:
        from main import _warm_recent_subevents
        n = _warm_recent_subevents(db, limit=10)
        update_health(db, "subevent_enrichment", n)
        db.commit()
        logger.info(f"[scheduler] Subevent enrichment: {n} nuovi child event")
    except Exception as e:
        db.rollback()
        update_health(db, "subevent_enrichment", 0, str(e))
        db.commit()
        logger.error(f"[scheduler] Subevent enrichment errore: {e}")
    finally:
        db.close()


def start_scheduler():
    """
    Carica i collector tramite il registry (auto-discovery) e registra i job.
    Per aggiungere una nuova fonte è sufficiente creare un file .py in
    vigil/collectors/ — non è necessario toccare questo file.
    """
    from vigil.core.collector_registry import discover_collectors

    plugins = discover_collectors()

    def make_job(p):
        def _run():
            db = SessionLocal()
            try:
                n = p.fetch_fn(db)
                update_health(db, p.name, n)
                db.commit()
                logger.info(f"[{p.name}] {n} item")
            except Exception as e:
                db.rollback()
                try:
                    update_health(db, p.name, 0, str(e))
                    db.commit()
                except Exception as health_exc:
                    db.rollback()
                    logger.error(f"[{p.name}] update_health errore: {health_exc}")
                logger.error(f"[{p.name}] errore: {e}")
            finally:
                db.close()
        return _run

    for plugin in plugins:
        if not plugin.enabled:
            logger.info(f"Collector '{plugin.name}': disabilitato, skip")
            continue

        scheduler.add_job(
            make_job(plugin),
            trigger=IntervalTrigger(minutes=plugin.interval_minutes),
            id=plugin.name,
            replace_existing=True,
            max_instances=1,
        )
        logger.info(
            f"Registrato collector: '{plugin.name}' ogni {plugin.interval_minutes}min"
        )

    # Geo enrichment: job speciale non proveniente dal registry
    scheduler.add_job(
        _run_geo_enrichment,
        trigger=IntervalTrigger(minutes=8),
        id="geo_enrichment",
        replace_existing=True,
        max_instances=1,
    )

    # Cache embeddings: pulizia ogni 60 minuti
    scheduler.add_job(
        _run_embedding_cleanup,
        trigger=IntervalTrigger(minutes=60),
        id="embedding_cleanup",
        replace_existing=True,
        max_instances=1,
    )

    # Incidenti locali da news: refresh ogni 20 minuti
    scheduler.add_job(
        _run_subevent_warmup,
        trigger=IntervalTrigger(minutes=20),
        id="subevent_enrichment",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    logger.info(
        f"Scheduler avviato — {sum(1 for p in plugins if p.enabled)} collector attivi "
        f"+ geo_enrichment ogni 8min + subevent_enrichment ogni 20min"
    )

    # First run differito di 3s per non bloccare il boot di FastAPI
    def _boot_run():
        time.sleep(3)
        for plugin in plugins:
            if plugin.enabled:
                try:
                    make_job(plugin)()
                except Exception as exc:
                    logger.error(f"[scheduler] boot run collector '{plugin.name}' errore non gestito: {exc}")
                    continue
        time.sleep(2)
        try:
            _run_subevent_warmup()
        except Exception as exc:
            logger.error(f"[scheduler] boot run subevent enrichment errore non gestito: {exc}")

    threading.Thread(target=_boot_run, daemon=True).start()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler fermato")
