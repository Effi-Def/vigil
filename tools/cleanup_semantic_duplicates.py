"""
Remove semantic near-duplicates in media_items history.

Usage:
  python tools/cleanup_semantic_duplicates.py --days 60
  python tools/cleanup_semantic_duplicates.py --days 60 --apply
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vigil.collectors.matcher import quality_score, title_signature
from vigil.core.database import SessionLocal
from vigil.core.models import MediaItem, Source


def jaccard(a: set[str], b: set[str]) -> float:
    u = len(a | b)
    if u == 0:
        return 0.0
    return len(a & b) / u


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cleanup semantic duplicates from media_items")
    p.add_argument("--days", type=int, default=30, help="Lookback window in days")
    p.add_argument("--threshold", type=float, default=0.82, help="Jaccard similarity threshold")
    p.add_argument("--apply", action="store_true", help="Apply deletions (default is dry-run)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=args.days)

    with SessionLocal() as db:
        rows = (
            db.query(MediaItem, Source.platform)
            .outerjoin(Source, MediaItem.source_id == Source.id)
            .filter(MediaItem.fetched_at >= cutoff)
            .order_by(MediaItem.event_id.asc(), MediaItem.media_type.asc(), MediaItem.fetched_at.desc())
            .all()
        )

        groups: dict[tuple[str, str], list[tuple[MediaItem, str | None]]] = {}
        for item, platform in rows:
            key = (str(item.event_id), str(item.media_type or "other"))
            groups.setdefault(key, []).append((item, platform))

        to_delete: list[int] = []
        checked = 0
        for (_event_id, _media_type), items in groups.items():
            survivors: list[tuple[int, set[str], int]] = []
            for item, platform in items:
                checked += 1
                sig = title_signature(str(item.caption or ""))
                if not sig:
                    continue
                tok = set(sig.split())
                item_id = int(getattr(item, "id", 0) or 0)
                item_confidence = int(getattr(item, "confidence", 0) or 0)
                item_captured = getattr(item, "captured_at", None)
                item_fetched = getattr(item, "fetched_at", None)
                item_media_type = str(getattr(item, "media_type", "") or "")
                item_thumb = bool(getattr(item, "thumb_url", None))
                q = quality_score(
                    confidence=item_confidence,
                    media_type=item_media_type,
                    platform=platform,
                    captured_at=item_captured,
                    fetched_at=item_fetched,
                    has_thumb=item_thumb,
                )

                duplicate_of = None
                for idx, stoks, sq in survivors:
                    if jaccard(tok, stoks) >= float(args.threshold):
                        duplicate_of = (idx, sq)
                        break

                if duplicate_of is None:
                    survivors.append((item_id, tok, q))
                else:
                    keep_id, keep_q = duplicate_of
                    if q > keep_q:
                        to_delete.append(keep_id)
                        survivors = [s for s in survivors if s[0] != keep_id]
                        survivors.append((item_id, tok, q))
                    else:
                        to_delete.append(item_id)

        unique_ids = sorted(set(to_delete))
        print(f"CHECKED={checked}")
        print(f"DUPLICATES_FOUND={len(unique_ids)}")

        if not args.apply:
            print("MODE=DRY_RUN")
            if unique_ids:
                print("SAMPLE_IDS=", unique_ids[:20])
            return 0

        if unique_ids:
            (
                db.query(MediaItem)
                .filter(MediaItem.id.in_(unique_ids))
                .delete(synchronize_session=False)
            )
            db.commit()
        print("MODE=APPLY")
        print(f"DELETED={len(unique_ids)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
