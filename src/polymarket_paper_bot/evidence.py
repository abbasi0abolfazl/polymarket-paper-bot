"""Immutable market-resolution evidence for paper-trading research."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import MarketSnapshot
from .reference import BtcTwapObservation


@dataclass(frozen=True)
class MarketEvidence:
    market_id: str
    event_slug: str
    market_slug: str
    question: str
    window_start: float
    window_end: float
    opening_btc_price: str
    opening_btc_observed_at: float
    btc_source: str
    resolution_url: str
    resolution_text: str
    recorded_at: float

    def __post_init__(self) -> None:
        if not self.market_id:
            raise ValueError("market_id is required")
        if self.window_start <= 0 or self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        if not self.opening_btc_price or self.opening_btc_observed_at < self.window_start:
            raise ValueError("A BTC opening observation at or after window_start is required")
        if not self.resolution_url or not self.resolution_text.strip():
            raise ValueError("resolution_url and resolution_text are required")


def parse_timestamp(value: str) -> float:
    """Accept a Unix timestamp or ISO-8601 UTC timestamp."""
    try:
        timestamp = float(value)
    except ValueError:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            raise ValueError("ISO timestamp must include a timezone, for example Z")
        timestamp = parsed.timestamp()
    if timestamp <= 0:
        raise ValueError("timestamp must be positive")
    return timestamp


def opening_observation(
    observations: list[BtcTwapObservation], window_start: float, max_delay: float = 60.0
) -> BtcTwapObservation:
    """Use the first captured reference value at/after the market window start."""
    if max_delay <= 0:
        raise ValueError("max_delay must be positive")
    candidates = [item for item in observations if item.observed_at >= window_start]
    if not candidates:
        raise ValueError("No captured BTC observation exists at or after window_start")
    candidate = min(candidates, key=lambda item: item.observed_at)
    if candidate.observed_at - window_start > max_delay:
        raise ValueError("First BTC observation is too late to establish an opening price")
    return candidate


def write_evidence(path: str | Path, evidence: MarketEvidence) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Evidence already exists and is immutable: {destination}")
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(asdict(evidence), stream, indent=2, sort_keys=True)
        stream.write("\n")


def load_evidence(path: str | Path) -> MarketEvidence:
    with Path(path).open(encoding="utf-8") as stream:
        return MarketEvidence(**json.load(stream))


def evidence_report(
    snapshots: list[MarketSnapshot], evidence: MarketEvidence
) -> dict[str, int | float | bool]:
    matching = [item for item in snapshots if item.market_id == evidence.market_id]
    in_window = [
        item
        for item in matching
        if evidence.window_start <= item.timestamp <= evidence.window_end
    ]
    return {
        "market_id_matches": bool(matching),
        "resolution_evidence_complete": True,
        "captured_snapshots": len(matching),
        "snapshots_inside_declared_window": len(in_window),
        "snapshots_outside_declared_window": len(matching) - len(in_window),
        "opening_delay_seconds": evidence.opening_btc_observed_at - evidence.window_start,
    }
