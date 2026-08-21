from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Side = Literal["UP", "DOWN"]
LiquidityRole = Literal["maker", "taker"]


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    size: float

    def __post_init__(self) -> None:
        if not 0 < self.price < 1:
            raise ValueError("Binary-contract prices must be between 0 and 1")
        if self.size <= 0:
            raise ValueError("Order-book size must be positive")


@dataclass(frozen=True)
class MarketSnapshot:
    market_id: str
    timestamp: int
    seconds_remaining: float
    prior_up_probability: float
    signal_multiplier: float
    short_term_volatility: float
    up_asks: tuple[OrderBookLevel, ...]
    down_asks: tuple[OrderBookLevel, ...]
    correlation_discount: float = 1.0
    taker_fee_rate: float = 0.07
    data_age_seconds: float = 0.0
    book_skew_seconds: float = 0.0
    source: str = "synthetic"
    question: str = ""
    up_token_id: str = ""
    down_token_id: str = ""

    def __post_init__(self) -> None:
        if not 0 < self.prior_up_probability < 1:
            raise ValueError("prior_up_probability must be between 0 and 1")
        if self.signal_multiplier <= 0:
            raise ValueError("signal_multiplier must be positive")
        if not 0 < self.correlation_discount <= 1:
            raise ValueError("correlation_discount must be in (0, 1]")
        if self.seconds_remaining < 0:
            raise ValueError("seconds_remaining cannot be negative")
        if self.short_term_volatility < 0:
            raise ValueError("short_term_volatility cannot be negative")
        if not 0 <= self.taker_fee_rate <= 1:
            raise ValueError("taker_fee_rate must be between 0 and 1")
        if self.data_age_seconds < 0:
            raise ValueError("data_age_seconds cannot be negative")
        if self.book_skew_seconds < 0:
            raise ValueError("book_skew_seconds cannot be negative")


@dataclass(frozen=True)
class BotConfig:
    starting_cash: float = 10_000.0
    order_size: float = 100.0
    default_taker_fee_rate: float = 0.07
    safety_margin: float = 0.01
    min_edge: float = 0.015
    kelly_fraction: float = 0.20
    inventory_lambda: float = 0.08
    max_capital_per_market: float = 500.0
    max_unhedged_shares: float = 250.0
    daily_loss_limit: float = 250.0
    max_data_age_seconds: float = 5.0
    max_book_skew_seconds: float = 2.0

    def __post_init__(self) -> None:
        positive = {
            "starting_cash": self.starting_cash,
            "order_size": self.order_size,
            "max_capital_per_market": self.max_capital_per_market,
            "max_unhedged_shares": self.max_unhedged_shares,
            "daily_loss_limit": self.daily_loss_limit,
            "max_data_age_seconds": self.max_data_age_seconds,
            "max_book_skew_seconds": self.max_book_skew_seconds,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        for name in (
            "default_taker_fee_rate",
            "safety_margin",
            "min_edge",
            "kelly_fraction",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.inventory_lambda < 0:
            raise ValueError("inventory_lambda cannot be negative")


@dataclass
class Position:
    up_quantity: float = 0.0
    down_quantity: float = 0.0
    up_cost: float = 0.0
    down_cost: float = 0.0
    fees_paid: float = 0.0

    @property
    def matched_quantity(self) -> float:
        return min(self.up_quantity, self.down_quantity)

    @property
    def directional_exposure(self) -> float:
        return self.up_quantity - self.down_quantity

    @property
    def capital_at_risk(self) -> float:
        return self.up_cost + self.down_cost

    def add(
        self,
        side: Side,
        quantity: float,
        average_price: float,
        fee: float = 0.0,
    ) -> None:
        if side == "UP":
            self.up_quantity += quantity
            self.up_cost += quantity * average_price + fee
        else:
            self.down_quantity += quantity
            self.down_cost += quantity * average_price + fee
        self.fees_paid += fee


@dataclass(frozen=True)
class TradeDecision:
    market_id: str
    side: Side
    quantity: float
    average_price: float
    fair_value: float
    usable_edge: float
    reason: str
    fee: float
    liquidity_role: LiquidityRole = "taker"

    @property
    def notional(self) -> float:
        return self.quantity * self.average_price

    @property
    def total_cost(self) -> float:
        return self.notional + self.fee


@dataclass
class PaperAccount:
    cash: float
    realized_pnl: float = 0.0
    total_fees: float = 0.0
    trade_count: int = 0
    positions: dict[str, Position] = field(default_factory=dict)
