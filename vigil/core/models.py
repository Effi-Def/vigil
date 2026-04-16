from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Text, ForeignKey, Index, Enum as SQLEnum
)
from sqlalchemy.orm import relationship, DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Event(Base):
    __tablename__ = "events"

    id = Column(String, primary_key=True)          # e.g. "gdacs-tc-1234"
    title = Column(String, nullable=False)
    type = Column(String, nullable=False)           # cyclone, flood, volcano, storm, ...
    severity = Column(String, nullable=False)       # red, orange, blue
    status = Column(String, nullable=False)         # CRITICO, ATTENZIONE, MODERATO
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    region = Column(String, nullable=True)
    parent_event_id = Column(String, ForeignKey("events.id"), nullable=True, index=True)
    subcategory = Column(String, nullable=True)
    derived_from = Column(String, nullable=True)
    wind_kmh = Column(Integer, nullable=True)
    pressure_hpa = Column(Integer, nullable=True)
    temp_c = Column(Float, nullable=True)
    precipitation_mm = Column(Float, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    is_alert: Mapped[bool | None] = mapped_column(default=False, nullable=True)
    started_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=_utc_now_naive, onupdate=_utc_now_naive)

    media_items = relationship("MediaItem", back_populates="event", cascade="all, delete-orphan")
    sources = relationship("Source", back_populates="event", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "severity": self.severity,
            "status": self.status,
            "lat": self.lat,
            "lon": self.lon,
            "region": self.region,
            "parent_event_id": self.parent_event_id,
            "subcategory": self.subcategory,
            "derived_from": self.derived_from,
            "is_subevent": bool(self.parent_event_id),
            "wind_kmh": self.wind_kmh,
            "pressure_hpa": self.pressure_hpa,
            "temp_c": self.temp_c,
            "precipitation_mm": self.precipitation_mm,
            "category": self.category,
            "is_alert": bool(self.is_alert),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class MediaItem(Base):
    __tablename__ = "media_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String, ForeignKey("events.id"), nullable=False)
    source_id = Column(String, ForeignKey("sources.id"), nullable=True)

    media_url = Column(Text, nullable=True)         # URL originale (foto/video)
    thumb_url = Column(Text, nullable=True)         # thumbnail se disponibile
    media_type = Column(String, default="article") # article, image, video, webcam
    caption = Column(Text, nullable=True)           # testo del post
    author = Column(String, nullable=True)          # username / canale

    lat = Column(Float, nullable=True)              # coordinate estratte
    lon = Column(Float, nullable=True)
    geo_raw = Column(String, nullable=True)         # testo grezzo originale ("near Valencia")

    captured_at = Column(DateTime, nullable=True)   # timestamp del post originale
    fetched_at = Column(DateTime, default=_utc_now_naive)

    confidence = Column(Integer, default=50)        # 0-100: certezza associazione evento
    relevance_score = Column(Float, nullable=True)  # 0.0-1.0: rilevanza operativa per summary/news
    content_hash = Column(String, nullable=True, unique=True)  # deduplicazione

    event = relationship("Event", back_populates="media_items")
    source = relationship("Source", back_populates="media_items")

    __table_args__ = (
        Index("ix_media_event_confidence", "event_id", "confidence"),
        Index("ix_media_event_confidence_captured", "event_id", "confidence", "captured_at"),
        Index("ix_media_fetched_at", "fetched_at"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "event_id": self.event_id,
            "source_id": self.source_id,
            "media_url": self.media_url,
            "thumb_url": self.thumb_url,
            "media_type": self.media_type,
            "caption": self.caption,
            "author": self.author,
            "platform": self.source.platform if self.source else None,
            "source_name": self.source.name if self.source else None,
            "source_url": self.source.url if self.source else None,
            "lat": self.lat,
            "lon": self.lon,
            "geo_raw": self.geo_raw,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "confidence": self.confidence,
            "relevance_score": self.relevance_score,
        }


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        Index("ix_source_platform", "platform"),
        Index("ix_source_event_id", "event_id"),
    )

    id = Column(String, primary_key=True)           # e.g. "reddit-r-typhoons"
    name = Column(String, nullable=False)           # "r/typhoons"
    type = Column(String, nullable=False)           # ufficiale, reddit, telegram, flickr
    platform = Column(String, nullable=False)       # gdacs, reddit, telegram, flickr
    url = Column(Text, nullable=True)

    event_id = Column(String, ForeignKey("events.id"), nullable=True)

    last_fetched = Column(DateTime, nullable=True)
    item_count = Column(Integer, default=0)

    event = relationship("Event", back_populates="sources")
    media_items = relationship("MediaItem", back_populates="source")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "platform": self.platform,
            "url": self.url,
            "event_id": self.event_id,
            "last_fetched": self.last_fetched.isoformat() if self.last_fetched else None,
            "item_count": self.item_count,
        }


