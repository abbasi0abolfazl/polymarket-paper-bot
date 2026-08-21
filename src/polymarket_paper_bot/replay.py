from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import MarketSnapshot, OrderBookLevel


def load_snapshots(path: str | Path) -> list[MarketSnapshot]:
    snapshots: list[MarketSnapshot] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                payload["up_asks"] = tuple(OrderBookLevel(**item) for item in payload["up_asks"])
                payload["down_asks"] = tuple(
                    OrderBookLevel(**item) for item in payload["down_asks"]
                )
                snapshots.append(MarketSnapshot(**payload))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"Invalid snapshot on line {line_number}: {error}") from error
    return snapshots


def append_snapshot(path: str | Path, snapshot: MarketSnapshot) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(asdict(snapshot), separators=(",", ":")))
        stream.write("\n")
