"""Durable, read-only data-capture records for paper-trading research."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .models import MarketSnapshot


class SnapshotJournal:
    """A small SQLite journal with duplicate and chronology protection.

    It records observed public-market data only. It deliberately contains no
    authentication, balances, wallet addresses, signing, or execution methods.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SnapshotJournal":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def record(self, snapshot: MarketSnapshot) -> bool:
        payload = _snapshot_payload(snapshot)
        digest = _digest(payload)
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO snapshots
                (digest, market_id, observed_at, source, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (digest, snapshot.market_id, snapshot.timestamp, snapshot.source, payload),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def audit(self) -> dict[str, int | float]:
        row = self.connection.execute(
            """
            SELECT
                COUNT(*) AS snapshot_count,
                COUNT(DISTINCT market_id) AS market_count,
                COUNT(DISTINCT digest) AS unique_count,
                COALESCE(MIN(observed_at), 0) AS first_timestamp,
                COALESCE(MAX(observed_at), 0) AS last_timestamp
            FROM snapshots
            """
        ).fetchone()
        ordering_violations = self.connection.execute(
            """
            WITH ordered AS (
                SELECT market_id, observed_at,
                       LAG(observed_at) OVER (
                           PARTITION BY market_id ORDER BY id
                       ) AS previous_timestamp
                FROM snapshots
            )
            SELECT COUNT(*) AS count
            FROM ordered
            WHERE previous_timestamp IS NOT NULL
              AND observed_at < previous_timestamp
            """
        ).fetchone()["count"]
        return {
            "snapshot_count": row["snapshot_count"],
            "market_count": row["market_count"],
            "unique_count": row["unique_count"],
            "duplicate_count": row["snapshot_count"] - row["unique_count"],
            "first_timestamp": row["first_timestamp"],
            "last_timestamp": row["last_timestamp"],
            "ordering_violations": ordering_violations,
        }

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY,
                digest TEXT NOT NULL UNIQUE,
                market_id TEXT NOT NULL,
                observed_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS snapshots_market_time
                ON snapshots (market_id, observed_at);
            """
        )
        self.connection.commit()


def _snapshot_payload(snapshot: MarketSnapshot) -> str:
    return json.dumps(asdict(snapshot), sort_keys=True, separators=(",", ":"))


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
