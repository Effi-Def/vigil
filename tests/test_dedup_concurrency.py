import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from vigil.core.models import Base, Event, MediaItem, Source


class DedupConcurrencyTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

        session = self.SessionLocal()
        try:
            session.add(
                Event(
                    id="gdacs-tc-123",
                    title="Typhoon X",
                    type="cyclone",
                    severity="red",
                    status="CRITICO",
                )
            )
            session.add(
                Source(
                    id="dpc-bollettino",
                    name="Bollettino DPC",
                    type="ufficiale",
                    platform="dpc",
                    event_id=None,
                )
            )
            session.commit()
        finally:
            session.close()

    def tearDown(self):
        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_atomic_dedup_with_concurrent_sessions(self):
        barrier = threading.Barrier(2)
        errors = []

        def worker(author_name: str):
            session = self.SessionLocal()
            try:
                existing = (
                    session.query(MediaItem)
                    .filter(MediaItem.content_hash == "race-hash")
                    .first()
                )
                if existing is not None:
                    session.commit()
                    return

                barrier.wait(timeout=3)

                item = MediaItem(
                    event_id="gdacs-tc-123",
                    source_id="dpc-bollettino",
                    media_url="https://example.com/img.jpg",
                    thumb_url="https://example.com/thumb.jpg",
                    caption="duplicate content",
                    author=author_name,
                    captured_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    confidence=75,
                    content_hash="race-hash",
                )

                try:
                    with session.begin_nested():
                        session.add(item)
                        session.flush()
                except IntegrityError:
                    # Expected on one of the two sessions in a race.
                    pass

                session.commit()
            except Exception as exc:
                errors.append(exc)
            finally:
                session.close()

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertFalse(errors, f"Unexpected thread errors: {errors}")

        check = self.SessionLocal()
        try:
            count = check.query(MediaItem).filter(MediaItem.content_hash == "race-hash").count()
        finally:
            check.close()

        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