class CollectorHealth(Base):
    __tablename__ = "collector_health"

    id = Column(Integer, primary_key=True, autoincrement=True)
    collector = Column(String, nullable=False, unique=True)
    last_run = Column(DateTime, nullable=True)
    last_ok = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)
    items_last = Column(Integer, default=0)
    items_total = Column(Integer, default=0)
    run_count = Column(Integer, default=0)
    ok_count = Column(Integer, default=0)

    def to_dict(self):
        return {
            "id": self.id,
            "collector": self.collector,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_ok": self.last_ok.isoformat() if self.last_ok else None,
            "last_error": self.last_error,
            "items_last": int(self.items_last or 0),
            "items_total": int(self.items_total or 0),
            "run_count": int(self.run_count or 0),
            "ok_count": int(self.ok_count or 0),
        }


class HydroDataQuality(str, Enum):
    synthetic = "synthetic"
    measured = "measured"
    estimated = "estimated"


class HydroStation(Base):
    """Hydrometric/Pluviometric station data from ARPA and regional providers."""
    __tablename__ = "hydro_stations"
    __table_args__ = (
        Index("ix_hydro_station_provider", "provider"),
        Index("ix_hydro_station_updated", "updated_at"),
        Index("ix_hydro_station_location", "lat", "lon"),
    )

    id = Column(String, primary_key=True)           # e.g. "arpa-em-rfi000" or "adbpo-po-001"
    provider = Column(String, nullable=False)       # arpa-em, adbpo, etc.
    station_code = Column(String, nullable=False)   # original provider code
    name = Column(String, nullable=False)           # station name
    river = Column(String, nullable=True)           # river name (for hydro stations)
    
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    
    # Hydrometric data (water levels/discharge)
    water_level_m = Column(Float, nullable=True)    # metres above reference
    discharge_m3s = Column(Float, nullable=True)    # cubic metres per second
    discharge_max_m3s = Column(Float, nullable=True)  # max discharge (for alerts)
    
    # Pluviometric data (rainfall)
    precip_mm = Column(Float, nullable=True)        # rainfall in last period
    precip_24h_mm = Column(Float, nullable=True)    # 24-hour rainfall
    
    # Computed fields
    hydro_level = Column(String, nullable=True)     # 'normal', 'moderate', 'high' (computed)
    hydro_index = Column(Float, nullable=True)      # 0-100+ (computed severity index)
    data_quality = Column(
        SQLEnum(HydroDataQuality, native_enum=False, validate_strings=True),
        nullable=False,
        default=HydroDataQuality.synthetic.value,
        server_default=HydroDataQuality.synthetic.value,
    )
    
    updated_at = Column(DateTime, default=_utc_now_naive, onupdate=_utc_now_naive)
    data_source = Column(String, nullable=True)     # 'measured' or 'estimated'

    def to_dict(self):
        return {
            "id": self.id,
            "provider": self.provider,
            "station_code": self.station_code,
            "name": self.name,
            "river": self.river,
            "lat": float(self.lat),
            "lon": float(self.lon),
            "water_level_m": float(self.water_level_m) if self.water_level_m is not None else None,
            "discharge_m3s": float(self.discharge_m3s) if self.discharge_m3s is not None else None,
            "discharge_max_m3s": float(self.discharge_max_m3s) if self.discharge_max_m3s is not None else None,
            "precip_mm": float(self.precip_mm) if self.precip_mm is not None else None,
            "precip_24h_mm": float(self.precip_24h_mm) if self.precip_24h_mm is not None else None,
            "hydro_level": self.hydro_level,
            "hydro_index": float(self.hydro_index) if self.hydro_index is not None else None,
            "data_quality": str(self.data_quality.value if isinstance(self.data_quality, HydroDataQuality) else self.data_quality),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "data_source": self.data_source,
        }
