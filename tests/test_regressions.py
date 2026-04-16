import time
import unittest
from typing import Any, cast
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vigil.collectors import gdacs, telegram, matcher
from vigil.core import rss_utils, scheduler
from vigil.core.models import Base, Event, MediaItem


class RegressionTests(unittest.TestCase):
    def test_gdacs_region_oceania_branch_reachable(self):
        self.assertEqual(gdacs._region_from_latlon(-20.0, 120.0), "Oceania")
        self.assertEqual(gdacs._region_from_latlon(-20.0, 80.0), "Africa / Oceano Indiano")

    def test_match_event_accepts_preloaded_events(self):
        events = [
            Event(
                id="gdacs-tc-1",
                title="Typhoon Kong-Rey",
                type="cyclone",
                severity="red",
                status="CRITICO",
                region="Asia",
            )
        ]

        event_id, confidence = matcher.match_event(
            events,
            text="Massive typhoon landfall expected in Asia in next 24 hours",
            title="Typhoon warning update",
        )

        self.assertEqual(event_id, "gdacs-tc-1")
        self.assertGreaterEqual(confidence, 30)

    def test_telegram_wrapper_handles_invalid_api_id_without_crashing(self):
        with patch.object(telegram, "TELEGRAM_API_ID", "not-a-number"), patch.object(
            telegram, "TELEGRAM_API_HASH", "dummy-hash"
        ):
            result = telegram.fetch_telegram_media(db=cast(Any, None))
        self.assertEqual(result, 0)

    def test_scheduler_start_is_non_blocking_bootstrap(self):
        if scheduler.scheduler.running:
            scheduler.stop_scheduler()

        from unittest.mock import MagicMock
        import vigil.core.collector_registry as reg
        from vigil.core.collector_registry import CollectorPlugin

        mock_fetch = MagicMock(return_value=0)
        mock_plugin = CollectorPlugin(
            name="test_collector",
            module_name="vigil.collectors.test",
            fetch_fn=mock_fetch,
            interval_minutes=10,
            enabled=True,
        )

        original_cache = reg._cached_plugins
        reg._cached_plugins = [mock_plugin]
        try:
            scheduler.start_scheduler()

            # Startup must NOT call collectors synchronously — it starts a daemon thread.
            self.assertFalse(mock_fetch.called)

            job_ids = {job.id for job in scheduler.scheduler.get_jobs()}
            self.assertIn("test_collector", job_ids)
            # geo_enrichment is always registered independently of the registry
            self.assertIn("geo_enrichment", job_ids)
        finally:
            reg._cached_plugins = original_cache
            scheduler.stop_scheduler()

    def test_extract_og_media_reads_page_once_for_image_and_video(self):
        html = (
            '<html><head>'
            '<meta property="og:image" content="/img.jpg">'
            '<meta property="og:video" content="/embed/video">'
            '</head></html>'
        )

        class FakeResponse:
            def __init__(self):
                self.text = html
                self.url = "https://example.com/news/story"

        with patch.object(rss_utils, "httpx") as mock_httpx:
            mock_httpx.get.return_value = FakeResponse()
            image_url, video_url = rss_utils.extract_og_media("https://example.com/news/story")

        self.assertEqual(image_url, "https://example.com/img.jpg")
        self.assertEqual(video_url, "https://example.com/embed/video")
        mock_httpx.get.assert_called_once()

    def test_infer_subevents_from_news_maps_bridge_and_flood(self):
        from main import _infer_subevents_from_news

        event = Event(
            id="meteoalarm-molise",
            title="Allerta arancione per maltempo in Molise",
            type="meteoalarm",
            severity="orange",
            status="ATTENZIONE",
            region="Molise",
            lat=41.70,
            lon=14.80,
        )
        news = [
            {
                "title": "Maltempo, crolla il ponte sul Trigno: la S.S.16 Adriatica interrotta",
                "source": "molisenetwork.net",
                "url": "https://example.com/ponte-trigno",
            },
            {
                "title": "Allerta maltempo per la diga del Liscione. Allagato basso Molise e nucleo industriale di Termoli",
                "source": "RaiNews",
                "url": "https://example.com/liscione-termoli",
            },
        ]

        subevents = _infer_subevents_from_news(event, news)

        self.assertGreaterEqual(len(subevents), 2)
        self.assertTrue(any(s["subcategory"] == "Ponte / infrastruttura" and s["lat"] is not None and s["lon"] is not None for s in subevents))
        self.assertTrue(any(s["subcategory"] == "Esondazione / allagamento" and (s.get("place_name") or "").lower() in {"trigno", "termoli", "basso molise", "diga del liscione"} for s in subevents))

    def test_persist_subevents_creates_child_events_and_media(self):
        from main import _persist_subevents

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)

        with Session() as db:
            parent = Event(
                id="meteoalarm-parent",
                title="Allerta arancione per maltempo in Molise",
                type="meteoalarm",
                severity="orange",
                status="ATTENZIONE",
                region="Molise",
                lat=41.70,
                lon=14.80,
            )
            db.add(parent)
            db.flush()

            subevents = [{
                "id": "subevent::meteoalarm-parent::bridge::0",
                "title": "Maltempo, crolla il ponte sul Trigno: la S.S.16 Adriatica interrotta",
                "subcategory": "Ponte / infrastruttura",
                "type": "bridge",
                "severity": "orange",
                "status": "ATTENZIONE",
                "lat": 41.976,
                "lon": 14.766,
                "place_name": "Trigno",
                "region": "Molise",
                "source": "molisenetwork.net",
                "news_url": "https://example.com/ponte-trigno",
                "thumb_url": "https://example.com/bridge.jpg",
                "video_url": "https://www.youtube.com/watch?v=abc123xyz00",
                "news": [{
                    "title": "Maltempo, crolla il ponte sul Trigno: la S.S.16 Adriatica interrotta",
                    "url": "https://example.com/ponte-trigno",
                    "source": "molisenetwork.net",
                    "thumb_url": "https://example.com/bridge.jpg",
                }],
                "videos": [{
                    "title": "Crolla il ponte sul Trigno",
                    "url": "https://www.youtube.com/watch?v=abc123xyz00",
                    "thumb_url": "https://i.ytimg.com/vi/abc123xyz00/hqdefault.jpg",
                    "source": "YouTube",
                    "platform": "youtube_public",
                }],
            }]

            created = _persist_subevents(db, parent, subevents)
            db.commit()

            self.assertEqual(created, 1)
            child = db.query(Event).filter(Event.parent_event_id == parent.id).one()
            self.assertEqual(child.subcategory, "Ponte / infrastruttura")
            self.assertEqual(child.derived_from, "news_inference")
            self.assertEqual(db.query(MediaItem).filter(MediaItem.event_id == child.id).count(), 2)

    def test_build_event_summary_payload_combines_news_media_and_impacts(self):
        from main import _build_event_summary_payload

        event = Event(
            id="meteoalarm-summary",
            title="Allerta arancione per vento e pioggia in Emilia-Romagna",
            type="meteoalarm",
            severity="orange",
            status="ATTENZIONE",
            region="Emilia-Romagna",
            lat=44.5,
            lon=11.3,
            wind_kmh=62,
            precipitation_mm=18.4,
        )
        news_items = [
            {"title": "Fiumi sorvegliati speciali e scuole chiuse in alcune aree", "source": "RaiNews", "published": "2026-04-03T07:20:00"},
            {"title": "Allagamenti locali e disagi alla viabilità tra Forlì e Ravenna", "source": "Il Resto del Carlino", "published": "2026-04-03T06:45:00"},
        ]
        media_items = [
            {"media_type": "image", "platform": "wikimedia"},
            {"media_type": "video", "platform": "youtube_public"},
            {"media_type": "article", "platform": "rss"},
        ]
        subevents = [
            {"subcategory": "Esondazione / allagamento", "title": "Allagamenti locali a Forlì", "place_name": "Forlì"},
            {"subcategory": "Viabilità / chiusure", "title": "Strade interrotte nel ravennate", "place_name": "Ravenna"},
        ]

        payload = _build_event_summary_payload(event, news_items, media_items, subevents)

        self.assertIn("Emilia-Romagna", payload["headline"])
        self.assertGreaterEqual(len(payload["key_points"]), 3)
        self.assertIn("Esondazione / allagamento", " ".join(payload["major_impacts"]))
        self.assertEqual(payload["coverage"]["articles"], 2)
        self.assertEqual(payload["coverage"]["visual_media"], 2)


if __name__ == "__main__":
    unittest.main()
