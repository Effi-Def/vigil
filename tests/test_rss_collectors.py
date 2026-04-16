import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vigil.collectors import news_google, rss_local
from vigil.core import rss_utils
from vigil.core.models import Base, Event, MediaItem, Source


class _Resp:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RSSCollectorsTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _add_event(self, event_id: str, title: str, event_type: str, region: str, severity: str = "orange"):
        db = self.SessionLocal()
        try:
            db.add(
                Event(
                    id=event_id,
                    title=title,
                    type=event_type,
                    severity=severity,
                    status="ATTENZIONE",
                    region=region,
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
            db.commit()
        finally:
            db.close()

    def test_parse_rss_feed_supports_rss_and_atom(self):
        rss_xml = """
        <rss><channel><item>
          <title>Alluvione a Milano</title>
          <link>https://example.com/a</link>
          <description>Piogge intense</description>
          <pubDate>Fri, 01 Mar 2024 10:00:00 GMT</pubDate>
          <enclosure url=\"https://example.com/img.jpg\" />
        </item></channel></rss>
        """.strip()
        atom_xml = """
        <feed xmlns=\"http://www.w3.org/2005/Atom\">
          <entry>
            <title>Storm warning</title>
            <link href=\"https://example.com/b\" rel=\"alternate\"/>
            <summary>Hail expected</summary>
            <updated>2024-03-01T10:00:00Z</updated>
          </entry>
        </feed>
        """.strip()

        with patch.object(rss_utils.httpx, "get", return_value=_Resp(rss_xml)):
            rss_items = rss_utils.parse_rss_feed("https://feed.example/rss")
        with patch.object(rss_utils.httpx, "get", return_value=_Resp(atom_xml)):
            atom_items = rss_utils.parse_rss_feed("https://feed.example/atom")

        self.assertEqual(len(rss_items), 1)
        self.assertEqual(rss_items[0]["title"], "Alluvione a Milano")
        self.assertEqual(rss_items[0]["enclosure_url"], "https://example.com/img.jpg")

        self.assertEqual(len(atom_items), 1)
        self.assertEqual(atom_items[0]["title"], "Storm warning")
        self.assertEqual(atom_items[0]["link"], "https://example.com/b")

    def test_extract_og_image_uses_range_and_parses_meta(self):
        html = '<html><head><meta property="og:image" content="https://img.example/x.jpg" /></head></html>'

        calls = []

        def _fake_get(url, headers=None, timeout=None, follow_redirects=None):
            calls.append({
                "url": url,
                "headers": headers,
                "timeout": timeout,
                "follow_redirects": follow_redirects,
            })
            return _Resp(html)

        with patch.object(rss_utils.httpx, "get", side_effect=_fake_get):
            img = rss_utils.extract_og_image("https://article.example/post")

        self.assertEqual(img, "https://img.example/x.jpg")
        self.assertEqual(calls[0]["headers"]["Range"], "bytes=0-8191")
        self.assertEqual(calls[0]["timeout"], 5)

    def test_keyword_match_event_applies_region_boost(self):
        self._add_event("ev-1", "Alluvione Lombardia", "flood", "Lombardia")
        db = self.SessionLocal()
        try:
            with patch.object(rss_utils, "match_event", return_value=("ev-1", 40)):
                event_id, confidence = rss_utils.keyword_match_event(db, "alluvione in lombardia")
            self.assertEqual(event_id, "ev-1")
            self.assertEqual(confidence, 60)
        finally:
            db.close()

    def test_keyword_match_event_matches_city_alias_without_base_match(self):
        self._add_event("ev-city", "Temporale severo Veneto", "storm", "Veneto")
        db = self.SessionLocal()
        try:
            with patch.object(rss_utils, "match_event", return_value=(None, 0)):
                event_id, confidence = rss_utils.keyword_match_event(
                    db,
                    "Grandine e maltempo su Padova e Vicenza nelle prossime ore",
                )
            self.assertEqual(event_id, "ev-city")
            self.assertGreaterEqual(confidence, 30)
        finally:
            db.close()

    def test_build_query_for_italian_flood_event(self):
        event = Event(
            id="ev-flood",
            title="Alluvione severa su Milano e hinterland",
            type="flood",
            severity="orange",
            status="ATTENZIONE",
            region="Lombardia",
        )

        query = news_google.build_query(event)

        self.assertIn("Lombardia", query)
        self.assertIn("alluvione OR esondazione OR piena", query)
        self.assertIn("lingua italiana", query)

    def test_fetch_google_news_persists_media_and_source(self):
        self._add_event("ev-gnews", "Alluvione area urbana", "flood", "Lombardia", severity="red")

        articles = [
            {
                "title": "Alluvione in Lombardia: esondazione del fiume",
                "link": "https://news.example/a",
                "description": "",
                "published": "Fri, 01 Mar 2024 10:00:00 GMT",
                "source": "ANSA",
            },
            {
                "title": "Maltempo in Lombardia oggi",
                "link": "https://news.example/b",
                "description": "",
                "published": "Fri, 01 Mar 2024 11:00:00 GMT",
                "source": "La Repubblica",
            },
        ]

        db = self.SessionLocal()
        try:
            with patch.object(news_google, "parse_rss_feed", return_value=articles), patch.object(
                news_google, "extract_og_image", return_value="https://img.example/thumb.jpg"
            ), patch.object(news_google.time, "sleep", return_value=None):
                saved = news_google.fetch_google_news(db)
                db.commit()

            self.assertEqual(saved, 2)

            src = db.query(Source).filter(Source.id == "gnews-ev-gnews").first()
            self.assertIsNotNone(src)
            assert src is not None
            self.assertEqual(src.platform, "google_news")
            self.assertEqual(src.type, "notizie")

            items = db.query(MediaItem).filter(MediaItem.source_id == "gnews-ev-gnews").all()
            self.assertEqual(len(items), 2)
            self.assertTrue(all(i.confidence >= 60 for i in items))
        finally:
            db.close()

    def test_google_confidence_uses_city_aliases(self):
        event = Event(
            id="ev-city-gnews",
            title="Temporale forte sul Veneto",
            type="storm",
            severity="orange",
            status="ATTENZIONE",
            region="Veneto",
        )

        confidence = news_google._compute_confidence(event, "Grandine e maltempo su Padova")
        self.assertGreaterEqual(confidence, 70)

    def test_fetch_rss_local_saves_and_deduplicates(self):
        self._add_event("ev-rss", "Temporale intenso", "storm", "Veneto", severity="orange")

        db = self.SessionLocal()
        try:
            with patch.dict(rss_local.REGIONAL_RSS, {
                "veneto": ["https://www.veneziatoday.it/rss"],
                "nazionale": ["https://www.ansa.it/sito/notizie/cronaca/cronaca_rss.xml"],
            }, clear=True), patch.object(
                rss_local, "parse_rss_feed", return_value=[
                    {
                        "title": "Grandine in Veneto",
                        "link": "https://local.example/article-1",
                        "description": "evento meteo",
                        "published": "Fri, 01 Mar 2024 12:00:00 GMT",
                    }
                ]
            ), patch.object(rss_local, "match_event", return_value=("ev-rss", 55)), patch.object(
                rss_local, "extract_og_image", return_value="https://img.example/local.jpg"
            ), patch.object(rss_local.time, "sleep", return_value=None):
                first = rss_local.fetch_rss_local(db)
                db.commit()
                second = rss_local.fetch_rss_local(db)
                db.commit()

            self.assertEqual(first, 1)
            self.assertEqual(second, 0)

            src = db.query(Source).filter(Source.id == "rss-local-veneziatoday.it").first()
            self.assertIsNotNone(src)
            assert src is not None
            self.assertEqual(src.platform, "rss")
            self.assertEqual(src.type, "notizie")

            items = db.query(MediaItem).filter(MediaItem.event_id == "ev-rss").all()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].author, "veneziatoday.it")
        finally:
            db.close()

    def test_fetch_rss_local_creates_wildfire_event_when_no_match_exists(self):
        db = self.SessionLocal()
        try:
            with patch.dict(rss_local.REGIONAL_RSS, {
                "nazionale": ["https://www.meteoweb.eu/feed/"],
            }, clear=True), patch.object(
                rss_local, "parse_rss_feed", return_value=[
                    {
                        "title": "Incendi: vigili del fuoco in azione in Lombardia per 2 roghi",
                        "link": "https://local.example/incendio-1",
                        "description": "Maxi incendio con fiamme e mezzi in azione in Lombardia.",
                        "published": "Sun, 05 Apr 2026 09:36:42 +0000",
                    }
                ]
            ), patch.object(rss_local, "match_event", return_value=(None, 0)), patch.object(
                rss_local, "extract_og_image", return_value=None
            ), patch.object(rss_local.time, "sleep", return_value=None):
                saved = rss_local.fetch_rss_local(db)
                db.commit()

            self.assertEqual(saved, 1)

            wildfire_event = db.query(Event).filter(Event.category == "wildfire").first()
            self.assertIsNotNone(wildfire_event)
            assert wildfire_event is not None
            self.assertEqual(wildfire_event.region, "Lombardia")
            self.assertIsNotNone(wildfire_event.lat)
            self.assertIsNotNone(wildfire_event.lon)

            items = db.query(MediaItem).filter(MediaItem.event_id == wildfire_event.id).all()
            self.assertEqual(len(items), 1)
            self.assertIn("Incendi", items[0].caption)
        finally:
            db.close()

    def test_get_live_wildfire_candidates_skips_foreign_fire_story(self):
        with patch.dict(rss_local.REGIONAL_RSS, {
            "nazionale": ["https://www.meteoweb.eu/feed/"],
            "lombardia": [],
            "toscana": [],
        }, clear=True), patch.object(
            rss_local, "parse_rss_feed", return_value=[
                {
                    "title": "Abu Dhabi, domati incendi al petrolchimico Borouge dopo la caduta di detriti",
                    "link": "https://example.com/abu-dhabi-fire",
                    "description": "Intervento all'estero, fuori dall'Italia.",
                    "published": "Sun, 05 Apr 2026 13:26:12 +0000",
                }
            ]
        ):
            rows = rss_local.get_live_wildfire_candidates(limit=6)

        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
