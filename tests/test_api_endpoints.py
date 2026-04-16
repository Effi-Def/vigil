import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from vigil.core.models import Base, Event, MediaItem, Source


class ApiEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher_init_db = patch("main.init_db", autospec=True)
        cls.patcher_start_scheduler = patch("main.start_scheduler", autospec=True)
        cls.patcher_stop_scheduler = patch("main.stop_scheduler", autospec=True)
        cls.patcher_init_db.start()
        cls.patcher_start_scheduler.start()
        cls.patcher_stop_scheduler.start()

    @classmethod
    def tearDownClass(cls):
        cls.patcher_stop_scheduler.stop()
        cls.patcher_start_scheduler.stop()
        cls.patcher_init_db.stop()

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        self._seed_data()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_data(self):
        session = self.SessionLocal()
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            session.add_all(
                [
                    Event(
                        id="gdacs-tc-1",
                        title="Typhoon K",
                        type="cyclone",
                        severity="red",
                        status="CRITICO",
                        region="Asia",
                        lat=41.87,
                        lon=12.57,
                        updated_at=now,
                    ),
                    Event(
                        id="gdacs-fl-2",
                        title="Flood City",
                        type="flood",
                        severity="orange",
                        status="ATTENZIONE",
                        region="Europa",
                        lat=45.46,
                        lon=9.19,
                        updated_at=now - timedelta(hours=1),
                    ),
                ]
            )

            session.add_all(
                [
                    Source(
                        id="dpc-bollettino",
                        name="Bollettino DPC",
                        type="ufficiale",
                        platform="dpc",
                        event_id="gdacs-tc-1",
                    ),
                    Source(
                        id="meteoalarm-it",
                        name="Meteoalarm Italia",
                        type="ufficiale",
                        platform="meteoalarm",
                        event_id="gdacs-tc-1",
                    ),
                    Source(
                        id="telegram-meteo",
                        name="Meteo Channel",
                        type="social",
                        platform="telegram",
                        event_id="gdacs-tc-1",
                    ),
                ]
            )

            session.add_all(
                [
                    MediaItem(
                        event_id="gdacs-tc-1",
                        source_id="dpc-bollettino",
                        media_url="https://example.com/a.jpg",
                        caption="A",
                        media_type="image",
                        confidence=80,
                        fetched_at=now - timedelta(minutes=5),
                        captured_at=now - timedelta(minutes=10),
                        content_hash="h1",
                    ),
                    MediaItem(
                        event_id="gdacs-tc-1",
                        source_id="meteoalarm-it",
                        media_url="https://example.com/ma.jpg",
                        caption="MA",
                        media_type="image",
                        confidence=70,
                        fetched_at=now - timedelta(minutes=3),
                        captured_at=now - timedelta(minutes=4),
                        content_hash="h4",
                    ),
                    MediaItem(
                        event_id="gdacs-tc-1",
                        source_id="telegram-meteo",
                        media_url="https://example.com/b.jpg",
                        caption="B",
                        media_type="image",
                        confidence=55,
                        fetched_at=now - timedelta(minutes=2),
                        captured_at=now - timedelta(minutes=3),
                        content_hash="h2",
                    ),
                    MediaItem(
                        event_id="gdacs-fl-2",
                        source_id="dpc-bollettino",
                        media_url="https://example.com/c.jpg",
                        caption="C",
                        media_type="image",
                        confidence=95,
                        fetched_at=now - timedelta(minutes=1),
                        captured_at=now - timedelta(minutes=1),
                        content_hash="h3",
                    ),
                ]
            )
            session.commit()
        finally:
            session.close()

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertIn("collectors_status", payload)

    def test_list_events_filters(self):
        response = self.client.get("/events", params={"severity": "red", "type": "cyclone"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["id"], "gdacs-tc-1")
        self.assertIn("media_count", payload[0])
        self.assertGreaterEqual(payload[0]["media_count"], 1)

    def test_list_media_filters_platform_confidence_and_limit(self):
        response = self.client.get(
            "/events/gdacs-tc-1/media",
            params={"platform": "telegram", "min_confidence": 50, "limit": 10},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["source_id"], "telegram-meteo")

    def test_recent_media_sorted_and_filtered(self):
        response = self.client.get("/media/recent", params={"limit": 2, "min_confidence": 60})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 2)
        self.assertGreaterEqual(payload[0]["confidence"], 60)
        self.assertGreaterEqual(payload[1]["confidence"], 60)

    def test_collectors_status_summary(self):
        response = self.client.get("/collectors/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertIn("totals", payload)
        self.assertIn("by_platform", payload)
        self.assertIn("by_event_type", payload)
        self.assertIn("by_severity", payload)
        self.assertIn("recent_sources", payload)

        self.assertEqual(payload["totals"]["events"], 2)
        self.assertEqual(payload["totals"]["media_items"], 4)
        self.assertEqual(payload["totals"]["sources"], 3)

        platforms = {row["platform"] for row in payload["by_platform"]}
        self.assertIn("dpc", platforms)
        self.assertIn("meteoalarm", platforms)
        self.assertIn("telegram", platforms)


if __name__ == "__main__":
    unittest.main()
