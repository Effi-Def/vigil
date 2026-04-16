from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from vigil.core.models import Base, CollectorHealth
import os

from sqlalchemy.engine.url import make_url

DATABASE_URL = os.getenv("VIGIL_DB_URL", "sqlite:///./vigil.db")

# Fail-fast su URL DB malformata.
make_url(DATABASE_URL)

_connect_args = {}
if "sqlite" in DATABASE_URL:
    _connect_args = {
        "check_same_thread": False,
        "timeout": 30,
    }

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _run_migrations():
    """Aggiunge colonne nuove a tabelle esistenti senza perdere dati."""
    from sqlalchemy import text
    new_columns = [
        ("events", "temp_c", "REAL"),
        ("events", "precipitation_mm", "REAL"),
        ("events", "category", "TEXT"),
        ("events", "is_alert", "BOOLEAN DEFAULT 0"),
        ("events", "parent_event_id", "TEXT"),
        ("events", "subcategory", "TEXT"),
        ("events", "derived_from", "TEXT"),
        ("media_items", "media_type", "TEXT DEFAULT 'article'"),
        ("media_items", "relevance_score", "REAL"),
        ("hydro_stations", "data_quality", "TEXT NOT NULL DEFAULT 'synthetic'"),
    ]
    with engine.connect() as conn:
        for table, col, col_type in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                pass  # colonna già presente

        try:
            conn.execute(text(
                "UPDATE media_items SET media_type = 'article' "
                "WHERE media_type IS NULL OR media_type NOT IN ('article','image','video','webcam')"
            ))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text(
                "UPDATE hydro_stations SET data_quality = 'synthetic' "
                "WHERE data_quality IS NULL OR data_quality NOT IN ('synthetic','measured','estimated')"
            ))
            conn.commit()
        except Exception:
            pass


def init_db():
    """Crea tutte le tabelle se non esistono e applica migrazioni leggere."""
    Base.metadata.create_all(bind=engine)
    _run_migrations()


@contextmanager
def get_session() -> Session:
    """Context manager per sessioni DB con rollback automatico su errore."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """Dependency per FastAPI."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
