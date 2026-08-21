from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .models import MarketSnapshot, OrderBookLevel


class PublicApiError(RuntimeError):
    """A safe, credential-free public API request failed."""


@dataclass(frozen=True)
class AccessStatus:
    blocked: bool
    country: str
    region: str


@dataclass(frozen=True)
class PublicMarket:
    market_id: str
    event_slug: str
    market_slug: str
    question: str
    end_timestamp: float
    up_token_id: str
    down_token_id: str
    taker_fee_rate: float


@dataclass(frozen=True)
class PublicOrderBook:
    token_id: str
    timestamp: float
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]


JsonOpener = Callable[[str], Any]


class PolymarketPublicClient:
    """Read-only public-data client. It has no authentication or order methods."""

    def __init__(
        self,
        opener: JsonOpener | None = None,
        gamma_base_url: str = "https://gamma-api.polymarket.com",
        clob_base_url: str = "https://clob.polymarket.com",
        website_base_url: str = "https://polymarket.com",
        default_taker_fee_rate: float = 0.07,
    ) -> None:
        self._opener = opener or self._request_json
        self.gamma_base_url = gamma_base_url.rstrip("/")
        self.clob_base_url = clob_base_url.rstrip("/")
        self.website_base_url = website_base_url.rstrip("/")
        self.default_taker_fee_rate = default_taker_fee_rate

    def check_access(self) -> AccessStatus:
        try:
            payload = self._opener(f"{self.website_base_url}/api/geoblock")
        except PublicApiError as error:
            if "HTTP 403" in str(error):
                return AccessStatus(blocked=True, country="unknown", region="")
            raise
        return AccessStatus(
            blocked=bool(payload.get("blocked", False)),
            country=str(payload.get("country", "unknown")),
            region=str(payload.get("region", "")),
        )

    def discover_active_markets(
        self,
        query: str = "Bitcoin Up or Down",
        event_slug: str | None = None,
        limit: int = 100,
    ) -> list[PublicMarket]:
        if event_slug:
            quoted_slug = urllib.parse.quote(event_slug, safe="-")
            payload = self._opener(
                f"{self.gamma_base_url}/events/slug/{quoted_slug}"
            )
            events = [payload]
        else:
            params = urllib.parse.urlencode({"closed": "false", "limit": limit})
            payload = self._opener(
                f"{self.gamma_base_url}/events/keyset?{params}"
            )
            if isinstance(payload, dict):
                events = payload.get("events", [])
            elif isinstance(payload, list):
                events = payload
            else:
                events = []

        now = time.time()
        matches: list[PublicMarket] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            for market in event.get("markets", ()):
                parsed = self._parse_market(event, market)
                if parsed is None:
                    continue
                searchable = f"{event.get('title', '')} {parsed.question}"
                if query and not _query_matches(query, searchable):
                    continue
                if parsed.end_timestamp and parsed.end_timestamp <= now:
                    continue
                matches.append(parsed)
        return sorted(matches, key=lambda market: market.end_timestamp or float("inf"))

    def get_order_book(self, token_id: str) -> PublicOrderBook:
        params = urllib.parse.urlencode({"token_id": token_id})
        payload = self._opener(f"{self.clob_base_url}/book?{params}")
        bids = _parse_levels(payload.get("bids", ()), reverse=True)
        asks = _parse_levels(payload.get("asks", ()), reverse=False)
        if not asks:
            raise PublicApiError(f"No asks are available for token {token_id}")
        timestamp = _epoch_seconds(payload.get("timestamp")) or time.time()
        return PublicOrderBook(
            token_id=token_id,
            timestamp=timestamp,
            bids=bids,
            asks=asks,
        )

    def snapshot(self, market: PublicMarket) -> MarketSnapshot:
        started_at = time.time()
        up_book = self.get_order_book(market.up_token_id)
        down_book = self.get_order_book(market.down_token_id)
        observed_at = time.time()
        up_mid = _midpoint(up_book)
        down_mid = _midpoint(down_book)
        probability_total = up_mid + down_mid
        if probability_total <= 0:
            raise PublicApiError("Cannot derive a market-implied probability")
        prior_up_probability = min(max(up_mid / probability_total, 1e-6), 1 - 1e-6)
        oldest_book = min(up_book.timestamp, down_book.timestamp)
        data_age = max(observed_at - oldest_book, observed_at - started_at, 0.0)
        return MarketSnapshot(
            market_id=market.market_id,
            timestamp=int(observed_at),
            seconds_remaining=max(market.end_timestamp - observed_at, 0.0),
            prior_up_probability=prior_up_probability,
            signal_multiplier=1.0,
            short_term_volatility=0.0,
            up_asks=up_book.asks,
            down_asks=down_book.asks,
            taker_fee_rate=market.taker_fee_rate,
            data_age_seconds=data_age,
            book_skew_seconds=abs(up_book.timestamp - down_book.timestamp),
            source="polymarket-public-api",
            question=market.question,
            up_token_id=market.up_token_id,
            down_token_id=market.down_token_id,
        )

    def _parse_market(
        self, event: dict[str, Any], market: dict[str, Any]
    ) -> PublicMarket | None:
        if not isinstance(market, dict):
            return None
        if market.get("closed") is True or market.get("active") is False:
            return None
        outcomes = _json_list(market.get("outcomes"))
        token_ids = _json_list(
            market.get("clobTokenIds") or market.get("clob_token_ids")
        )
        if len(outcomes) != len(token_ids) or len(token_ids) < 2:
            return None
        up_index = _outcome_index(outcomes, ("up", "yes"))
        down_index = _outcome_index(outcomes, ("down", "no"))
        if up_index is None or down_index is None:
            return None

        fees_enabled = market.get("feesEnabled")
        fee_rate = 0.0 if fees_enabled is False else self.default_taker_fee_rate
        raw_fee_rate = market.get("takerBaseFee")
        if raw_fee_rate is not None:
            try:
                candidate = float(raw_fee_rate)
                if 0 <= candidate <= 1:
                    fee_rate = candidate
            except (TypeError, ValueError):
                pass

        end_timestamp = _iso_timestamp(
            market.get("endDate")
            or market.get("end_date")
            or event.get("endDate")
            or event.get("end_date")
        )
        return PublicMarket(
            market_id=str(market.get("conditionId") or market.get("id") or ""),
            event_slug=str(event.get("slug") or ""),
            market_slug=str(market.get("slug") or ""),
            question=str(market.get("question") or event.get("title") or ""),
            end_timestamp=end_timestamp,
            up_token_id=str(token_ids[up_index]),
            down_token_id=str(token_ids[down_index]),
            taker_fee_rate=fee_rate,
        )

    @staticmethod
    def _request_json(url: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "polymarket-paper-bot/0.2 (read-only research)",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 403:
                raise PublicApiError(
                    "Public endpoint returned HTTP 403. Access may be unavailable "
                    "from this jurisdiction; this project will not bypass geographic restrictions."
                ) from error
            raise PublicApiError(f"Public API returned HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise PublicApiError(f"Public API request failed: {error}") from error


def _parse_levels(values: Any, reverse: bool) -> tuple[OrderBookLevel, ...]:
    levels: list[OrderBookLevel] = []
    for value in values or ():
        try:
            levels.append(
                OrderBookLevel(price=float(value["price"]), size=float(value["size"]))
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(sorted(levels, key=lambda level: level.price, reverse=reverse))


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _outcome_index(outcomes: list[Any], accepted: tuple[str, ...]) -> int | None:
    for index, outcome in enumerate(outcomes):
        if str(outcome).strip().lower() in accepted:
            return index
    return None


def _query_matches(query: str, text: str) -> bool:
    def normalize(value: str) -> set[str]:
        value = re.sub(r"\bbtc\b", "bitcoin", value.lower())
        return set(re.findall(r"[a-z0-9]+", value))

    return normalize(query).issubset(normalize(text))


def _iso_timestamp(value: Any) -> float:
    if not value:
        return 0.0
    try:
        normalized = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _epoch_seconds(value: Any) -> float:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return 0.0
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return timestamp


def _midpoint(book: PublicOrderBook) -> float:
    if book.bids and book.asks:
        return (book.bids[0].price + book.asks[0].price) / 2
    if book.asks:
        return book.asks[0].price
    if book.bids:
        return book.bids[0].price
    raise PublicApiError(f"Order book {book.token_id} has no price levels")
