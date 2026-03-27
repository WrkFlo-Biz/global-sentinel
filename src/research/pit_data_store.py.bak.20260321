"""Point-in-time data store for reproducible research snapshots."""
from __future__ import annotations

import gzip
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class PointInTimeDataStore:
    """Capture and replay timestamped snapshots of bridge results, scorecards,
    and supporting data so that any past decision can be reproduced exactly."""

    def __init__(self, store_dir: str = "data/pit", repo_root: str | None = None):
        if repo_root:
            self._dir = Path(repo_root) / store_dir
        else:
            self._dir = Path(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def capture(
        self,
        bridge_results: dict | list | None = None,
        scorecard: dict | None = None,
        extra: dict | None = None,
    ) -> Path:
        """Save a gzip-compressed JSON snapshot (~50 KB typical).

        *extra* is a grab-bag for chokepoint scores, analog matches, trade
        ideas, exposure, market prices, or anything else worth preserving.
        """
        now = datetime.now(tz=timezone.utc)
        snapshot = {
            "timestamp": now.isoformat(),
            "bridge_results": bridge_results,
            "scorecard": scorecard,
            "extra": extra,
        }
        fname = f"snapshot_{now.strftime('%Y%m%d_%H%M%S')}.json.gz"
        path = self._dir / fname
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(snapshot, f, default=str)
        logger.info("PIT snapshot saved: %s (%d bytes)", fname, path.stat().st_size)
        return path

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def replay(
        self,
        start_date: datetime | str | None = None,
        end_date: datetime | str | None = None,
    ) -> list[dict]:
        """Load snapshots in chronological order, optionally filtered by date range."""
        start = self._parse_date(start_date) if start_date else None
        end = self._parse_date(end_date) if end_date else None

        snapshots: list[dict] = []
        for p in sorted(self._dir.glob("snapshot_*.json.gz")):
            ts = self._timestamp_from_filename(p.name)
            if ts is None:
                continue
            if start and ts < start:
                continue
            if end and ts > end:
                continue
            try:
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    snapshots.append(json.load(f))
            except Exception:
                logger.warning("Skipping corrupt snapshot: %s", p.name)
        return snapshots

    def replay_through_pipeline(
        self,
        snapshots: list[dict],
        pipeline_fn,
    ) -> list:
        """Feed each snapshot through *pipeline_fn* and collect results."""
        results = []
        for snap in snapshots:
            try:
                results.append(pipeline_fn(snap))
            except Exception:
                logger.exception("Pipeline failed on snapshot %s", snap.get("timestamp"))
                results.append(None)
        return results

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup(self, keep_days: int = 30) -> int:
        """Remove snapshots older than *keep_days*.  Returns count deleted."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=keep_days)
        deleted = 0
        for p in self._dir.glob("snapshot_*.json.gz"):
            ts = self._timestamp_from_filename(p.name)
            if ts and ts < cutoff:
                p.unlink()
                deleted += 1
        if deleted:
            logger.info("PIT cleanup: removed %d snapshots older than %d days", deleted, keep_days)
        return deleted

    def list_snapshots(self) -> list[dict]:
        """Return list of ``{"filename": …, "timestamp": …}`` dicts."""
        entries = []
        for p in sorted(self._dir.glob("snapshot_*.json.gz")):
            ts = self._timestamp_from_filename(p.name)
            if ts:
                entries.append({"filename": p.name, "timestamp": ts.isoformat()})
        return entries

    def stats(self) -> dict:
        """Return summary statistics about the store."""
        files = sorted(self._dir.glob("snapshot_*.json.gz"))
        if not files:
            return {"count": 0, "oldest": None, "newest": None, "total_size_mb": 0.0}
        timestamps = [self._timestamp_from_filename(f.name) for f in files]
        timestamps = [t for t in timestamps if t is not None]
        total_bytes = sum(f.stat().st_size for f in files)
        return {
            "count": len(files),
            "oldest": min(timestamps).isoformat() if timestamps else None,
            "newest": max(timestamps).isoformat() if timestamps else None,
            "total_size_mb": round(total_bytes / (1024 * 1024), 2),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)

    @staticmethod
    def _timestamp_from_filename(name: str) -> datetime | None:
        # snapshot_20260308_143022.json.gz → 2026-03-08 14:30:22 UTC
        try:
            stem = name.replace("snapshot_", "").replace(".json.gz", "")
            return datetime.strptime(stem, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
