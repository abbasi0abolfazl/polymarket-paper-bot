"""Read-only Chainlink TWAP capture and market-data synchronization checks."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .models import MarketSnapshot


class ReferenceFeedError(RuntimeError):
    """The public reference feed returned unusable data or became unavailable."""


@dataclass(frozen=True)
class BtcTwapObservation:
    symbol: str
    value: str
    observed_at: float
    received_at: float
    window_seconds: int
    source: str = "polymarket-rtds-chainlink-twap"

    @property
    def decimal_value(self) -> Decimal:
        return Decimal(self.value)


def parse_twap_event(event: dict[str, Any], symbol: str = "btc/usd") -> BtcTwapObservation:
    """Validate a documented RTDS Chainlink TWAP update without losing precision."""
    payload = event.get("payload")
    if event.get("type") != "update" or not isinstance(payload, dict):
        raise ReferenceFeedError("Expected a Chainlink TWAP update event")
    if str(payload.get("symbol", "")).lower() != symbol.lower():
        raise ReferenceFeedError(f"Expected {symbol}, received {payload.get('symbol')}")
    window = payload.get("windowSeconds", payload.get("window_s"))
    if window not in (30, 60):
        raise ReferenceFeedError("TWAP window must be 30 or 60 seconds")
    try:
        value = str(payload["value"])
        decimal_value = Decimal(value)
        observed_at = _epoch_seconds(payload["timestamp"])
    except (KeyError, InvalidOperation, TypeError, ValueError) as error:
        raise ReferenceFeedError(f"Invalid Chainlink TWAP event: {error}") from error
    if observed_at <= 0:
        raise ReferenceFeedError("Chainlink observation timestamp is missing or invalid")
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ReferenceFeedError("Chainlink TWAP value must be a positive finite number")
    return BtcTwapObservation(
        symbol=symbol.lower(),
        value=value,
        observed_at=observed_at,
        received_at=time.time(),
        window_seconds=window,
    )


def append_observation(path: str | Path, observation: BtcTwapObservation) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(asdict(observation), separators=(",", ":")))
        stream.write("\n")


def load_observations(path: str | Path) -> list[BtcTwapObservation]:
    observations: list[BtcTwapObservation] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                observations.append(BtcTwapObservation(**payload))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"Invalid BTC observation on line {line_number}: {error}"
                ) from error
    return sorted(observations, key=lambda item: item.observed_at)


def synchronization_report(
    snapshots: list[MarketSnapshot],
    observations: list[BtcTwapObservation],
    max_age_seconds: float = 5.0,
) -> dict[str, int | float]:
    """Report whether every market observation has a sufficiently fresh BTC point."""
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    ages: list[float] = []
    for snapshot in snapshots:
        nearest = _nearest_observation(snapshot.timestamp, observations)
        if nearest is not None:
            ages.append(abs(snapshot.timestamp - nearest.observed_at))
    fresh = sum(age <= max_age_seconds for age in ages)
    return {
        "market_snapshots": len(snapshots),
        "btc_observations": len(observations),
        "matched_snapshots": len(ages),
        "fresh_snapshots": fresh,
        "stale_or_missing_snapshots": len(snapshots) - fresh,
        "max_age_seconds": max_age_seconds,
        "worst_match_age_seconds": max(ages, default=0.0),
        "mean_match_age_seconds": sum(ages) / len(ages) if ages else 0.0,
    }


async def capture_twap(
    output: str | Path,
    samples: int,
    window_seconds: int = 30,
    symbol: str = "btc/usd",
    endpoint: str = "wss://ws-live-data.polymarket.com",
    max_retries: int = 5,
) -> int:
    """Capture public RTDS updates with heartbeat and bounded reconnects.

    The optional ``websockets`` package is loaded only for this public, read-only
    command. No credentials or account information are used.
    """
    if samples < 1:
        raise ValueError("samples must be at least 1")
    if window_seconds not in (30, 60):
        raise ValueError("window_seconds must be 30 or 60")
    try:
        import websockets
    except ImportError as error:
        raise ReferenceFeedError(
            "BTC capture requires the optional live dependency. Install with: "
            "python -m pip install -e '.[live]'"
        ) from error

    topic = "crypto_prices_twap_thirty" if window_seconds == 30 else "crypto_prices_twap_sixty"
    subscription = {
        "action": "subscribe",
        "subscriptions": [
            {
                "topic": topic,
                "type": "update",
                "filters": json.dumps({"symbol": symbol.lower()}, separators=(",", ":")),
            }
        ],
    }
    captured = 0
    attempts = 0
    while captured < samples:
        try:
            async with websockets.connect(endpoint, ping_interval=None) as socket:
                attempts = 0
                await socket.send(json.dumps(subscription, separators=(",", ":")))
                while captured < samples:
                    try:
                        raw_event = await asyncio.wait_for(socket.recv(), timeout=5.0)
                    except TimeoutError:
                        await socket.send("PING")
                        continue
                    if not isinstance(raw_event, str):
                        continue
                    try:
                        observation = parse_twap_event(json.loads(raw_event), symbol)
                    except (json.JSONDecodeError, ReferenceFeedError):
                        continue
                    if observation.window_seconds != window_seconds:
                        continue
                    append_observation(output, observation)
                    captured += 1
        except (OSError, websockets.exceptions.WebSocketException) as error:
            attempts += 1
            if attempts > max_retries:
                raise ReferenceFeedError(
                    f"BTC reference feed failed after {max_retries} reconnects: {error}"
                ) from error
            await asyncio.sleep(min(2**attempts, 30))
    return captured


def _nearest_observation(
    timestamp: float, observations: list[BtcTwapObservation]
) -> BtcTwapObservation | None:
    return min(observations, key=lambda item: abs(timestamp - item.observed_at), default=None)


def _epoch_seconds(value: Any) -> float:
    timestamp = float(value)
    return timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
