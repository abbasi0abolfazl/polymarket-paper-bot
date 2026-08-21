from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .models import LiquidityRole, OrderBookLevel


@dataclass(frozen=True)
class FillQuote:
    average_price: float
    quantity: float
    notional: float
    fee: float

    @property
    def fee_per_share(self) -> float:
        return self.fee / self.quantity

    @property
    def total_cost(self) -> float:
        return self.notional + self.fee

    @property
    def total_cost_per_share(self) -> float:
        return self.total_cost / self.quantity


def polymarket_fee(
    shares: float,
    price: float,
    fee_rate: float,
    liquidity_role: LiquidityRole = "taker",
) -> float:
    """Calculate the documented fee and round it to five USDC decimals."""
    if shares < 0:
        raise ValueError("shares cannot be negative")
    if not 0 < price < 1:
        raise ValueError("price must be between 0 and 1")
    if not 0 <= fee_rate <= 1:
        raise ValueError("fee_rate must be between 0 and 1")
    if liquidity_role not in ("maker", "taker"):
        raise ValueError("liquidity_role must be maker or taker")
    if liquidity_role == "maker" or shares == 0:
        return 0.0
    return round(shares * fee_rate * price * (1 - price), 5)


def quote_fill(
    asks: Iterable[OrderBookLevel],
    requested_quantity: float,
    fee_rate: float,
    liquidity_role: LiquidityRole = "taker",
) -> FillQuote:
    """Quote a depth-aware fill, including nonlinear fees at each price level."""
    if requested_quantity <= 0:
        raise ValueError("requested_quantity must be positive")

    remaining = requested_quantity
    total_cost = 0.0
    raw_fee = 0.0
    filled = 0.0
    for level in sorted(asks, key=lambda item: item.price):
        take = min(remaining, level.size)
        total_cost += take * level.price
        if liquidity_role == "taker":
            raw_fee += take * fee_rate * level.price * (1 - level.price)
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break

    if filled == 0:
        raise ValueError("No liquidity is available")
    return FillQuote(
        average_price=total_cost / filled,
        quantity=filled,
        notional=total_cost,
        fee=round(raw_fee, 5),
    )


def revise_probability(
    previous_probability: float,
    signal_multiplier: float,
    correlation_discount: float = 1.0,
) -> float:
    """Bayesian-odds update with a discount for correlated evidence."""
    if not 0 < previous_probability < 1:
        raise ValueError("previous_probability must be between 0 and 1")
    if signal_multiplier <= 0:
        raise ValueError("signal_multiplier must be positive")
    if not 0 < correlation_discount <= 1:
        raise ValueError("correlation_discount must be in (0, 1]")

    prior_odds = previous_probability / (1 - previous_probability)
    discounted_multiplier = signal_multiplier**correlation_discount
    adjusted_odds = prior_odds * discounted_multiplier
    return adjusted_odds / (1 + adjusted_odds)


def average_fill_price(
    asks: Iterable[OrderBookLevel], requested_quantity: float
) -> tuple[float, float]:
    """Return volume-weighted average price and fillable quantity."""
    quote = quote_fill(asks, requested_quantity, fee_rate=0.0)
    return quote.average_price, quote.quantity


def calculate_trade_edge(
    fair_value: float,
    average_entry: float,
    execution_costs: float,
    safety_margin: float,
) -> float:
    return fair_value - average_entry - execution_costs - safety_margin


def paired_arbitrage_edge(
    up_average_entry: float,
    down_average_entry: float,
    combined_execution_costs: float,
    safety_margin: float,
) -> float:
    """Guaranteed payout minus the combined cost of one UP and one DOWN share."""
    return 1.0 - up_average_entry - down_average_entry - combined_execution_costs - safety_margin


def inventory_penalty(
    position_imbalance: float,
    risk_sensitivity: float,
    volatility: float,
    seconds_remaining: float,
) -> float:
    time_fraction = max(seconds_remaining, 0.0) / 300.0
    return position_imbalance * risk_sensitivity * (volatility**2) * time_fraction


def fractional_kelly(
    win_probability: float,
    entry_price: float,
    fraction: float = 0.20,
) -> float:
    if not 0 < win_probability < 1:
        raise ValueError("win_probability must be between 0 and 1")
    if not 0 < entry_price < 1:
        raise ValueError("entry_price must be between 0 and 1")
    if not 0 <= fraction <= 1:
        raise ValueError("fraction must be between 0 and 1")

    loss_probability = 1 - win_probability
    net_odds = (1 - entry_price) / entry_price
    full_kelly = (net_odds * win_probability - loss_probability) / net_odds
    return max(full_kelly * fraction, 0.0)


def relative_score(current_gap: float, typical_gap: float, gap_volatility: float) -> float:
    if gap_volatility <= 0:
        raise ValueError("gap_volatility must be positive")
    return (current_gap - typical_gap) / gap_volatility
