from datetime import datetime, timezone

from sqlalchemy.orm import Session

from vigil.core.models import CollectorHealth


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def update_health(db: Session, collector: str, items: int, error: str | None = None) -> CollectorHealth:
    record = (
        db.query(CollectorHealth)
        .filter(CollectorHealth.collector == collector)
        .first()
    )
    if record is None:
        record = CollectorHealth(collector=collector)
        db.add(record)

    now = _utc_now_naive()
    item_count = int(items or 0)

    record.last_run = now
    record.items_last = item_count
    record.items_total = int(record.items_total or 0) + item_count
    record.run_count = int(record.run_count or 0) + 1

    if error is None:
        record.last_ok = now
        record.last_error = None
        record.ok_count = int(record.ok_count or 0) + 1
    else:
        record.last_error = str(error)[:500]

    db.flush()
    return record
